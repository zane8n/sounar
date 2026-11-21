from fakes import legacy_presets
import numpy as np
import pytest

import sim
from sounar.layouts import loudness_db, plan, room_gains
from sounar.speakers import Output, Setup, Speaker, examples, load_setup
from sounar.state import effective, load_state


def setup_of(name):
    s = load_setup(examples()[name])
    for i, o in enumerate(s.outputs, 1):
        o.node = f"node{i}"
    return s


def nodes(s):
    return {o.node for o in s.outputs}


@pytest.fixture
def ps():
    return legacy_presets()


def eff(ps, st, pid, **params):
    e = effective(st, ps, pid)
    e.params.update(params)
    return e


def band(lo, hi):
    f = sim.freqs()
    return (f >= lo) & (f <= hi)


def resp(p, output_id):
    op = next(o for o in p.outputs if o.output_id == output_id)
    return sim.responses(op.text)


def db_(x):
    return 20 * np.log10(np.maximum(np.abs(x), 1e-12))


def test_stereo_as_wired(tmp_home, ps):
    s = setup_of("stereo-pair")
    st = load_state(ps)
    p = plan(s, eff(ps, st, "tv"), st, nodes(s))
    assert p.layout == "stereo" and len(p.outputs) == 2
    H = resp(p, "front-left")
    assert np.allclose(H[0, 0], 1) and np.allclose(H[1, 0], 0)      # L -> FL
    assert np.allclose(H[1, 1], 1) and np.allclose(H[0, 1], 0)      # R -> FR


def test_symmetric_pair_room_is_left_right(tmp_home, ps):
    s = setup_of("stereo-pair")
    st = load_state(ps)
    p = plan(s, eff(ps, st, "music-room", vocal=0.0), st, nodes(s))
    assert p.layout == "room"
    b = band(1000, 15000)
    for oid, own, other in (("front-left", 0, 1), ("front-right", 1, 0)):
        H = resp(p, oid)
        for ch in (0, 1):                                            # a stereo box: both channels the same
            assert np.max(np.abs(db_(H[own, ch, b]))) < 0.5
            assert np.max(db_(H[other, ch, b])) < -40


def test_lcr_centre_gets_mid(tmp_home, ps):
    s = setup_of("left-centre-right")
    st = load_state(ps)
    p = plan(s, eff(ps, st, "music-room", vocal=0.0), st, nodes(s))
    H = resp(p, "centre")
    b = band(400, 15000)
    assert np.allclose(H[0, 0, b], H[1, 0, b], atol=1e-9)
    assert np.max(np.abs(H[0, 0, b])) > 0.3


def test_surround_gets_only_side_band(tmp_home, ps):
    s = setup_of("quad")
    st = load_state(ps)
    p = plan(s, eff(ps, st, "music-room"), st, nodes(s))
    H = resp(p, "rear-left")
    mono_in = H[0] + H[1]                                            # the same signal on L and R: no side
    assert np.max(db_(mono_in[:, band(1000, 20000)])) < -60   # only the shared bass, rolled off
    side_in = H[0] - H[1]
    assert np.max(db_(side_in[:, band(1000, 8000)])) > -20


def test_surround_pair_is_decorrelated(tmp_home, ps):
    s = setup_of("quad")
    st = load_state(ps)
    H = resp(plan(s, eff(ps, st, "music-room"), st, nodes(s)), "rear-left")
    b = band(2000, 8000)
    assert np.max(np.abs(H[0, 0, b] + H[0, 1, b])) < 1e-3 * np.max(np.abs(H[0, 0, b]))   # rear-left = -(rear-right)


def _hi_peak(H):
    f = sim.freqs()
    ir = np.fft.irfft(H * (f > 2000), sim.N)
    return int(np.argmax(np.abs(ir)))


def test_surround_is_late(tmp_home, ps):
    s = setup_of("pair-plus-rear")
    st = load_state(ps)
    p = plan(s, eff(ps, st, "music-room", haas=15.0), st, nodes(s))
    front = _hi_peak(resp(p, "front-left")[0, 0])
    rear = _hi_peak(resp(p, "rear-centre")[0, 0])
    assert rear - front >= 15 * 48 - 5


def test_sub_only_bass(tmp_home, ps):
    s = setup_of("pair-with-sub")
    st = load_state(ps)
    H = resp(plan(s, eff(ps, st, "music-room"), st, nodes(s)), "sub")
    assert np.max(db_(H[:, :, band(1500, 20000)])) < -40
    assert np.max(db_(H[0, 0, band(30, 60)])) > -10


def test_bass_boost_respects_flag(tmp_home, ps):
    s = setup_of("stereo-box-and-twin-right")
    st = load_state(ps)
    p = plan(s, eff(ps, st, "club"), st, nodes(s))                   # bass +3 dB
    b = band(30, 40)
    kept = np.abs(resp(p, "front-left")[0, 0, b])
    boosted = np.abs(resp(p, "front-right")[0, 0, b])
    assert np.allclose(kept, 0.5, rtol=0.02)
    assert np.allclose(boosted / kept, 10 ** (3 / 20), rtol=0.02)


def test_spread_width_zero_is_mono(tmp_home, ps):
    s = setup_of("stereo-pair")
    st = load_state(ps)
    H = resp(plan(s, eff(ps, st, "party", width=0.0), st, nodes(s)), "front-right")
    assert np.allclose(H, 0.5)


def test_lone_stereo_box_plays_stereo(tmp_home, ps):
    s = Setup([Output("a", [Speaker("centre", ["FL", "FR"], 12)]),
               Output("b", [Speaker("rear-centre", ["FL", "FR"], 6)])])
    st = load_state(ps)
    H = resp(plan(s, eff(ps, st, "music-room", vocal=0.0), st, {"a", "b"}), "centre")
    b = band(1000, 15000)
    assert np.max(np.abs(db_(H[0, 0, b]))) < 0.5 and np.max(db_(H[1, 0, b])) < -40
    assert np.max(np.abs(db_(H[1, 1, b]))) < 0.5 and np.max(db_(H[0, 1, b])) < -40


def test_anchor_matrix_original(tmp_home, ps):
    s = setup_of("stereo-box-and-twin-right")
    e = eff(ps, load_state(ps), "music-room")                        # centre 6
    g = room_gains(s.speakers(), e.params, {})
    c = 10 ** (-6 / 20)
    norm = np.sqrt(2 / (1 + c * c))
    assert g["front-left"].mid == pytest.approx(norm) and g["front-left"].side == pytest.approx(norm * c)
    assert g["front-right"].mid == pytest.approx(norm * c) and g["front-right"].side == pytest.approx(-norm)
    assert g["rear-right"].amb == pytest.approx(norm * 10 ** (-9 / 20))
    assert g["front-left"].bass == 1.0 and g["front-right"].bass == pytest.approx(10 ** (1 / 20))


def test_missing_output_falls_back(tmp_home, ps):
    s = setup_of("stereo-box-and-twin-right")
    st = load_state(ps)
    p = plan(s, eff(ps, st, "music-room"), st, {"node1"})
    assert p.layout == "stereo" and p.wanted == "room" and len(p.outputs) == 1
    assert "missing" in p.note


def test_no_outputs_connected(tmp_home, ps):
    s = setup_of("stereo-pair")
    st = load_state(ps)
    p = plan(s, eff(ps, st, "music-room"), st, set())
    assert p.outputs == [] and p.note == "No speakers connected"


def test_force_layout_stereo(tmp_home, ps):
    s = setup_of("quad")
    st = load_state(ps)
    assert plan(s, eff(ps, st, "music-room"), st, nodes(s), force_layout="stereo").layout == "stereo"


def test_delays_wait_for_farthest(tmp_home, ps):
    s = setup_of("stereo-box-and-twin-right")
    st = load_state(ps)
    st["calibration"]["offsets_ms"] = {"rear-right": 3.0}
    st["sync_ms"] = {"front-left": 0.0, "front-right": 17.9}
    p = plan(s, eff(ps, st, "music-room"), st, nodes(s))
    d = {k: v for o in p.outputs for k, v in o.delays_ms.items()}
    assert d == pytest.approx({"front-left": 3.0, "front-right": 20.9, "rear-right": 17.9})
    ps_ = plan(s, eff(ps, st, "tv"), st, nodes(s))
    d = {k: v for o in ps_.outputs for k, v in o.delays_ms.items()}
    assert d == pytest.approx({"front-left": 0.0, "front-right": 17.9, "rear-right": 17.9})


def test_correction_applied_last(tmp_home, ps):
    s = setup_of("stereo-pair")
    s.outputs[0].correction = [{"type": "highshelf", "freq": 10.0, "gain": -6.0, "q": 0.7}]
    st = load_state(ps)
    H = resp(plan(s, eff(ps, st, "tv"), st, nodes(s)), "front-left")
    assert np.allclose(db_(H[0, 0, band(100, 10000)]), -6, atol=0.1)


def test_loudness_stereo_reference_is_zero(tmp_home, ps):
    s = setup_of("stereo-pair")
    st = load_state(ps)
    assert loudness_db(s, eff(ps, st, "tv"), st, side_gain=1.7) == pytest.approx(0.0)
    assert loudness_db(s, eff(ps, st, "party", width=0.0), st, side_gain=1.7) < 0
