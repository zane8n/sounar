import json

import pytest

from fakes import FakeEffects, bt_sink, original_room_system, bt_names
from sounar import core, paths
from sounar.guard import Guard
from sounar.pipewire import Stream
from sounar.presets import load_presets
from sounar.speakers import examples, load_setup
from sounar.state import Measuring, history_append


@pytest.fixture
def ctx(tmp_home):
    pw = original_room_system()
    s = load_setup(examples()["stereo-box-and-twin-right"])
    s.outputs[0].node, s.outputs[1].node = bt_names(pw)
    notes = []
    c = core.Ctx(s, pw, FakeEffects(), load_presets(None), say=lambda *_: None,
                 notify=lambda t, b="", i="": notes.append((t, b)), settle=0)
    c.notes = notes
    core.switch(c, "music-room")
    return c


def guard(ctx):
    return Guard(lambda: ctx, settle=0)


def test_quiet_when_all_is_well(ctx):
    assert guard(ctx).check() == []


def test_restores_default_output(ctx):
    ctx.pw.default = ctx.setup.outputs[0].node
    msgs = guard(ctx).check()
    assert ctx.pw.default == "sounar" and any("default output" in m for m in msgs)


def test_unmutes_feed_and_sets_100(ctx):
    f = ctx.pw.feed_list[1]
    f.mute, f.volume_raw = True, 30000
    msgs = guard(ctx).check()
    assert not f.mute and f.volume_raw == 65536 and len(msgs) == 2


def test_reconnect_restores_volume_and_notifies_sync(ctx):
    node = ctx.setup.outputs[1].node
    vols = {s.name: s.volume_raw for s in ctx.pw.sinks()}
    history_append("calibrate", {"volumes": vols}, {"volumes": vols, "offsets": {}}, "test")
    g = guard(ctx)
    g.check()
    ctx.pw.remove_sink(node)
    g.check()
    ctx.pw.add_sink(bt_sink(2, "Speaker B", volume_raw=20000))
    msgs = g.check()
    assert ctx.pw._sinks[node].volume_raw == vols[node]
    assert any("reconnected" in m for m in msgs)
    assert ctx.notes[-1][0] == "Speaker reconnected" and "sounar sync" in ctx.notes[-1][1]
    assert core.verify(ctx) == []                       # its graph came back too


def test_restores_latency_offset(ctx):
    a = ctx.pw.sinks()[0]
    history_append("calibrate", {}, {"volumes": {}, "offsets": {f"{a.card}:{a.port}": 140000}}, "test")
    a.offset_us = 0
    guard(ctx).check()
    assert ctx.pw._sinks[a.name].offset_us == 140000


def test_restarts_keepalive(ctx):
    ctx.pw.stream_list = []
    msgs = guard(ctx).check()
    assert ("systemctl", "restart", "sounar-keepalive.service") in ctx.pw.calls and msgs


def test_unmutes_left_streams(ctx):
    ctx.pw.stream_list.append(Stream(401, "browser", True, False, "browser", "Video"))
    paths.state_dir().mkdir(parents=True, exist_ok=True)
    (paths.state_dir() / "muted.json").write_text(json.dumps([{"index": 401, "app": "browser"}]))
    msgs = guard(ctx).check()
    assert not ctx.pw.stream_list[-1].mute and any("left muted" in m for m in msgs)


def test_does_nothing_while_measuring(ctx):
    ctx.pw.default = ctx.setup.outputs[0].node
    with Measuring("sync"):
        assert guard(ctx).check() == []
    assert ctx.pw.default != "sounar"


def test_without_setup_does_nothing(tmp_home):
    c = core.Ctx(None, original_room_system(), FakeEffects(), load_presets(None), say=lambda *_: None)
    assert Guard(lambda: c, settle=0).check() == []


def test_same_error_logged_once(tmp_home):
    from sounar import SounarError
    from sounar.state import log  # noqa: F401

    def broken():
        raise SounarError("setup.toml: unknown setting 'postion'")
    g = Guard(broken, settle=0)
    for _ in range(3):
        g.safe_check()
    lines = [ln for ln in (paths.state_dir() / "log").read_text().splitlines() if "postion" in ln]
    assert len(lines) == 1


# ---- Easy Effects comes up on the wrong preset, or linked to nothing

def test_new_easy_effects_gets_the_active_preset_back(ctx):
    g = guard(ctx)
    g.check()                                     # seen once: process 4242 on the right preset
    ctx.ee.loaded = "Sounar Movie Night"          # Easy Effects restarted on its last-saved preset
    ctx.ee.pid_value = "5151"
    msgs = g.check()
    assert ctx.ee.loaded == "Sounar Room" and any("came up on Sounar Movie Night" in m for m in msgs)
    assert g.check() == []                        # same process again: nothing to do


def test_easy_effects_linked_to_nothing_is_restarted_on_the_second_check(ctx):
    g = guard(ctx)
    ctx.pw.out_ports = "ee_soe_output_level:output_FL\nee_soe_output_level:output_FR\n"
    ctx.pw.link_text = ""
    g.check()
    assert not [c for c in ctx.pw.calls if c[:2] == ("systemctl", "restart")]
    msgs = g.check()
    assert ("systemctl", "restart", "app-com.github.wwmm.easyeffects@autostart.service") in ctx.pw.calls
    assert any("linked to nothing" in m for m in msgs)


# ---- the audio server stops answering

class Stack:
    """A fake audio server: answers or hangs; records systemctl calls; a controllable clock."""
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls, self.t, self.notes = [], 1000.0, []

    def pulse_ok(self, timeout=8):
        return self.answers.pop(0) if self.answers else True

    def systemctl(self, *a):
        self.calls.append(a)
        return type("R", (), {"stdout": "active\n" if a[:1] == ("is-active",) else ""})()


def stuck_guard(stack, make_ctx=None):
    def broken():
        raise RuntimeError("pactl: Connection failure: Timeout")
    return Guard(make_ctx or broken, settle=0, pulse_ok=stack.pulse_ok, systemctl=stack.systemctl,
                 clock=lambda: stack.t, sleep=lambda s: None, notify=lambda t, b: stack.notes.append(t))


def test_stuck_audio_is_restarted_after_two_failed_checks():
    stack = Stack([False, False, True])           # hangs, hangs, then answers after the restart
    g = stuck_guard(stack)
    g.safe_check()
    assert not [c for c in stack.calls if c[0] == "restart"]          # one failure: wait
    g.safe_check()
    restarts = [c for c in stack.calls if c[0] == "restart"]
    assert restarts[0][1:] == ("pipewire.service", "pipewire-pulse.service", "wireplumber.service")
    assert set(restarts[1][1:]) == {"app-com.github.wwmm.easyeffects@autostart.service", "sounar-keepalive.service"}
    assert stack.notes[-1] == "Audio is back"


def test_no_restart_while_pactl_answers():
    stack = Stack([True] * 10)                    # checks fail for another reason
    g = stuck_guard(stack)
    for _ in range(5):
        g.safe_check()
    assert not [c for c in stack.calls if c[0] == "restart"]


def test_at_most_one_restart_per_ten_minutes():
    stack = Stack([False, False, True] + [False] * 40)
    g = stuck_guard(stack)
    g.safe_check(); g.safe_check()                # restart 1
    stack.t += 120
    for _ in range(4):
        g.safe_check()
    assert len([c for c in stack.calls if c[:2] == ("restart", "pipewire.service")]) == 1
    assert stack.notes.count("Audio is stuck") == 1
