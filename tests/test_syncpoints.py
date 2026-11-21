"""`sounar sync undo` and `sounar sync stable` (sounar/syncpoints.py)."""
import pytest

from test_calibrate import ROOM, make_ctx
from sounar import SounarError, core, syncpoints
from sounar.calibrate import calibrate, sync
from sounar.state import load_state, save_state
from sounar.status import status_json


def sync_ms(ctx):
    return load_state(ctx.presets)["sync_ms"]


def test_settled_sync_becomes_stable_and_nudges_go_back_to_it(tmp_home):
    ctx, room, _ = make_ctx(ROOM)
    core.switch(ctx, "music-room")
    sync(ctx)
    good = dict(sync_ms(ctx))
    st = load_state(ctx.presets)
    assert st["sync_stable"]["sync_ms"] == good and st["sync_stable"]["what"] == "sync"
    core.nudge(ctx, "front-left", 4.0)
    core.nudge(ctx, "front-left", 3.0)
    nudged = dict(sync_ms(ctx))
    assert nudged != good
    assert load_state(ctx.presets)["sync_stable"]["sync_ms"] == good        # nudges never move it
    core.stable_sync(ctx)
    assert sync_ms(ctx) == good and core.verify(ctx) == []
    core.undo_sync(ctx)                                                     # stable is one undo step
    assert sync_ms(ctx) == nudged


def test_two_quick_nudges_are_one_undo_step(tmp_home):
    ctx, _, _ = make_ctx(ROOM)
    core.switch(ctx, "music-room")
    start = dict(sync_ms(ctx))
    core.nudge(ctx, "front-left", 2.0)
    core.nudge(ctx, "front-left", 2.0)
    assert len(syncpoints.points(load_state(ctx.presets))) == 1
    core.undo_sync(ctx)
    assert sync_ms(ctx) == start


def test_undo_with_nothing_to_undo_says_so(tmp_home):
    ctx, _, _ = make_ctx(ROOM)
    with pytest.raises(SounarError, match="no earlier sync"):
        core.undo_sync(ctx)


def test_stable_needs_a_settled_measurement(tmp_home):
    ctx, _, _ = make_ctx(ROOM)
    with pytest.raises(SounarError, match="no stable sync yet"):
        core.stable_sync(ctx)


def test_points_for_other_outputs_are_not_offered(tmp_home):
    ctx, _, _ = make_ctx(ROOM)
    core.switch(ctx, "music-room")
    st = load_state(ctx.presets)
    st["sync_ms"] = {"front-left": 0.0, "front-right": 18.0}
    st["sync_undo"] = [{"what": "sync", "ts": "2026-11-14T21:49:00", "last": "2026-11-14T21:49:00",
                        "sync_ms": {"front-left": 347.0, "front-right": 163.0, "tv": 0.0}}]
    st["sync_stable"] = {"what": "sync", "ts": "2026-11-14T21:49:00",
                         "sync_ms": {"front-left": 347.0, "front-right": 163.0, "tv": 0.0}}
    save_state(st)
    st = load_state(ctx.presets)
    assert syncpoints.points(st) == [] and syncpoints.stable_point(st) is None
    with pytest.raises(SounarError):
        core.undo_sync(ctx)


def test_stable_when_already_there_changes_nothing(tmp_home):
    ctx, _, _ = make_ctx(ROOM)
    core.switch(ctx, "music-room")
    sync(ctx)
    n = len(load_state(ctx.presets).get("sync_undo", []))
    calls = len(ctx.pw.calls)
    said = []
    ctx.say = said.append
    core.stable_sync(ctx)
    assert any("already stable" in m for m in said)
    assert len(load_state(ctx.presets).get("sync_undo", [])) == n
    assert not [c for c in ctx.pw.calls[calls:] if c[0] == "set_graph"]


def test_calibration_sets_the_stable_sync(tmp_home):
    ctx, _, _ = make_ctx(ROOM)
    core.switch(ctx, "music-room")
    calibrate(ctx)
    st = load_state(ctx.presets)
    assert st["sync_stable"]["what"] == "calibrate" and st["sync_stable"]["sync_ms"] == st["sync_ms"]


def test_status_reports_undo_and_stable(tmp_home):
    ctx, _, _ = make_ctx(ROOM)
    core.switch(ctx, "music-room")
    sync(ctx)
    core.nudge(ctx, "front-left", 5.0)
    j = status_json(ctx)["sync"]
    assert j["stable"] and j["stable"]["active"] is False and j["stable"]["what"] == "sync"
    assert j["undo"] and j["undo"]["what"] == "by-ear changes"
