"""The original room (setups/stereo-box-and-twin-right) must sound exactly as it did with the
prototype: for every preset, the same filter graphs (compared by their simulated responses) and
the same leveling target. Fixtures: tools/record-prototype-fixtures.py."""
import json
from pathlib import Path

import pytest

import sim
from sounar.effects import Effects, autogain_target, side_gain
from sounar.layouts import loudness_db, plan
from sounar.presets import load_presets
from sounar.speakers import examples, load_setup
from sounar.state import effective, load_state
from sounar import paths

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "prototype").glob("*.json"))


def original_room(rec):
    s = load_setup(examples()["stereo-box-and-twin-right"])
    s.outputs[0].node, s.outputs[1].node = "n1", "n2"
    s.outputs[0].correction = rec["correction"]["stereo-box"]
    s.outputs[1].correction = rec["correction"]["twin"]
    return s


def state_for(rec, ps):
    st = load_state(ps)
    lv, sync = rec["state"]["levels"], rec["state"]["sync_ms"]
    st["calibration"]["levels"] = {"front-left": lv["stereo-box"], "front-right": lv["twin-front"],
                                   "rear-right": lv["twin-rear"]}
    st["calibration"]["offsets_ms"] = {"front-left": 0.0, "front-right": 0.0, "rear-right": rec["state"]["align_ms"]}
    st["sync_ms"] = {"front-left": sync["stereo-box"], "front-right": sync["twin"]}
    st["preset"] = rec["preset"]
    return st


def shipped_effects():
    return Effects(which=lambda _: None, preset_dir=paths.package_data() / "easyeffects",
                   socket_path="/nonexistent")


@pytest.fixture
def ps():
    # the engine must still build the prototype's graphs for the nine presets it was pinned on;
    # 0.2 ships three, so the nine come back as a user presets file
    return load_presets(Path(__file__).parent / "fixtures/prototype/presets-0.1.toml")


def test_fixtures_present():
    assert len(FIXTURES) == 18


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_every_preset_equivalent(path, tmp_home, ps):
    rec = json.loads(path.read_text())
    s, st = original_room(rec), state_for(rec, ps)
    e = effective(st, ps)
    assert e.params == rec["params"] and e.layout == rec["layout"] and e.ee == rec["ee"]
    p = plan(s, e, st, {"n1", "n2"})
    assert p.layout == rec["layout"]
    by_id = {o.output_id: o.text for o in p.outputs}
    for oid, role in (("front-left", "stereo-box"), ("front-right", "twin")):
        diff = sim.max_difference(by_id[oid], rec["graphs"][role])
        assert diff < 1e-6, f"{role}: responses differ by {diff:.2e}"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_leveling_targets_match(path, tmp_home, ps):
    rec = json.loads(path.read_text())
    s, st = original_room(rec), state_for(rec, ps)
    e = effective(st, ps)
    data = shipped_effects().data(e.ee)
    loud = loudness_db(s, e, st, side_gain(data))
    assert loud == pytest.approx(rec["loudness_db"], abs=1e-9)
    assert autogain_target(data, loud) == rec["autogain_target"]
