"""Easy Effects, the taste layer (EQ, widening, leveling) in front of the speakers. Optional:
without it presets still switch layout, balance and delay. Live changes go through Easy Effects'
local socket (Easy Effects 8); older versions only get presets loaded by name."""
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

from sounar import paths
from sounar.layouts import REF_SIDE_GAIN  # noqa: F401  (the loudness reference, re-exported)

AUTOGAIN_CEILING = -10           # never level louder than this (LUFS)


def side_gain(data) -> float:
    """How much a preset's Stereo Tools widen the side signal (1 = not at all)."""
    st = data.get("stereo_tools#0", {})
    return 1 + 2 * st.get("stereo-base", 0.0) if st and not st.get("bypass") else 1.0


def autogain_target(data, loudness) -> int | None:
    """The leveling target that makes this preset as loud as the reference, or None without Autogain."""
    ag = data.get("autogain#0")
    if not ag or ag.get("bypass"):
        return None
    return int(round(min(AUTOGAIN_CEILING, ag.get("target", -14) - loudness)))


def preset_dirs():
    home = Path(os.environ.get("HOME") or os.path.expanduser("~"))
    return [paths.xdg_data_home() / "easyeffects/output", paths.xdg_config_home() / "easyeffects/output",
            home / ".var/app/com.github.wwmm.easyeffects/data/easyeffects/output"]


class Effects:
    def __init__(self, dry_run=False, say=print, which=shutil.which, run=subprocess.run,
                 socket_path=None, preset_dir=None):
        self.dry_run, self.say, self.which, self.run = dry_run, say, which, run
        rt = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        self.socket_path = Path(socket_path or Path(rt) / "EasyEffectsServer")
        self._dir = Path(preset_dir) if preset_dir else None

    # ------------------------------------------------------------ plumbing
    def _cli(self, *args):
        if not self.which("easyeffects"):
            return None
        r = self.run(["easyeffects", *args], capture_output=True, text=True, check=False)
        return r.stdout.strip()

    def _send(self, cmd, reply=False):
        if not self.socket_path.exists():
            return None
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.5)
        try:
            s.connect(str(self.socket_path))
            s.sendall((cmd + "\n").encode())
            return s.recv(4096).decode().strip() if reply else ""
        except OSError:
            return None
        finally:
            s.close()

    def _would(self, what):
        self.say(f"would: {what}")

    # ------------------------------------------------------------ reading
    def available(self) -> bool:
        return bool(self.which("easyeffects")) or self.socket_path.exists()

    def running(self) -> bool:
        r = self.run(["pgrep", "-x", "easyeffects"], capture_output=True, text=True, check=False)
        return bool((r.stdout or "").strip())

    def preset_dir(self) -> Path:
        if self._dir:
            return self._dir
        dirs = preset_dirs()
        return next((d for d in dirs if d.is_dir()), dirs[0])

    def presets(self) -> list:
        return sorted(p.stem for p in self.preset_dir().glob("*.json"))

    def data(self, name) -> dict:
        try:
            return json.loads((self.preset_dir() / f"{name}.json").read_text())["output"]
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    # Reads go straight to the socket. `easyeffects -a/--bypass 3` send these same requests, but
    # the command line needs a display (it aborts without one) and starts Easy Effects if it's down.
    def current(self) -> str | None:
        return self._send("get_last_loaded_preset:output", reply=True) or None

    def bypass(self) -> bool | None:
        v = self._send("get_global_bypass", reply=True)
        return None if not v else v == "1"

    def leveling(self, name) -> bool:
        ag = self.data(name).get("autogain#0")
        return bool(ag) and not ag.get("bypass", False)

    def target_live(self) -> int | None:
        v = self._send("get_property:output:autogain:0:target", reply=True)
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------ changing
    def load(self, name) -> None:
        if not self.available():
            return
        if self.dry_run:
            return self._would(f"load Easy Effects preset {name!r}")
        if self._send(f"load_preset:output:{name}") is None:
            self._cli("-l", name)

    def set_bypass(self, on: bool) -> None:
        if not self.available():
            return
        if self.dry_run:
            return self._would(f"Easy Effects {'off (bypass)' if on else 'on'}")
        self._cli("-b", "1" if on else "2")

    def set_target(self, target: int) -> None:
        if self.dry_run:
            return self._would(f"leveling target {target} LUFS")
        self._send(f"set_property:output:autogain:0:target:{target}")

    def set_leveling(self, on: bool, names) -> None:
        """Song leveling (Autogain) on or off in these presets, then reload the active one."""
        if self.dry_run:
            return self._would(f"leveling {'on' if on else 'off'} in {', '.join(names)}")
        for n in names:
            f = self.preset_dir() / f"{n}.json"
            try:
                d = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if "autogain#0" not in d.get("output", {}):
                continue
            d["output"]["autogain#0"]["bypass"] = not on
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, indent=4))
            tmp.replace(f)
        cur = self.current()
        if cur:
            self.load(cur)
