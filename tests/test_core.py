import pytest

from fakes import FakeEffects, original_room_system, bt_names, legacy_presets
from sounar import SounarError, core
from sounar.pipewire import raw_to_db
from sounar.speakers import examples, load_setup
from sounar.state import Measuring, history, load_state, save_state


def original_setup(pw):
    s = load_setup(examples()["stereo-box-and-twin-right"])
    names = bt_names(pw)
    s.outputs[0].node, s.outputs[1].node = names[0], names[1]
    return s


@pytest.fixture
def ctx(tmp_home):
    pw = original_room_system()
    said, notes = [], []
    c = core.Ctx(original_setup(pw), pw, FakeEffects(), legacy_presets(), say=said.append,
                 notify=lambda t, b="", i="": notes.append((t, b)), settle=0)
    c.said, c.notes = said, notes
    return c


def graph_calls(ctx):
    return [c for c in ctx.pw.calls if c[0] == "set_graph"]


def test_switch_sets_one_graph_per_output(ctx):
    p = core.switch(ctx, "club")
    assert p.layout == "room" and len(graph_calls(ctx)) == 2
    st = load_state(ctx.presets)
    assert st["preset"] == "club" and st["previous"] == "music-room"
    assert ctx.ee.loaded == "Sounar Room" and ctx.ee.target is not None
    assert ctx.notes[-1][0].endswith("Club")


def test_switch_fuzzy_and_unknown(ctx):
    assert core.switch(ctx, "Concert").wanted == "room"
    assert load_state(ctx.presets)["preset"] == "concert"
    with pytest.raises(SounarError, match="choose from"):
        core.switch(ctx, "nope")


def test_switch_partial_connection_stereo_note(ctx):
    ctx.pw.remove_sink(ctx.setup.outputs[1].node)
    p = core.switch(ctx, "music-room")
    assert p.layout == "stereo" and len(graph_calls(ctx)) == 1
    assert "missing" in ctx.notes[-1][1]


def test_switch_no_speakers(ctx):
    for o in ctx.setup.outputs:
        ctx.pw.remove_sink(o.node)
    p = core.switch(ctx, "party")
    assert p.outputs == [] and graph_calls(ctx) == []
    assert "No speakers connected" in ctx.notes[-1][1]
    assert load_state(ctx.presets)["preset"] == "party"


def test_step_and_back(ctx):
    core.switch(ctx, "tv")                       # built-ins first, then the user's own: ... tv, party
    core.step(ctx, 1)
    assert load_state(ctx.presets)["preset"] == "party"
    core.back(ctx)
    assert load_state(ctx.presets)["preset"] == "tv"


def test_tune_refuses_inapplicable(ctx):
    ctx.setup.outputs[1].speakers.pop()          # no rear speaker any more
    core.switch(ctx, "music-room")
    with pytest.raises(SounarError, match="Room fill doesn't apply"):
        core.set_params(ctx, {"fill": -3.0}, "tune")
    with pytest.raises(SounarError, match="has no 'width'"):
        core.set_params(ctx, {"width": 0.3}, "tune")


def test_tweak_relative_and_absolute(ctx):
    core.switch(ctx, "music-room")
    core.tweak(ctx, "vocal", "+0.5")
    assert load_state(ctx.presets)["tweaks"] == {"music-room": {"vocal": 1.5}}
    core.tweak(ctx, "vocal", "=2")
    assert load_state(ctx.presets)["tweaks"]["music-room"]["vocal"] == 2.0
    core.tweak(ctx, "center", "-1")
    assert load_state(ctx.presets)["tweaks"]["music-room"]["centre"] == 5.0
    core.tweak(ctx, "vocal", "99")
    assert load_state(ctx.presets)["tweaks"]["music-room"]["vocal"] == 6.0          # clamped to the limit
    core.reset(ctx)
    assert load_state(ctx.presets)["tweaks"] == {}


def test_tune_several(ctx):
    core.switch(ctx, "music-room")
    core.tune(ctx, ["vocal=1.5", "bass=2"])
    assert load_state(ctx.presets)["tweaks"]["music-room"] == {"vocal": 1.5, "bass": 2.0}


def test_balance_raises_then_lowers_and_shifts_levels(ctx):
    st = load_state(ctx.presets)
    st["calibration"]["levels"] = {"front-left": 0.0, "front-right": -3.0, "rear-right": -3.0}
    save_state(st)
    a, b = ctx.setup.outputs[0].node, ctx.setup.outputs[1].node
    va, vb = (ctx.pw._sinks[n].vol_db for n in (a, b))
    room = -vb                                           # how far the second output can go up
    core.balance(ctx, "rear-right", room + 1.0)          # louder by more than its headroom
    na, nb = (ctx.pw._sinks[n].vol_db for n in (a, b))
    assert nb == pytest.approx(0.0, abs=0.01)            # raised to 100 % first
    assert na == pytest.approx(va - 1.0, abs=0.05)       # then the others lowered by the rest
    lv = load_state(ctx.presets)["calibration"]["levels"]
    moved = (nb - vb) - (na - va)
    assert lv["front-right"] == pytest.approx(-3.0 + moved, abs=0.01)
    assert lv["rear-right"] == pytest.approx(-3.0 + moved, abs=0.01) and lv["front-left"] == 0.0
    assert history()[-1]["action"] == "balance"


def test_balance_needs_two_outputs(ctx):
    ctx.pw.remove_sink(ctx.setup.outputs[0].node)
    with pytest.raises(SounarError, match="two outputs"):
        core.balance(ctx, "front-right", 1.0)


def test_undo_restores_volumes_and_levels(ctx):
    before = {n: s.volume_raw for n, s in ctx.pw._sinks.items()}
    core.balance(ctx, "front-left", -2.0)
    core.undo(ctx)
    assert {n: s.volume_raw for n, s in ctx.pw._sinks.items()} == before
    assert load_state(ctx.presets)["calibration"]["levels"].get("front-left", 0.0) == 0.0


def test_nudge_moves_sync(ctx):
    core.nudge(ctx, "front-left", 2.5)
    assert load_state(ctx.presets)["sync_ms"] == {"front-left": 2.5, "front-right": 0.0}
    core.nudge(ctx, "front-left", -4.0)
    assert load_state(ctx.presets)["sync_ms"] == {"front-left": 0.0, "front-right": 1.5}
    assert "waits 1.5 ms" in core.sync_text(ctx.setup, load_state(ctx.presets))


def test_verify_detects_stale_graph(ctx):
    core.switch(ctx, "music-room")
    assert core.verify(ctx) == []
    nid = ctx.pw.ids[ctx.setup.outputs[1].node]
    ctx.pw.live[nid]["snr:Gain 8"] = 0.123
    assert core.verify(ctx) == ["front-right"]


def test_repair_reapplies(ctx):
    core.switch(ctx, "music-room")
    nid = ctx.pw.ids[ctx.setup.outputs[0].node]
    ctx.pw.live[nid] = {}
    msg = core.repair(ctx)
    assert "front-left" in msg and core.verify(ctx) == []


def test_repair_fixes_leveling_target(ctx):
    core.switch(ctx, "music-room")
    ctx.ee.target = -3
    assert "leveling target" in core.repair(ctx)


def test_repair_skips_while_measuring(ctx):
    with Measuring("sync"):
        assert core.repair(ctx) is None


def test_changes_refused_while_measuring(ctx):
    with Measuring("calibrate"):
        with pytest.raises(SounarError, match="being measured"):
            core.switch(ctx, "club")


def test_effects_missing_still_switches(ctx):
    ctx.ee = FakeEffects(available=False)
    p = core.switch(ctx, "club")
    assert len(p.outputs) == 2 and len(graph_calls(ctx)) == 2


def test_effects_choice_saved_as_tweak(ctx):
    core.switch(ctx, "club")
    core.set_effects(ctx, "classical room")
    assert load_state(ctx.presets)["tweaks"] == {"club": {"ee": "Sounar Classical Room"}}
    assert ctx.ee.loaded == "Sounar Classical Room"
    core.set_effects(ctx, "preset")
    assert load_state(ctx.presets)["tweaks"] == {} and ctx.ee.loaded == "Sounar Room"
    with pytest.raises(SounarError, match="no Easy Effects preset"):
        core.set_effects(ctx, "zzz")


def test_load_ctx_without_setup(tmp_home):
    with pytest.raises(SounarError, match="sounar setup"):
        core.load_ctx()
    assert core.load_ctx(need_setup=False).setup is None


def test_volume_db_helper():
    assert raw_to_db(65536) == 0.0


def test_move_speaker_saves_and_reapplies(ctx, tmp_home):
    from sounar import paths
    from sounar.speakers import load_setup
    core.switch(ctx, "music-room")
    n = len(graph_calls(ctx))
    core.move_speaker(ctx, "rear-right", 4.5)
    assert load_setup(paths.setup_file()).speakers()[2].at == 4.5
    assert len(graph_calls(ctx)) > n and core.verify(ctx) == []
    core.move_speaker(ctx, "rear-right", 0)                 # 0 o'clock is 12
    assert load_setup(paths.setup_file()).speakers()[2].at == 12.0
    with pytest.raises(SounarError, match="no speaker"):
        core.move_speaker(ctx, "nope", 3)

