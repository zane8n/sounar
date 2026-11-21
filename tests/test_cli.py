import json

import pytest

from fakes import FakeEffects, bt_names, original_room_system
from sounar import cli, core
from sounar.presets import load_presets
from sounar.speakers import examples, load_setup
from sounar.state import load_state


@pytest.fixture
def ctx(tmp_home, monkeypatch):
    pw = original_room_system()
    s = load_setup(examples()["stereo-box-and-twin-right"])
    s.outputs[0].node, s.outputs[1].node = bt_names(pw)
    c = core.Ctx(s, pw, FakeEffects(), load_presets(None), notify=lambda *a: None, settle=0)
    monkeypatch.setattr(cli, "make_ctx", lambda quiet=False, need_setup=True: c)
    return c


def test_cli_set_and_status_json(ctx, capsys):
    assert cli.main(["-q", "set", "immersive"]) == 0
    capsys.readouterr()
    assert cli.main(["status", "--json"]) == 0
    j = json.loads(capsys.readouterr().out)
    assert j["preset"] == "immersive" and j["version"] == 1


def test_preset_id_as_command(ctx):
    assert cli.main(["movie"]) == 0
    assert load_state(ctx.presets)["preset"] == "movie"


def test_cli_error_exit_code(ctx, capsys):
    assert cli.main(["set", "nope"]) == 1
    assert capsys.readouterr().out.startswith("sounar: no preset 'nope'")


def test_cli_tweak_syntax(ctx):
    cli.main(["-q", "music-room"])
    assert cli.main(["-q", "vocal", "+0.5"]) == 0
    assert load_state(ctx.presets)["tweaks"]["music-room"]["vocal"] == 1.5
    assert cli.main(["-q", "vocal", "=2"]) == 0
    assert load_state(ctx.presets)["tweaks"]["music-room"]["vocal"] == 2.0
    assert cli.main(["-q", "tune", "bass=2", "fill=-6"]) == 0
    assert cli.main(["-q", "reset"]) == 0 and load_state(ctx.presets)["tweaks"] == {}


def test_cli_balance_nudge(ctx):
    assert cli.main(["-q", "balance", "rear-right", "-1"]) == 0
    assert cli.main(["-q", "nudge", "front-left", "+2"]) == 0
    assert load_state(ctx.presets)["sync_ms"]["front-left"] == 2.0
    assert cli.main(["-q", "balance", "+1"]) == 1                    # which speaker?


def test_effects_commands(ctx):
    assert cli.main(["-q", "effects", "off"]) == 0 and ctx.ee.bypassed is True
    assert cli.main(["-q", "effects", "on"]) == 0 and ctx.ee.bypassed is False
    assert cli.main(["-q", "ee", "Sounar Late Night"]) == 0
    assert load_state(ctx.presets)["tweaks"]["music-room"]["ee"] == "Sounar Late Night"
    assert cli.main(["-q", "effects", "leveling", "off"]) == 0
    assert ctx.ee.calls[-1][0:2] == ("leveling", False)


def test_help_lists_commands(capsys):
    assert cli.main(["help"]) == 0
    out = capsys.readouterr().out
    for cmd in ("sounar calibrate", "sounar setup", "sounar balance", "sounar doctor", "SOUNAR_DRY_RUN"):
        assert cmd in out


def test_version(capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "sounar 0.1.0"


def test_unknown_command(tmp_home, capsys):
    assert cli.main(["frobnicate"]) == 2
