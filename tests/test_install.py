"""install.sh, run in a throwaway home with stand-ins for every system command (no real
PipeWire, systemd or Omarchy is reachable from the PATH it gets)."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BASIC = ["sh", "cp", "mkdir", "rm", "rmdir", "cmp", "sed", "grep", "cat", "date", "mktemp", "dirname", "basename",
         "find", "chmod", "ln", "mv", "printf", "head", "tail", "tr", "cut", "env", "test", "touch", "ls", "sort",
         "uname", "readlink", "realpath", "wc", "sleep", "true", "false", "id", "tee", "awk", "diff", "stat"]
LOGGED = ["pw-cli", "pw-dump", "pw-play", "pw-record", "busctl", "notify-send", "easyeffects", "systemctl", "pactl"]


@pytest.fixture
def box(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "calls.log"
    for tool in BASIC:
        real = shutil.which(tool)
        if real:
            (bin_ / tool).symlink_to(real)
    (bin_ / "python3").symlink_to(sys.executable)
    for tool in LOGGED:
        f = bin_ / tool
        f.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\n'
                     + ('[ "$1" = get-default-sink ] && echo alsa_output.speaker\n' if tool == "pactl" else "")
                     + "exit 0\n")
        f.chmod(0o755)
    pw = bin_ / "pipewire"
    pw.write_text("#!/bin/sh\necho pipewire\necho 'Compiled with libpipewire 1.6.8'\n")
    pw.chmod(0o755)
    env = {"HOME": str(home), "PATH": str(bin_), "LANG": "C.UTF-8"}

    def run(*args, stdin=""):
        r = subprocess.run(["sh", str(REPO / "install.sh"), *args], env=env, input=stdin, capture_output=True,
                           text=True, timeout=60)
        return r

    run.home, run.bin, run.log, run.env = home, bin_, log, env
    return run


def calls(box):
    return box.log.read_text().splitlines() if box.log.exists() else []


def test_install_into_temp_home(box):
    ee = box.home / ".local/share/easyeffects/output"
    ee.mkdir(parents=True)
    (ee / "Sounar Room.json").write_text('{"output": {"mine": true}}')           # the user changed this one
    r = box("--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    dest = box.home / ".local/share/sounar"
    assert (dest / "sounar/cli.py").exists() and (dest / "run.py").exists() and (dest / "setups/quad.toml").exists()
    assert not list(dest.rglob("__pycache__"))
    wrapper = box.home / ".local/bin/sounar"
    assert os.access(wrapper, os.X_OK) and "sounar wrapper" in wrapper.read_text()
    shipped = REPO / "sounar/data/easyeffects/Sounar Default.json"
    assert (ee / "Sounar Default.json").read_text() == shipped.read_text()
    assert (ee / "Sounar Room.json").read_text() == '{"output": {"mine": true}}'
    assert "kept your changed Sounar Room" in r.stdout
    units = box.home / ".config/systemd/user"
    assert (units / "sounar-guard.service").exists() and (units / "sounar-keepalive.service").exists()
    assert "systemctl --user daemon-reload" in calls(box)
    assert not [c for c in calls(box) if "enable" in c or c.startswith("pactl set")]
    assert "sounar setup" in r.stdout


def test_wrapper_runs(box):
    assert box("--yes").returncode == 0
    r = subprocess.run([str(box.home / ".local/bin/sounar"), "--version"], env=box.env, capture_output=True, text=True)
    assert r.stdout.strip() == "sounar 0.1.0", r.stderr


def test_old_wrapper_backed_up(box):
    b = box.home / ".local/bin"
    b.mkdir(parents=True)
    (b / "sounar").write_text("#!/bin/sh\necho old\n")
    r = box("--yes")
    assert r.returncode == 0
    backups = list(b.glob("sounar.before-install*"))
    assert len(backups) == 1 and backups[0].read_text() == "#!/bin/sh\necho old\n"


def test_missing_requirement_stops(box):
    (box.bin / "pw-cli").unlink()
    r = box("--yes")
    assert r.returncode == 1 and "pw-cli" in r.stdout
    assert not (box.home / ".local/share/sounar").exists()


def test_old_pipewire_stops(box):
    (box.bin / "pipewire").write_text("#!/bin/sh\necho 'Compiled with libpipewire 1.2.7'\n")
    r = box("--yes")
    assert r.returncode == 1 and "1.4" in r.stdout


def test_uninstall_removes_files_keeps_config(box):
    ee = box.home / ".local/share/easyeffects/output"
    ee.mkdir(parents=True)
    assert box("--yes").returncode == 0
    (ee / "Sounar Late Night.json").write_text("{}")                              # changed after install
    cfg = box.home / ".config/sounar"
    cfg.mkdir(parents=True)
    (cfg / "setup.toml").write_text("version = 1\n")
    state = box.home / ".local/state/sounar"
    state.mkdir(parents=True)
    (state / "previous-default").write_text("alsa_output.speaker\n")
    pwc = box.home / ".config/pipewire/pipewire.conf.d"
    pwc.mkdir(parents=True)
    (pwc / "sounar.conf").write_text("x")
    (pwc / "mine.conf").write_text("y")
    wpc = box.home / ".config/wireplumber/wireplumber.conf.d"
    wpc.mkdir(parents=True)
    (wpc / "sounar-bluetooth.conf").write_text("x")
    r = box("--uninstall")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (box.home / ".local/share/sounar").exists() and not (box.home / ".local/bin/sounar").exists()
    assert not (box.home / ".config/systemd/user/sounar-guard.service").exists()
    assert (cfg / "setup.toml").exists()
    assert not (ee / "Sounar Default.json").exists() and (ee / "Sounar Late Night.json").exists()
    assert not (pwc / "sounar.conf").exists() and (pwc / "mine.conf").exists()
    assert not (wpc / "sounar-bluetooth.conf").exists()
    c = calls(box)
    assert "systemctl --user disable --now sounar-guard.service sounar-keepalive.service" in c
    assert "pactl set-default-sink alsa_output.speaker" in c


def test_purge_removes_config(box):
    assert box("--yes").returncode == 0
    cfg = box.home / ".config/sounar"
    cfg.mkdir(parents=True)
    (cfg / "setup.toml").write_text("version = 1\n")
    assert box("--uninstall", "--purge").returncode == 0
    assert not cfg.exists()


def test_omarchy_extras(box):
    omarchy = box.bin / "omarchy"
    omarchy.write_text(f'#!/bin/sh\necho "omarchy $*" >> "{box.log}"\n')
    omarchy.chmod(0o755)
    hypr = box.home / ".config/hypr"
    hypr.mkdir(parents=True)
    (hypr / "bindings.lua").write_text('o.bind("SUPER + RETURN", "Terminal", "terminal")\n')
    r = box("--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    plugin = box.home / ".config/omarchy/plugins/sounar.panel"
    assert (plugin / "Panel.qml").exists() and (plugin / "manifest.json").exists()
    assert "omarchy plugin enable sounar.panel" in calls(box)
    b = (hypr / "bindings.lua").read_text()
    assert b.count("-- sounar:begin") == 1 and '"SUPER + ALT + R"' in b and "sounar menu" in b
    assert box("--yes").returncode == 0                                       # again: still one block
    assert (hypr / "bindings.lua").read_text().count("-- sounar:begin") == 1
    assert box("--uninstall").returncode == 0
    assert not plugin.exists() and "sounar:begin" not in (hypr / "bindings.lua").read_text()


def test_existing_bindings_not_duplicated(box):
    omarchy = box.bin / "omarchy"
    omarchy.write_text("#!/bin/sh\nexit 0\n")
    omarchy.chmod(0o755)
    hypr = box.home / ".config/hypr"
    hypr.mkdir(parents=True)
    (hypr / "bindings.lua").write_text('o.bind("SUPER + ALT + R", "Mine", "something")\n')
    r = box("--yes")
    assert r.returncode == 0 and "already bound" in r.stdout
    assert "sounar:begin" not in (hypr / "bindings.lua").read_text()


def test_numpy_checked_the_way_sounar_runs(box):
    # numpy only reachable without -I (e.g. pip --user): the wrapper runs python3 -I, so install must refuse
    (box.bin / "python3").unlink()
    shim = box.bin / "python3"
    shim.write_text(f'#!/bin/sh\nfor a in "$@"; do [ "$a" = -I ] && iso=1; done\n'
                    f'case "$*" in *"import numpy"*) [ -n "$iso" ] && exit 1 ;; esac\nexec {sys.executable} "$@"\n')
    shim.chmod(0o755)
    r = box("--yes")
    assert r.returncode == 1 and "numpy" in r.stdout


def test_no_compiler_no_upmixer(box):
    r = box("--yes")
    assert r.returncode == 0 and "upmixer isn't built" in r.stdout
    assert not (box.home / ".local/lib/ladspa/sounar_upmix.so").exists()


def test_upmixer_built_installed_and_removed(box):
    make = box.bin / "make"
    make.write_text('#!/bin/sh\n: > "$3/$4"\n')                     # make -s -C DIR TARGET
    make.chmod(0o755)
    cc = box.bin / "cc"
    cc.write_text("#!/bin/sh\nexit 0\n")
    cc.chmod(0o755)
    r = box("--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    so = box.home / ".local/lib/ladspa/sounar_upmix.so"
    dropin = box.home / ".config/systemd/user/wireplumber.service.d/sounar-ladspa.conf"
    assert so.exists() and "LADSPA_PATH=%h/.local/lib/ladspa" in dropin.read_text()
    assert box("--uninstall").returncode == 0
    assert not so.exists() and not dropin.exists()

