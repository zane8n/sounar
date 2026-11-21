"""Room measurement, the analysis half: pure maths on recordings, tested on simulated rooms
(tests/test_room.py). sounar/roommeasure.py plays the sweeps and records; this turns the
recordings into:
  - impulse responses (exponential sweep, Farina deconvolution), per mic channel
  - echo: RT60 per octave band (Schroeder backward integration, T20)
  - the computer's own "sonar": reflections of a sweep from its built-in speaker, as distances
    to the surfaces around it, each with a side (left / right / centre) from a two-channel mic
  - per speaker: direct-to-reverberant ratio -> rough distance (critical distance from the
    room volume and RT60; Bluetooth hides each speaker's own delay, so timing can't give it)
  - per speaker: the bass response at the seat; shared booms get a few gentle cuts
"""
import numpy as np

C = 343.0                     # speed of sound, m/s


# ---------------------------------------------------------------- sweeps and impulse responses

def sweep(f0, f1, secs, rate, fade=0.05):
    """Exponential sine sweep and its inverse filter (Farina). conv(sweep, inverse) ~ impulse at
    len(sweep) - 1, peak 1."""
    n = int(secs * rate)
    t = np.arange(n) / rate
    r = np.log(f1 / f0)
    x = np.sin(2 * np.pi * f0 * secs / r * (np.exp(t * r / secs) - 1))
    nf = int(fade * rate)
    w = np.ones(n)
    w[:nf] = 0.5 - 0.5 * np.cos(np.pi * np.arange(nf) / nf)
    w[-nf:] = w[:nf][::-1]
    x *= w
    inv = x[::-1] * np.exp(-t * r / secs)
    peak = np.max(np.abs(fftconv(x, inv)))
    return x.astype(np.float64), inv / peak


def fftconv(a, b):
    n = len(a) + len(b) - 1
    m = 1 << int(np.ceil(np.log2(n)))
    return np.fft.irfft(np.fft.rfft(a, m) * np.fft.rfft(b, m), m)[:n]


def impulse_response(rec, inv):
    """rec: (samples,) or (samples, channels), recorded while the sweep played. Returns the IRs
    (samples, channels); time 0 of the recording sits at index len(inv) - 1."""
    rec = rec.reshape(len(rec), -1)
    return np.stack([fftconv(rec[:, c], inv) for c in range(rec.shape[1])], axis=1)


def envelope(x):
    """Magnitude of the analytic signal."""
    n = len(x)
    X = np.fft.fft(x)
    h = np.zeros(n)
    h[0] = 1
    if n % 2 == 0:
        h[n // 2] = 1
        h[1:n // 2] = 2
    else:
        h[1:(n + 1) // 2] = 2
    return np.abs(np.fft.ifft(X * h))


def onset(ir, lo, hi, rel_db=-12.0, band=(500.0, 12000.0), rate=48000):
    """First sample in [lo, hi) where the envelope reaches rel_db below that window's maximum:
    the direct sound (the first arrival, not necessarily the loudest). Found in the 500 Hz-12 kHz
    band: deep bass smears in time and crossed the threshold ~1 ms early."""
    env = envelope(bandpass(ir[lo:hi], rate, *band) if band else ir[lo:hi])
    thr = env.max() * 10 ** (rel_db / 20)
    i = int(np.argmax(env >= thr))
    j = i + int(np.argmax(env[i:i + 64]))     # its peak, a few samples later
    return lo + j


def bandpass(x, rate, lo, hi, edge_oct=0.5):
    """Zero-phase band-pass with half-cosine edges edge_oct wide (outside lo and hi). A brick
    wall would make a strong direct sound ring through the whole window (and wrap around)."""
    n = len(x)
    m = 1 << int(np.ceil(np.log2(2 * n)))
    X = np.fft.rfft(x, m)
    f = np.fft.rfftfreq(m, 1 / rate)
    w = np.ones(len(f))
    with np.errstate(divide="ignore"):
        below = np.log2(lo / np.maximum(f, 1e-9))
        above = np.log2(np.maximum(f, 1e-9) / hi)
    for d in (below, above):
        ramp = (d > 0) & (d < edge_oct)
        w[ramp] *= 0.5 + 0.5 * np.cos(np.pi * d[ramp] / edge_oct)
        w[d >= edge_oct] = 0
    return np.fft.irfft(X * w, m)[:n]


# ---------------------------------------------------------------- echo (RT60)

def rt60(ir, t0, rate, bands=(250, 500, 1000, 2000, 4000), length_s=1.2):
    """T20 per octave band: Schroeder curve from t0, fit -5..-25 dB, x3. Returns {band: s}
    (None where the decay isn't clean enough)."""
    out = {}
    seg = ir[t0:t0 + int(length_s * rate)]
    for fc in bands:
        y = bandpass(seg, rate, fc / np.sqrt(2), fc * np.sqrt(2))
        e = y ** 2
        # noise floor: the last 15 % of the segment; integrate only above it
        noise = np.mean(e[-len(e) // 7:])
        e = np.maximum(e - noise, 0)
        sch = np.cumsum(e[::-1])[::-1]
        if sch[0] <= 0:
            out[fc] = None
            continue
        db = 10 * np.log10(sch / sch[0] + 1e-30)
        i5, i25 = np.argmax(db <= -5), np.argmax(db <= -25)
        if i25 <= i5 or db[i25] > -24:
            out[fc] = None
            continue
        t = np.arange(i5, i25) / rate
        slope = np.polyfit(t, db[i5:i25], 1)[0]
        out[fc] = round(float(-60 / slope), 3) if slope < 0 else None
    return out


def rt60_mid(bands):
    v = [bands.get(f) for f in (500, 1000) if bands.get(f)]
    return float(np.mean(v)) if v else None


# ---------------------------------------------------------------- the laptop's sonar

def gcc_lag_us(a, b, rate, max_us=600, up=8):
    """Delay of b against a (microseconds, + = b later), GCC-PHAT, interpolated."""
    n = 1 << int(np.ceil(np.log2(2 * len(a))))
    A, B = np.fft.rfft(a, n), np.fft.rfft(b, n)
    G = np.conj(A) * B
    G /= np.abs(G) + 1e-12
    cc = np.fft.irfft(G, n * up)
    m = int(max_us * 1e-6 * rate * up)
    cc = np.concatenate([cc[-m:], cc[:m + 1]])
    return (int(np.argmax(cc)) - m) / (rate * up) * 1e6


def coherence(ir2, k, rate, half_ms=0.6, max_us=600):
    """Peak normalised cross-correlation of the two mics around sample k (1-12 kHz)."""
    w = int(half_ms * 1e-3 * rate)
    a = bandpass(ir2[k - w:k + w, 0], rate, 1000, 12000)
    b = bandpass(ir2[k - w:k + w, 1], rate, 1000, 12000)
    den = np.sqrt(np.sum(a * a) * np.sum(b * b))
    if den <= 0:
        return 0.0
    m = int(max_us * 1e-6 * rate)
    cc = np.correlate(a, b, mode="full")
    mid = len(cc) // 2
    return float(np.max(np.abs(cc[mid - m:mid + m + 1])) / den)


def reflections(ir2, t0, rate, min_ms=1.2, max_ms=42.0, gap_ms=0.7, floor_db=-50.0, above_db=6.0,
                src_mic_m=0.10, n=8, min_coherence=0.6):
    """Reflections after the direct sound of the laptop speaker: [{ms, dist_m, db, lag_us}].
    dist_m = distance from the laptop to the reflecting surface (speaker and mics a few cm apart:
    path = 2 d - src_mic). lag_us = mic 2 against mic 1 (its sign says which side)."""
    mono = ir2.mean(axis=1)
    env = envelope(bandpass(mono, rate, 700, 12000))
    ref = env[max(0, t0 - 24):t0 + 48].max()
    lo, hi = t0 + int(min_ms * 1e-3 * rate), t0 + int(max_ms * 1e-3 * rate)
    seg = env[lo:hi]
    # a reflection stands above the local background (reverb, other reflections): median of +-2 ms
    half = int(0.002 * rate)
    bg = np.array([np.median(env[max(lo + i - half, 0):lo + i + half]) for i in range(len(seg))])
    gap = int(gap_ms * 1e-3 * rate)
    picks = []
    order = np.argsort(seg)[::-1]
    for i in order:
        if seg[i] < ref * 10 ** (floor_db / 20):
            break
        if seg[i] < bg[i] * 10 ** (above_db / 20) or any(abs(i - j) < gap for j in picks):
            continue
        # a local maximum, not the shoulder of a bigger peak
        a, b = max(0, i - 3), min(len(seg), i + 4)
        if seg[i] < seg[a:b].max():
            continue
        # a reflection reaches both mics as the same wave (a few us apart); a bump in the diffuse
        # reverb is a different wave at each mic, so it is dropped here
        if coherence(ir2, lo + i, rate) < min_coherence:
            continue
        picks.append(i)
        if len(picks) >= n:
            break
    out = []
    w = int(0.6e-3 * rate)
    for i in sorted(picks):
        k = lo + i
        ms = (k - t0) / rate * 1e3
        d = (C * ms * 1e-3 + src_mic_m) / 2
        a = bandpass(ir2[k - w:k + w, 0], rate, 1000, 12000)
        b = bandpass(ir2[k - w:k + w, 1], rate, 1000, 12000)
        out.append({"ms": round(ms, 2), "dist_m": round(d, 2),
                    "db": round(20 * np.log10(seg[i] / ref + 1e-12), 1),
                    "lag_us": round(gcc_lag_us(a, b, rate), 1)})
    return out


def surfaces(refl, side_us=40.0, right_sign=1.0):
    """Turn reflections into the surfaces around the laptop (a rough guess for the map):
    floor (strong, below ~1.1 m, centred), ceiling (centred, 1.2-2.8 m), the nearest wall on each
    side (|lag| beyond side_us; right_sign: which lag sign is the right, from the speakers) and
    the nearest centred walls (front or back: two mics side by side can't tell)."""
    side = lambda r: (0 if abs(r["lag_us"]) < side_us else (1 if r["lag_us"] * right_sign > 0 else -1))
    s = {"floor": None, "ceiling": None, "left": None, "right": None, "front_back": []}
    cen = [r for r in refl if side(r) == 0]
    low = [r for r in cen if 0.25 <= r["dist_m"] <= 1.1]
    if low:
        s["floor"] = max(low, key=lambda r: r["db"])["dist_m"]
    for r in sorted(refl, key=lambda r: r["dist_m"]):
        k = side(r)
        if k < 0 and s["left"] is None and r["dist_m"] >= 0.3:
            s["left"] = r["dist_m"]
        if k > 0 and s["right"] is None and r["dist_m"] >= 0.3:
            s["right"] = r["dist_m"]
    rest = [r for r in cen if r["dist_m"] > 1.1]
    up = [r for r in rest if r["dist_m"] <= 2.8]
    if up and s["floor"] is not None:
        s["ceiling"] = min(up, key=lambda r: abs(r["dist_m"] + s["floor"] - 2.6))["dist_m"]
    s["front_back"] = sorted({r["dist_m"] for r in rest if r["dist_m"] != s["ceiling"]})[:2]
    return s


def room_box(s, default=(3.6, 4.5, 2.6)):
    """Width x length x height (m) and where the laptop is in it, from the surfaces; whatever
    wasn't heard comes from a typical room. The front/back split puts the nearer centred wall
    ahead; the map lets the user flip and resize it."""
    W, L, H = default
    known = {}
    if s.get("left") and s.get("right"):
        W = s["left"] + s["right"]
        known["width"] = True
    fb = s.get("front_back") or []
    if len(fb) >= 2:
        L = fb[0] + fb[1]
        known["length"] = True
    if s.get("floor") and s.get("ceiling"):
        H = s["floor"] + s["ceiling"]
        known["height"] = True
    x = s["left"] if s.get("left") else (W - s["right"] if s.get("right") else W / 2)
    y = fb[0] if fb else L / 2                      # distance to the wall ahead
    x, y = min(max(x, 0.2), W - 0.2), min(max(y, 0.2), L - 0.2)
    return {"width": round(W, 2), "length": round(L, 2), "height": round(H, 2),
            "laptop": {"x": round(x, 2), "y": round(y, 2), "height": s.get("floor")},
            "known": known}


# ---------------------------------------------------------------- distance of a Bluetooth unit

def drr_db(ir, t0, rate, direct_ms=2.5, tail_s=0.5, lo=500, hi=4000):
    """Direct-to-reverberant energy ratio in the 500 Hz-4 kHz band."""
    y = bandpass(ir[max(0, t0 - int(0.003 * rate)):t0 + int(tail_s * rate)], rate, lo, hi)
    k = int(0.003 * rate)
    d = int(direct_ms * 1e-3 * rate)
    e_dir = np.sum(y[k - int(0.0005 * rate):k + d] ** 2)
    e_rev = np.sum(y[k + d:] ** 2)
    return float(10 * np.log10((e_dir + 1e-30) / (e_rev + 1e-30)))


def critical_distance(volume, rt60, q=2.0):
    return 0.057 * np.sqrt(q * volume / max(rt60, 0.1))


def distance_from_drr(drr, volume, rt60, q=2.0, limits=(0.5, 4.0)):
    d = critical_distance(volume, rt60, q) / np.sqrt(10 ** (drr / 10))
    return float(min(max(d, limits[0]), limits[1]))


def pair_distances(est_near, est_far, extra_m, limits=(0.5, 4.0)):
    """Two speakers on one output (a twin's front and rear): their arrival difference gives
    exactly how much farther the far one is (extra_m); fit the near one so both rough estimates
    agree best (in log distance)."""
    best, arg = None, est_near
    for d in np.linspace(limits[0], limits[1], 701):
        far = d + extra_m
        if far <= 0:
            continue
        cost = np.log(d / est_near) ** 2 + np.log(far / est_far) ** 2
        if best is None or cost < best:
            best, arg = cost, d
    return float(arg), float(min(max(arg + extra_m, limits[0]), limits[1]))


def group_distances(ests, extras, limits=(0.5, 4.0)):
    """Speakers on one output: extras[k] = how much farther speaker k is than the first (exact,
    from the calibrated arrival differences); ests = their rough estimates. Fits the first one so
    all agree best in log distance and returns every speaker's distance."""
    best, arg = None, ests[0]
    for d in np.linspace(limits[0], limits[1], 701):
        ds = [d + e for e in extras]
        if min(ds) <= 0:
            continue
        cost = sum(np.log(x / e) ** 2 for x, e in zip(ds, ests, strict=True))
        if best is None or cost < best:
            best, arg = cost, d
    return [float(min(max(arg + e, limits[0]), limits[1])) for e in extras]


# ---------------------------------------------------------------- bass and gentle cuts

def bass_response(ir, t0, rate, f_lo=30.0, f_hi=350.0, window_s=0.35, per_oct=48, smooth_oct=1 / 12):
    """Magnitude (dB) at the seat from t0, on a log grid, smoothed to smooth_oct."""
    n = int(window_s * rate)
    y = ir[max(0, t0 - int(0.005 * rate)):][:n]
    w = np.ones(len(y))
    tail = len(y) // 4
    w[-tail:] = 0.5 + 0.5 * np.cos(np.pi * np.arange(tail) / tail)
    Y = np.abs(np.fft.rfft(y * w, 1 << 17)) ** 2
    f = np.fft.rfftfreq(1 << 17, 1 / rate)
    grid = f_lo * 2 ** (np.arange(int(np.log2(f_hi / f_lo) * per_oct) + 1) / per_oct)
    mag = np.empty(len(grid))
    for i, g in enumerate(grid):
        a, b = g * 2 ** (-smooth_oct / 2), g * 2 ** (smooth_oct / 2)
        sel = (f >= a) & (f <= b)
        mag[i] = 10 * np.log10(Y[sel].mean() + 1e-30)
    return grid, mag


def smooth_log(grid, mag, oct):
    out = np.empty_like(mag)
    lg = np.log2(grid)
    for i in range(len(grid)):
        sel = np.abs(lg - lg[i]) <= oct / 2
        out[i] = mag[sel].mean()
    return out


def median_log(grid, mag, oct):
    """Running median over `oct` octaves: a trend that deep dips (seat cancellations) don't pull
    down, so peaks aren't exaggerated."""
    out = np.empty_like(mag)
    lg = np.log2(grid)
    for i in range(len(grid)):
        out[i] = np.median(mag[np.abs(lg - lg[i]) <= oct / 2])
    return out


def shared_bass_cuts(curves, max_cuts=2, min_excess=3.0, max_cut=4.0, f_min=40.0, f_max=300.0, share=0.5):
    """Gentle room correction: correct, but don't overdo it. All speakers play the same bass in
    the room layout, so only booms they share at the seat are cut, the same on every speaker:
    the speakers' excess over a median trend (dips don't count) is averaged; peaks of that average
    at least min_excess dB get a cut of share x excess, at most max_cut dB, at most max_cuts.
    curves = [(grid, mag_db)] on the same grid. Never boosts."""
    grid = curves[0][0]
    ex = np.mean([m - median_log(grid, m, 1.0) for _, m in curves], axis=0)
    cuts = []
    for i in range(3, len(grid) - 3):
        if grid[i] < f_min or grid[i] > f_max:
            continue
        if ex[i] >= min_excess and ex[i] == ex[max(0, i - 6):i + 7].max():
            half = ex[i] / 2
            a = i
            while a > 0 and ex[a] > half:
                a -= 1
            b = i
            while b < len(ex) - 1 and ex[b] > half:
                b += 1
            bw = max(np.log2(grid[b] / grid[a]), 1 / 12)
            cuts.append({"freq": round(float(grid[i]), 1), "gain": round(-min(share * ex[i], max_cut), 1),
                         "q": round(float(min(max(1.0 / bw, 1.0), 6.0)), 2), "excess": round(float(ex[i]), 1)})
    cuts.sort(key=lambda c: c["excess"], reverse=True)
    return sorted(cuts[:max_cuts], key=lambda c: c["freq"])


def bass_cuts(grid, mag, max_cuts=3, min_excess=3.0, max_cut=6.0, f_max=300.0):
    """Cuts for the room's booms only: peaks standing min_excess dB or more above the
    one-octave trend, below f_max, at most max_cuts, each at most max_cut dB (never a boost).
    The cut is the excess minus 1 dB (leave a little, the ear forgives a small peak)."""
    trend = smooth_log(grid, mag, 1.0)
    ex = mag - trend
    cuts = []
    for i in range(2, len(grid) - 2):
        if grid[i] > f_max:
            break
        if ex[i] >= min_excess and ex[i] == ex[max(0, i - 6):i + 7].max():
            # width: where the excess falls to half (in dB) on both sides
            half = ex[i] / 2
            a = i
            while a > 0 and ex[a] > half:
                a -= 1
            b = i
            while b < len(ex) - 1 and ex[b] > half:
                b += 1
            bw_oct = max(np.log2(grid[b] / grid[a]), 1 / 12)
            q = float(min(max(1.0 / bw_oct, 1.0), 8.0))
            cuts.append({"freq": round(float(grid[i]), 1), "gain": round(-min(ex[i] - 1.0, max_cut), 1),
                         "q": round(q, 2), "excess": round(float(ex[i]), 1)})
    cuts.sort(key=lambda c: c["excess"], reverse=True)
    return sorted(cuts[:max_cuts], key=lambda c: c["freq"])
