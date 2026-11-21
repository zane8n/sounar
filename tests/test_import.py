import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from sounar.presets import load_presets
from sounar.speakers import load_setup

REPO = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures" / "prototype-home"


def run_import(home, *extra):
    return subprocess.run([sys.executable, "-I", str(REPO / "tools/import-prototype.py"), "--home", str(home), *extra],
                          capture_output=True, text=True, timeout=60)


@pytest.fixture
def home(tmp_path):
    import shutil
    h = tmp_path / "home"
    shutil.copytree(FIX, h)
    return h


def test_import_maps_everything(home):
    r = run_import(home)
    assert r.returncode == 0, r.stdout + r.stderr
    s = load_setup(home / ".config/sounar/setup.toml")
    assert s.combine_node == "combine_speakers" and s.combine_managed is False
    assert s.mic == "alsa_input.test-mic"
    assert [o.node for o in s.outputs] == ["bluez_output.00_00_00_00_00_01.1", "bluez_output.00_00_00_00_00_02.1"]
    assert [(sp.id, sp.at, sp.channels, sp.bass_boost) for sp in s.speakers()] == [
        ("front-left", 11, ["FL", "FR"], False), ("front-right", 2, ["FL"], True), ("rear-right", 4, ["FR"], True)]
    assert s.outputs[0].correction[0] == {"type": "highshelf", "freq": 10.0, "gain": -2.0, "q": 0.7}
    assert len(s.outputs[1].correction) == 7
    st = json.loads((home / ".local/state/sounar/state.json").read_text())
    assert st["preset"] == "club" and st["previous"] == "music-room"
    assert st["tweaks"] == {"music-room": {"bass": 1.5}, "party": {"ee": "Sounar Late Night", "width": 0.4}}
    assert st["calibration"]["levels"] == {"front-left": 0.0, "front-right": -3.18, "rear-right": -2.85}
    assert st["calibration"]["offsets_ms"] == {"front-left": 0.0, "front-right": 0.0, "rear-right": 3.0}
    assert st["calibration"]["ts"] == "2026-10-24T13:43:55"
    assert st["sync_ms"] == {"front-left": 0.0, "front-right": 17.9}
    # the user's presets file keeps what differs from the built-ins; prototype presets that are
    # no longer built in (0.2 ships three) come across whole, so none is lost
    user = tomllib.loads((home / ".config/sounar/presets.toml").read_text())
    assert {"vocal", "club", "concert", "late-night", "tv", "party"} <= set(user)
    assert user["club"]["bass"] == 3.5 and user["club"]["layout"] == "room" and user["party"]["layout"] == "spread"
    assert load_presets(home / ".config/sounar/presets.toml")["club"].params["bass"] == 3.5
    h = [json.loads(line) for line in (home / ".local/state/sounar/history.jsonl").read_text().splitlines()]
    assert h[-1]["action"] == "import" and h[-1]["after"]["volumes"]["bluez_output.00_00_00_00_00_02.1"] == 54000
    backup = home / ".local/state/sounar/prototype-backup"
    assert (backup / "state.json").exists() and (backup / "presets.toml").exists()


def test_dry_run_writes_nothing(home):
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    r = run_import(home, "--dry-run")
    assert r.returncode == 0 and "setup.toml" in r.stdout and "front-left" in r.stdout
    assert {p: p.read_bytes() for p in home.rglob("*") if p.is_file()} == before


def test_into_other_dirs(home, tmp_path):
    out = tmp_path / "out"
    r = run_import(home, "--config", str(out / "config"), "--state", str(out / "state"))
    assert r.returncode == 0, r.stderr
    assert (out / "config/setup.toml").exists() and (out / "state/state.json").exists()
    assert json.loads((home / ".local/state/sounar/state.json").read_text())["sync_ms"] == {"box": 0.0, "twin": 17.9}


def test_already_imported_refuses(home):
    assert run_import(home).returncode == 0
    r = run_import(home)
    assert r.returncode == 1 and "already" in r.stdout
