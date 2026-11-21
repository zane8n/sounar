"""`sounar room measure` end to end, with playback and recording replaced by a simulated room."""
import math

import numpy as np
import pytest

from fakes import FakeEffects, FakePlayers, bt_names, original_room_system
from test_room import RT, imp, sonar_ir, tail
from sounar import core, roommeasure
from sounar import room as R
from sounar.measure import REC_LEAD
from sounar.pipewire import Sink
from sounar.presets import load_presets
from sounar.speakers import examples, load_setup
from sounar.state import load_state, save_state
from sounar.status import status_json

RATE = 48000
LAPTOP = "alsa_output.pci-0000_00_1f.3.analog-stereo-Speaker"
TRUE = {"front-left": 1.2, "front-right": 1.4, "rear-right": 2.1}     # metres from the seat
LINK = {0: 0.30, 1: 0.18}                                             # each output's Bluetooth delay, s
MIC_DX = 0.06
V = 3.8 * 5.0 * 2.6


class SimRoom:
    """Every speaker heard after its output's link delay and its distance, with a 63 Hz room boom
    and diffuse reverb; the built-in speaker hears the walls. Notes the graphs playing each time."""
    def __init__(self, pw, setup):
        self.pw, self.setup = pw, setup
        self.graphs_seen = []

    def _speaker(self, idx, L, R_):
        out = self.setup.outputs[idx]
        if np.any(L) and np.any(R_):
            return next(s for s in out.speakers if s.channels == ["FL", "FR"])
        ch = "FL" if np.any(L) else "FR"
        return next(s for s in out.speakers if s.channels == [ch])

    def _unit_ir(self, s, out_idx, ch, n):
        d = TRUE[s.id]
        side = math.sin(math.radians(s.at * 30)) * MIC_DX / R.C / 2 * (1 if ch == 1 else -1)
        t = LINK[out_idx] + d / R.C - side
        x = imp(n, t, 1 / d)
        tt = np.arange(n) / RATE
        x += np.where(tt >= t, 0.9 / d * np.sin(2 * np.pi * 63 * (tt - t)) * np.exp(-(tt - t) / 0.12), 0)
        e_dir = np.sum(R.bandpass(imp(n, t, 1 / d), RATE, 500, 4000) ** 2)
        x += tail(n, t + 0.004, RT, e_dir * (d / R.critical_distance(V, RT)) ** 2,
                  seed=hash((s.id, ch)) % 997, band=(500, 4000))
        return x

    def play_record(self, L, R_, switches, feeds, target=None, channels=1):
        self.graphs_seen.append(dict(self.pw.graphs))
        n = int(RATE * 1.4)
        if target == LAPTOP:
            irs, src = [sonar_ir(-0.03, n), sonar_ir(0.03, n)], L
        else:
            idx = switches[0][1]
            s = self._speaker(idx, L, R_)
            irs, src = [self._unit_ir(s, idx, c, n) for c in (0, 1)], (L if np.any(L) else R_)
        pre = np.zeros(int(REC_LEAD * RATE))
        out = np.stack([np.concatenate([pre, R.fftconv(src, ir)]) for ir in irs], axis=1)
        return out + np.random.default_rng(len(self.graphs_seen)).standard_normal(out.shape) * 2e-5


@pytest.fixture
def ctx(tmp_home):
    pw = original_room_system()
    pw.add_sink(Sink(LAPTOP, "Built-in speaker", ["FL", "FR"], 49152, bus="built-in"))
    s = load_setup(examples()["stereo-box-and-twin-right"])
    s.outputs[0].node, s.outputs[1].node = bt_names(pw)
    c = core.Ctx(s, pw, FakeEffects(), load_presets(None), say=lambda *_: None, notify=lambda *a: None,
                 settle=0, extra={"players": FakePlayers()})
    c.recorder = SimRoom(pw, s)
    core.switch(c, "music-room")
    st = load_state(c.presets)
    st["calibration"]["offsets_ms"] = {"front-left": 0.0, "front-right": 0.0, "rear-right": 0.7 / R.C * 1000}
    save_state(st)
    return c


def cut_in(text, cuts):
    return any(f"freq = {c['freq']:g} gain = {c['gain']:g}" in text for c in cuts)


def test_measure_room(ctx):
    r = roommeasure.measure(ctx)
    assert all(v["heard"] for v in r["speakers"].values())
    assert r["rt60"] == pytest.approx(RT, rel=0.2)
    assert r["sonar_ok"] and r["surfaces"]["floor"] == pytest.approx(0.75, abs=0.15)
    assert r["surfaces"]["left"] == pytest.approx(1.1, abs=0.15)          # its side learnt from the speakers
    for sid, d in TRUE.items():
        assert r["speakers"][sid]["dist_est"] == pytest.approx(d, rel=0.4)
    sp = r["speakers"]
    assert sp["rear-right"]["dist_est"] - sp["front-right"]["dist_est"] == pytest.approx(0.7, abs=0.02)
    assert len(r["cuts"]) <= 2 and any(abs(c["freq"] - 63) < 6 and -4 <= c["gain"] < 0 for c in r["cuts"])
    # the cuts play on every output, after the speakers' own correction
    for node in (o.node for o in ctx.setup.outputs):
        assert cut_in(ctx.pw.graphs[ctx.pw.ids[node]], r["cuts"])
    assert ctx.pw._sinks[LAPTOP].volume_raw == 49152                      # the built-in speaker put back
    assert core.verify(ctx) == []
    assert status_json(ctx)["room"]["cuts"] == r["cuts"]


def test_the_room_is_measured_without_its_own_cuts(ctx):
    first = roommeasure.measure(ctx)["cuts"]
    ctx.recorder.graphs_seen.clear()
    second = roommeasure.measure(ctx)["cuts"]
    assert ctx.recorder.graphs_seen and not any(cut_in(t, first) for g in ctx.recorder.graphs_seen for t in g.values())
    assert [c["freq"] for c in second] == [c["freq"] for c in first]       # same room, same cuts


def test_correction_off_and_on(ctx):
    cuts = roommeasure.measure(ctx)["cuts"]
    node = ctx.pw.ids[ctx.setup.outputs[0].node]
    roommeasure.set_correction(ctx, False)
    assert not cut_in(ctx.pw.graphs[node], cuts) and core.verify(ctx) == []
    roommeasure.set_correction(ctx, True)
    assert cut_in(ctx.pw.graphs[node], cuts)


def test_walls_by_hand_survive_a_new_measurement(ctx):
    roommeasure.measure(ctx)
    roommeasure.set_walls(ctx, 1.0, 2.5, 1.5, 2.0)
    box = load_state(ctx.presets)["room"]["box"]
    assert box["width"] == 3.5 and box["length"] == 3.5 and box["user"] and box["laptop"]["x"] == 1.0
    roommeasure.measure(ctx)
    assert load_state(ctx.presets)["room"]["box"]["width"] == 3.5
