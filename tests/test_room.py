"""The room analysis (sounar/room.py) on simulated rooms: no sound is played."""
import numpy as np
import pytest

from sounar import room as R

RATE = 48000


def imp(n, t_s, amp):
    """A band-limited impulse at t_s seconds (windowed sinc)."""
    x = np.zeros(n)
    c = t_s * RATE
    k = np.arange(int(c) - 32, int(c) + 33)
    k = k[(k >= 0) & (k < n)]
    x[k] += amp * np.sinc(k - c) * (0.5 + 0.5 * np.cos(np.pi * (k - c) / 33))
    return x


def tail(n, start_s, rt60, energy, seed, band=None):
    """Diffuse reverb: noise decaying 60 dB in rt60, total energy `energy`."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / RATE
    x = rng.standard_normal(n) * np.where(t >= start_s, np.exp(-6.91 * (t - start_s) / rt60), 0)
    if band:
        x = R.bandpass(x, RATE, *band, edge_oct=0.01)
    return x * np.sqrt(energy / np.sum(x ** 2))


W, L, H = 3.8, 5.0, 2.6                  # room
LX, LY, LZ = 1.1, 1.6, 0.75              # the computer: 1.1 m from the left wall, 1.6 m from the wall ahead
RT = 0.45


def sonar_ir(mic_x, n):
    """Built-in speaker -> one mic: direct, the six first-order walls, and diffuse reverb that is a
    different wave at each mic."""
    src, mic = np.array([LX, LY - 0.08, LZ]), np.array([LX + mic_x, LY, LZ + 0.02])
    images = [(-src[0], src[1], src[2]), (2 * W - src[0], src[1], src[2]), (src[0], -src[1], src[2]),
              (src[0], 2 * L - src[1], src[2]), (src[0], src[1], -src[2]), (src[0], src[1], 2 * H - src[2])]
    d0 = np.linalg.norm(mic - src)
    x = imp(n, 0.01 + d0 / R.C, 1 / d0)
    for im in images:
        d = np.linalg.norm(mic - np.array(im))
        x += imp(n, 0.01 + d / R.C, 0.7 / d)
    x += tail(n, 0.022, RT, (1 / d0) ** 2 * (d0 / R.critical_distance(W * L * H, RT)) ** 2,
              seed=2 if mic_x < 0 else 3)
    return x


@pytest.fixture(scope="module")
def sweep():
    return R.sweep(40, 18000, 2.0, RATE)


@pytest.fixture(scope="module")
def sonar(sweep):
    sw, inv = sweep
    n = int(RATE * 1.5)
    ir2 = np.stack([sonar_ir(-0.03, n), sonar_ir(0.03, n)], axis=1)      # mic 1 on the left
    rec = np.stack([R.fftconv(sw, ir2[:, 0]), R.fftconv(sw, ir2[:, 1])], axis=1)
    rec += np.random.default_rng(3).standard_normal(rec.shape) * 1e-4
    irs = R.impulse_response(rec, inv)
    z = len(inv) - 1
    t0 = R.onset(irs[:, 0], z, z + int(0.05 * RATE))
    return irs, t0


def test_sweep_deconvolves_to_a_clean_impulse(sweep):
    sw, inv = sweep
    rec = np.concatenate([np.zeros(1000), sw, np.zeros(RATE // 2)])
    ir = R.impulse_response(rec, inv)[:, 0]
    pk = int(np.argmax(np.abs(ir)))
    assert pk == len(inv) - 1 + 1000 and abs(ir[pk] - 1) < 0.05
    assert np.max(np.abs(np.delete(ir, range(pk - 200, pk + 200)))) < 0.01


def test_two_mic_lag():
    a = R.bandpass(np.random.default_rng(1).standard_normal(4000), RATE, 1000, 12000)
    assert R.gcc_lag_us(a, np.roll(a, 7), RATE) == pytest.approx(145.8, abs=6)


def test_sonar_finds_the_nearest_surfaces(sonar):
    irs, t0 = sonar
    refl = R.reflections(irs, t0, RATE)
    # mic 1 is on the left here, so a sound from the right reaches mic 2 first: right = negative lag
    s = R.surfaces(refl, right_sign=-1.0)
    assert s["floor"] == pytest.approx(LZ, abs=0.12)
    assert s["left"] == pytest.approx(LX, abs=0.15)                    # the nearest side wall, on its side
    assert s["right"] is None or s["right"] == pytest.approx(W - LX, abs=0.25)
    assert s["front_back"] and s["front_back"][0] == pytest.approx(LY, abs=0.2)
    assert s["ceiling"] == pytest.approx(H - LZ, abs=0.2)
    # reverb bumps differ between the mics, so they aren't taken for walls
    walls = (LZ, LX, W - LX, LY, L - LY, H - LZ)
    assert len([r for r in refl if not any(abs(r["dist_m"] - d) < 0.15 for d in walls)]) <= 2
    assert R.room_box(s)["laptop"]["x"] == pytest.approx(LX, abs=0.15)


def test_echo_time(sonar):
    irs, t0 = sonar
    assert R.rt60_mid(R.rt60(irs[:, 0], t0, RATE)) == pytest.approx(RT, rel=0.15)


@pytest.mark.parametrize("r", [0.8, 1.4, 2.4])
def test_distance_from_the_direct_to_reverberant_ratio(r):
    V = W * L * H
    rc = R.critical_distance(V, RT)
    nb = int(RATE * 0.8)
    x = imp(nb, 0.2, 1 / r)
    scale = np.sum(R.bandpass(x, RATE, 500, 4000) ** 2) * r ** 2
    tl = R.bandpass(tail(nb, 0.204, RT, 1.0, seed=int(r * 10)), RATE, 500, 4000, edge_oct=0.01)
    x += tl * np.sqrt((1 / rc ** 2) * scale / np.sum(tl ** 2))
    t0 = R.onset(x, 0, nb)
    assert R.distance_from_drr(R.drr_db(x, t0, RATE), V, RT) == pytest.approx(r, rel=0.35)


def test_speakers_on_one_output_keep_their_exact_difference():
    near, far = R.pair_distances(1.1, 2.4, extra_m=0.69)
    assert far - near == pytest.approx(0.69, abs=0.01) and 1.1 <= near <= 2.4 - 0.69 + 0.01
    a, b, c = R.group_distances([1.0, 1.5, 2.2], [0.0, 0.5, 1.2])
    assert b - a == pytest.approx(0.5, abs=0.01) and c - a == pytest.approx(1.2, abs=0.01)


GRID = 30 * 2 ** (np.arange(int(np.log2(350 / 30) * 48) + 1) / 48)
FLAT = -3 * np.log2(GRID / 30) / 4


def peak(f, db, q):
    return db / (1 + ((GRID - f) / (f / q / 2)) ** 2)


def test_shared_cuts_only_for_booms_every_speaker_has():
    curves = [(GRID, FLAT + peak(63, 7, 6) + peak(f, 7, 6) + peak(85, -12, 8)) for f in (90, 140, 200)]
    cuts = R.shared_bass_cuts(curves)
    assert len(cuts) == 1 and cuts[0]["freq"] == pytest.approx(63, abs=4) and -4.0 <= cuts[0]["gain"] < 0
    assert cuts[0]["excess"] < 8                         # the deep dip doesn't inflate the peak


def test_shared_cuts_are_few_gentle_and_never_boost():
    many = FLAT + sum(peak(f, 8, 8) for f in (45, 70, 95, 130, 190)) + peak(150, -9, 4)
    cuts = R.shared_bass_cuts([(GRID, many)] * 3)
    assert len(cuts) <= 2 and all(-4.0 <= c["gain"] < 0 for c in cuts)
    assert R.shared_bass_cuts([(GRID, FLAT)] * 3) == []
