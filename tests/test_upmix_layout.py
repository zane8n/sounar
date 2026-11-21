"""The room layout with the upmixer (Placement above 0 and the plugin installed)."""
import pytest

from fakes import FakeEffects, bt_names, original_room_system
import sim
from sounar import core, paths
from sounar.layouts import UPMIX_LATENCY_S, plan, upmix_chains
from sounar.presets import applicable, load_presets
from sounar.speakers import Output, Setup, Speaker, examples, load_setup
from sounar.state import effective, load_state


@pytest.fixture
def plugin():
    (paths.ladspa_dir() / "sounar_upmix.so").write_bytes(b"")      # "installed"


def original():
    s = load_setup(examples()["stereo-box-and-twin-right"])
    s.outputs[0].node, s.outputs[1].node = "n1", "n2"
    return s


def eff(pid="music-room", **params):
    ps = load_presets(None)
    st = load_state(ps)
    st["preset"] = pid
    e = effective(st, ps)
    e.params.update(params)
    return e, st


def nodes(text):
    return {n["name"]: n for n in sim.parse(text)["nodes"]}


def test_music_places_sounds_with_the_plugin(plugin):
    e, st = eff()
    assert e.params["place"] == 0.6
    p = plan(original(), e, st, {"n1", "n2"})
    box, twin = (nodes(o.text) for o in p.outputs)
    up = [n for n in box.values() if n.get("type") == "ladspa"]
    assert len(up) == 1 and up[0]["plugin"] == "sounar_upmix" and up[0]["label"] == "sounar_upmix"
    c = up[0]["control"]
    assert float(c["Self"]) == 1 and float(c["Count"]) == 3 and float(c["Placement"]) == pytest.approx(0.6)
    # the map: the box at 11 o'clock (front), the twin at 2 (front) and 4 (surround)
    assert [float(c[f"Zone {i}"]) for i in (1, 2, 3, 4)] == [1, 1, 2, 0]
    assert float(c["Angle 1"]) == pytest.approx(-30) and float(c["Angle 3"]) == pytest.approx(120)
    rear = twin["s1u"]["control"]
    assert float(rear["Self"]) == 3 and float(rear["Base M"]) == 0 and float(rear["Base amb"]) != 0
    assert "s1a" in twin and "s1h" in twin                           # the surround's ambience band and delay
    assert float(twin["s0u"]["control"]["Base amb"]) == 0             # the front unit has none


def test_placement_zero_or_no_plugin_is_the_plain_room_mix(plugin):
    e, st = eff(place=0.0)
    assert "ladspa" not in "".join(o.text for o in plan(original(), e, st, {"n1", "n2"}).outputs)
    (paths.ladspa_dir() / "sounar_upmix.so").unlink()
    e, st = eff()
    assert "ladspa" not in "".join(o.text for o in plan(original(), e, st, {"n1", "n2"}).outputs)
    assert "place" not in applicable(original(), "room")             # no slider that does nothing


def test_placement_is_offered_with_the_plugin(plugin):
    a = applicable(original(), "room")
    assert "place" in a and "wrap" in a


def test_a_lone_stereo_box_is_two_chains_and_a_sub_waits_for_the_plugin(plugin):
    s = Setup(outputs=[Output("n1", [Speaker("front", ["FL", "FR"], at=12)]),
                       Output("n2", [Speaker("sub", ["FL", "FR"], at=12, sub=True)])])
    e, st = eff()
    p = plan(s, e, st, {"n1", "n2"})
    box, sub = (nodes(o.text) for o in p.outputs)
    ups = sorted(n["name"] for n in box.values() if n.get("type") == "ladspa")
    assert ups == ["s0lu", "s0ru"]
    left, right = box["s0lu"]["control"], box["s0ru"]["control"]
    assert float(left["Angle 1"]) == -15 and float(left["Angle 2"]) == 15
    assert float(left["Base S"]) == -float(right["Base S"])           # M + side*S left, M - side*S right
    assert float(sub["s0d"]["control"]["Delay (s)"]) == pytest.approx(UPMIX_LATENCY_S, abs=1e-5)


def test_too_many_chains_fall_back_to_the_plain_mix(plugin):
    spk = [Speaker(f"s{i}", ["FL"], at=(i * 1.3) % 12) for i in range(9)]
    gains = {s.id: type("G", (), {"split": False})() for s in spk}
    assert upmix_chains(spk, gains) is None


def test_applied_and_verified_live(plugin, tmp_home):
    pw = original_room_system()
    s = load_setup(examples()["stereo-box-and-twin-right"])
    s.outputs[0].node, s.outputs[1].node = bt_names(pw)
    ctx = core.Ctx(s, pw, FakeEffects(), load_presets(None), say=lambda *_: None, notify=lambda *a: None, settle=0)
    core.switch(ctx, "music-room")
    assert core.verify(ctx) == [] and "sounar_upmix" in "".join(pw.graphs.values())
    core.set_params(ctx, {"place": 0.9}, "tune")
    assert core.verify(ctx) == [] and "0.900000" in "".join(pw.graphs.values())
