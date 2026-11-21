import pytest

from fakes import FakeEffects, FakePipeWire, bt_names, original_room_system
from sounar import SounarError, core, paths, wizard
from sounar.presets import load_presets
from sounar.speakers import examples, load_setup


def answers(*items):
    it = iter(items)
    asked = []

    def ask(q=""):
        asked.append(q)
        return next(it)
    ask.asked = asked
    return ask


def ctx_for(pw):
    return core.Ctx(None, pw, FakeEffects(), load_presets(None), say=lambda *_: None, notify=lambda *a: None, settle=0)


def expected_original(pw):
    s = load_setup(examples()["stereo-box-and-twin-right"])
    s.outputs[0].node, s.outputs[1].node = bt_names(pw)
    s.mic = ""
    return s


def test_candidates_skip_virtual():
    pw = original_room_system()
    assert [s.name for s in wizard.candidates(pw)] == bt_names(pw)


def test_scripted_original_room(tmp_home):
    pw = original_room_system()
    ask = answers("1 2",                          # use both outputs
                  "y", "1", "11", "y", "",        # heard it; one box; at 11; limits its bass; no nickname
                  "y", "2", "2", "n", "", "4", "n", "",   # heard; two boxes: left at 2, right at 4
                  "",                             # default microphone
                  "y")                            # save
    s = wizard.run(ctx_for(pw), ask=ask, say=lambda *_: None)
    assert s == expected_original(pw)
    assert any("clock" in q for q in ask.asked)
    assert ("play", bt_names(pw)[0]) in [c[:2] for c in pw.calls]


def test_example_prefill_only_asks_mapping(tmp_home):
    pw = original_room_system()
    ask = answers("1", "2", "", "y")
    s = wizard.run(ctx_for(pw), ask=ask, say=lambda *_: None, example="stereo-box-and-twin-right")
    assert s == expected_original(pw)
    assert len(ask.asked) == 4


def test_bad_answers_are_asked_again(tmp_home):
    pw = original_room_system()
    ask = answers("1 2", "y", "1", "13", "eleven", "11", "y", "", "y", "1", "3", "n", "", "", "y")
    s = wizard.run(ctx_for(pw), ask=ask, say=lambda *_: None)
    assert s.speakers()[0].at == 11 and s.speakers()[1].at == 3


def test_combine_conf_matches_nodes():
    pw = original_room_system()
    text = wizard.combine_conf(expected_original(pw))
    assert 'node.name = "sounar"' in text and "combine.latency-compensate = true" in text
    for n in bt_names(pw):
        assert f'node.name = "{n}"' in text


def test_correction_conf():
    pw = original_room_system()
    s = expected_original(pw)
    assert wizard.correction_conf(s) is None
    s.outputs[0].correction = [{"type": "highpass", "freq": 50.0, "gain": 0.0, "q": 0.707}]
    text = wizard.correction_conf(s)
    assert "monitor.bluez.rules" in text and bt_names(pw)[0] in text and "bq_highpass" in text
    assert bt_names(pw)[1] not in text


def test_refuses_old_pipewire(tmp_home):
    pw = FakePipeWire(original_room_system().sinks(), version=(1, 2, 7))
    with pytest.raises(SounarError, match="PipeWire 1.4"):
        wizard.run(ctx_for(pw), ask=answers(), say=lambda *_: None)


def test_declined_restart_changes_nothing(tmp_home):
    pw = original_room_system()
    pw.default = "alsa_output.speaker"
    wizard.finish(ctx_for(pw), expected_original(pw), ask=answers("n"), say=lambda *_: None)
    assert paths.setup_file().exists()
    assert not [c for c in pw.calls if c[0] in ("systemctl", "set_default_sink", "set_graph")]


def test_finish_restarts_enables_and_applies(tmp_home):
    pw = original_room_system()
    pw.default = "alsa_output.speaker"
    ctx = ctx_for(pw)
    wizard.finish(ctx, expected_original(pw), ask=answers("y", "n"), say=lambda *_: None)
    calls = [c for c in pw.calls if c[0] == "systemctl"]
    assert ("systemctl", "restart", "pipewire.service", "pipewire-pulse.service", "wireplumber.service") in calls
    assert ("systemctl", "enable", "--now", "sounar-guard.service", "sounar-keepalive.service") in calls
    assert pw.default == "sounar"
    assert (paths.state_dir() / "previous-default").read_text().strip() == "alsa_output.speaker"
    assert len([c for c in pw.calls if c[0] == "set_graph"]) == 2
    conf = paths.xdg_config_home() / "pipewire/pipewire.conf.d/sounar.conf"
    assert conf.exists()
    bt = paths.xdg_config_home() / "wireplumber/wireplumber.conf.d/sounar-bluetooth.conf"
    assert "monitor.bluez.seat-monitoring = disabled" in bt.read_text()       # the suspend-crash fix


def test_no_bluetooth_conf_without_bluetooth_outputs():
    s = expected_original(original_room_system())
    for o in s.outputs:
        o.node = "alsa_output.usb-speaker"
    assert wizard.bluetooth_conf(s) is None
