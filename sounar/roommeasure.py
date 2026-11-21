"""`sounar room`: the room measurement (about 30 s, music paused) and the gentle bass correction.
See docs/room.md.

1. The computer's sonar: a sweep from its built-in speaker, recorded on a two-channel mic, gives
   the surfaces around the seat (floor, ceiling, the nearest walls and their side).
2. Each speaker alone: a sweep (after a second of quiet noise that wakes a sleeping amplifier)
   gives the room's echo time, a rough distance from the direct-to-reverberant ratio, and the bass
   response at the seat.
3. Booms every speaker shares at the seat get at most two gentle cuts (half the excess, at most
   4 dB, never a boost), the same on every output. Distances stay estimates: Bluetooth hides each
   speaker's own delay, so timing can't give them; speakers on one output get their exact
   difference from the calibration."""
import math

import numpy as np

from sounar import SounarError
from sounar import room as RM
from sounar.measure import RATE, Session, _gains, _recorder, pink_noise
from sounar.state import Lock, load_state, log, now, save_state

ROOM_SWEEP = (35.0, 16000.0, 3.0)        # each speaker: Hz, Hz, seconds
SONAR_SWEEP = (200.0, 16000.0, 2.5)      # the built-in speaker has no deep bass
SONAR_VOLUME = 0.7
MIN_SNR_DB = 20.0
TYPICAL_ROOM = (3.6, 4.5, 2.6)


def builtin_speaker(ctx):
    """The computer's own speaker, if it has one that isn't part of the setup."""
    used = {o.node for o in ctx.setup.outputs}
    for s in ctx.pw.sinks():
        if s.bus == "built-in" and s.name not in used and s.name.startswith("alsa_output."):
            return s
    return None


def _direct(irs, z, lo_s=0.25, hi_s=1.6):
    """The direct sound in two-channel IRs: (t0, snr_db, mic lag in us)."""
    mono = irs.mean(axis=1)
    lo, hi = z + int(lo_s * RATE), min(len(mono), z + int(hi_s * RATE))
    t0 = RM.onset(mono, lo, hi, rate=RATE)
    env = RM.envelope(RM.bandpass(mono[lo:hi], RATE, 300, 12000))
    k = t0 - lo
    pre = env[max(0, k - int(0.25 * RATE)):max(1, k - int(0.02 * RATE))]
    snr = 20 * math.log10(env[k] / (np.median(pre) + 1e-12)) if len(pre) else 0.0
    w = int(0.0006 * RATE)
    a = RM.bandpass(irs[t0 - w:t0 + w, 0], RATE, 1000, 12000)
    b = RM.bandpass(irs[t0 - w:t0 + w, 1], RATE, 1000, 12000)
    return t0, float(snr), float(RM.gcc_lag_us(a, b, RATE))


def _two(x):
    x = np.asarray(x, dtype=float)
    return np.stack([x, x], axis=1) if x.ndim == 1 else x


def measure(ctx) -> dict:
    from sounar import core
    from sounar.calibrate import _outputs
    outs, feeds, index = _outputs(ctx)
    missing = [o.label for o in ctx.setup.outputs if o not in outs]
    if missing:
        raise SounarError(f"connect every speaker before measuring the room (missing: {', '.join(missing)})")
    rec = _recorder(ctx)
    lap = builtin_speaker(ctx)
    ctx.tell("Measuring the room…", "About 30 seconds of sweeps, music paused. Stay quiet and seated")
    sonar, irs = None, {}
    with Session(ctx, "room"):
        if lap:
            ctx.say("sonar (the computer's own speaker)…")
            was = lap.volume_raw
            ctx.pw.set_volume(lap.name, int(65536 * SONAR_VOLUME))
            try:
                sw, inv = RM.sweep(*SONAR_SWEEP, RATE)
                x = rec.play_record(sw * 0.5, sw * 0.5, [(0, None)], feeds, target=lap.name, channels=2)
                sonar = (RM.impulse_response(_two(x), inv), len(inv) - 1)
            finally:
                ctx.pw.set_volume(lap.name, was)
        sw, inv = RM.sweep(*ROOM_SWEEP, RATE)
        prime, gap = pink_noise(1.0, rms=0.004), np.zeros(int(0.4 * RATE))
        track = np.concatenate([prime, gap, sw * 0.5])
        lead = len(prime) + len(gap)
        for o in outs:
            for s in o.speakers:
                ctx.say(f"{s.label}…")
                lg, rg = _gains(s)
                x = _two(rec.play_record(track * lg, track * rg, [(0, index[o.id])], feeds, channels=2))
                irs[s.id] = (RM.impulse_response(x[lead:], inv), len(inv) - 1)
    st = load_state(ctx.presets)
    res = analyse(ctx.setup, st, irs, sonar)
    with Lock():
        st = load_state(ctx.presets)
        prev = st.get("room") or {}
        if (prev.get("box") or {}).get("user"):      # walls placed by hand beat the sonar's guesses
            res["box"] = dict(prev["box"])
        res["correction"] = prev.get("correction", True)
        st["room"] = res
        save_state(st)
    core.apply(ctx)
    n = len(res["cuts"])
    log(f"room measure: RT60 {res['rt60']} s, box {res['box']['width']}x{res['box']['length']} m, "
        f"distances {({k: v['dist_est'] for k, v in res['speakers'].items()})}, {n} cuts")
    ctx.say(room_text(ctx.setup, st))
    ctx.tell("Room measured", f"Echo {res['rt60']:.2f} s · {n} bass cut{'s' if n != 1 else ''}")
    return res


def analyse(setup, st, irs, sonar) -> dict:
    """Recordings -> the room record (pure; tested with simulated rooms)."""
    spk, rts, votes, curves = {}, [], [], []
    for s in setup.speakers():
        if s.id not in irs:
            continue
        ir, z = irs[s.id]
        t0, snr, lag = _direct(ir, z)
        mono = ir.mean(axis=1)
        bands = RM.rt60(mono, t0, RATE)
        heard = snr >= MIN_SNR_DB
        # per mic, then averaged: a speaker to one side reaches the two mics ~0.1 ms apart, and
        # their sum would blur its direct sound (it read 1.5x too far)
        drr = float(np.mean([RM.drr_db(ir[:, c], t0, RATE) for c in range(ir.shape[1])]))
        spk[s.id] = {"snr_db": round(snr, 1), "lag_us": round(lag, 1), "heard": heard, "drr_db": round(drr, 1),
                     "t0": t0}
        if heard:
            if RM.rt60_mid(bands):
                rts.append(RM.rt60_mid(bands))
            curves.append(RM.bass_response(mono, t0, RATE))
            if s.at is not None:
                side = math.sin(math.radians(s.at * 30))
                if abs(side) > 0.3 and abs(lag) > 15:
                    votes.append(math.copysign(1, lag) * math.copysign(1, side))
    right_sign = 1.0 if sum(votes) >= 0 else -1.0
    surf, refl, sonar_ok = {}, [], False
    if sonar:
        ir, z = sonar
        t0, snr, _ = _direct(ir, z, 0.0, 1.0)
        if snr >= MIN_SNR_DB:
            sonar_ok = True
            refl = RM.reflections(ir, t0, RATE)
            surf = RM.surfaces(refl, right_sign=right_sign)
            mid = RM.rt60_mid(RM.rt60(ir.mean(axis=1), t0, RATE))
            if mid:
                rts.append(mid)
    box = RM.room_box(surf, TYPICAL_ROOM)
    rt60 = float(np.median(rts)) if rts else 0.5
    k = box.get("known") or {}
    keys = ("width", "length", "height")
    dims = [box[key] if k.get(key) else typ for key, typ in zip(keys, TYPICAL_ROOM, strict=True)]
    volume = dims[0] * dims[1] * dims[2]
    for v in spk.values():
        v["dist_est"] = round(RM.distance_from_drr(v["drr_db"], volume, rt60), 2) if v["heard"] else None
    offsets = st["calibration"].get("offsets_ms") or {}
    for o in setup.outputs:
        group = [s for s in o.speakers if s.id in spk and spk[s.id]["heard"]]
        if len(group) > 1 and all(s.id in offsets for s in group):
            # one link for all of them: the calibrated arrival differences are distance differences
            ref = offsets[group[0].id]
            extras = [RM.C * (offsets[s.id] - ref) / 1000 for s in group]
            fitted = RM.group_distances([spk[s.id]["dist_est"] for s in group], extras)
            for s, d in zip(group, fitted, strict=True):
                spk[s.id]["dist_est"] = round(d, 2)
    for v in spk.values():
        v.pop("t0", None)
    return {"ts": now(), "rt60": round(rt60, 2), "volume_m3": round(volume, 1), "box": box, "surfaces": surf,
            "reflections": refl, "sonar_ok": sonar_ok, "right_sign": right_sign, "speakers": spk,
            "cuts": RM.shared_bass_cuts(curves) if curves else []}


def set_correction(ctx, on: bool) -> None:
    from sounar import core
    with Lock():
        st = load_state(ctx.presets)
        if not st.get("room"):
            raise SounarError("measure the room first: `sounar room measure`")
        st["room"]["correction"] = on
        save_state(st)
    core.apply(ctx)
    log(f"room correction {'on' if on else 'off'}")
    ctx.say(f"room correction {'on' if on else 'off'}")
    ctx.tell("Room correction", "on" if on else "off: the speakers' own correction only")


def set_walls(ctx, left, right, ahead, behind) -> None:
    """The walls by hand: metres from the seat to the left, right, ahead and behind."""
    vals = [round(min(max(float(v), 0.2), 15.0), 2) for v in (left, right, ahead, behind)]
    left, right, ahead, behind = vals
    with Lock():
        st = load_state(ctx.presets)
        r = st.get("room")
        if not r:
            raise SounarError("measure the room first: `sounar room measure`")
        b = r["box"]
        seat = dict(b.get("laptop") or {})
        seat.update({"x": left, "y": ahead})
        r["box"] = {**b, "width": round(left + right, 2), "length": round(ahead + behind, 2), "laptop": seat,
                    "known": {**(b.get("known") or {}), "width": True, "length": True}, "user": True}
        save_state(st)
    log(f"room walls (by hand): left {left}, right {right}, ahead {ahead}, behind {behind} m")
    ctx.say(f"room: {left + right:.2f} x {ahead + behind:.2f} m, the seat {left:g} m from the left wall "
            f"and {ahead:g} m from the wall ahead")


def info(st):
    """For `sounar status --json`."""
    r = st.get("room")
    if not r:
        return None
    return {"ts": r["ts"], "rt60": r["rt60"], "box": r["box"], "correction": r.get("correction", True),
            "cuts": r.get("cuts", []), "sonar_ok": r.get("sonar_ok", False),
            "walls_by_user": bool(r["box"].get("user")),
            "speakers": {k: {"dist_est": v.get("dist_est"), "heard": v.get("heard")}
                         for k, v in (r.get("speakers") or {}).items()}}


def room_text(setup, st) -> str:
    r = st.get("room")
    if not r:
        return "room: not measured yet (`sounar room measure`, about 30 s of sweeps)"
    b = r["box"]
    lines = [f"room (measured {r['ts'][:16].replace('T', ' ')}): echo {r['rt60']:.2f} s, about "
             f"{b['width']:.1f} x {b['length']:.1f} x {b['height']:.1f} m"
             + (" (walls placed by you)" if b.get("user") else ""
                if b.get("known") else " (size not heard: a typical room)")]
    s = r.get("surfaces") or {}
    if r.get("sonar_ok"):
        bits = [f"{k} {s[k]:.2f} m" for k in ("floor", "ceiling", "left", "right") if s.get(k)]
        bits += [f"ahead/behind {d:.2f} m" for d in s.get("front_back", [])]
        lines.append("  around the seat: " + (", ".join(bits) or "no clear reflections"))
    for sp in setup.speakers():
        x = (r.get("speakers") or {}).get(sp.id) or {}
        lines.append(f"  {sp.label:<14} "
                     + (f"~{x['dist_est']:.1f} m (rough)" if x.get("heard") else "not heard clearly"))
    cuts = ", ".join(f"{c['freq']:.0f} Hz {c['gain']:+.1f} dB" for c in r.get("cuts", [])) or "none needed"
    lines.append(f"  bass cuts: {cuts}; room correction {'on' if r.get('correction', True) else 'off'}")
    return "\n".join(lines)
