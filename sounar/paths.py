"""Where Sounar keeps things. XDG locations, each overridable for tests and dry runs:
SOUNAR_CONFIG_DIR, SOUNAR_STATE_DIR, SOUNAR_CACHE_DIR, SOUNAR_DATA_DIR."""
import os
from pathlib import Path


def _home():
    return Path(os.environ.get("HOME") or os.path.expanduser("~"))


def _dir(own, xdg, default):
    if os.environ.get(own):
        return Path(os.environ[own])
    if os.environ.get(xdg):
        return Path(os.environ[xdg]) / "sounar"
    return _home() / default / "sounar"


def config_dir() -> Path:
    return _dir("SOUNAR_CONFIG_DIR", "XDG_CONFIG_HOME", ".config")


def state_dir() -> Path:
    return _dir("SOUNAR_STATE_DIR", "XDG_STATE_HOME", ".local/state")


def cache_dir() -> Path:
    return _dir("SOUNAR_CACHE_DIR", "XDG_CACHE_HOME", ".cache")


def data_dir() -> Path:
    """Where the installer puts the program (~/.local/share/sounar)."""
    return _dir("SOUNAR_DATA_DIR", "XDG_DATA_HOME", ".local/share")


def xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or _home() / ".config")


def xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or _home() / ".local/share")


def package_data() -> Path:
    """Data shipped with the program: presets, Easy Effects presets, service files."""
    return Path(__file__).resolve().parent / "data"


def ladspa_dir() -> Path:
    """Where install.sh puts the upmix plugin; WirePlumber finds it through LADSPA_PATH (a
    drop-in for wireplumber.service). SOUNAR_LADSPA_DIR overrides it."""
    d = os.environ.get("SOUNAR_LADSPA_DIR")
    return Path(d) if d else Path.home() / ".local/lib/ladspa"


def upmix_available() -> bool:
    return (ladspa_dir() / "sounar_upmix.so").exists()


def setup_file() -> Path:
    return config_dir() / "setup.toml"


def user_presets_file() -> Path:
    return config_dir() / "presets.toml"


def dry_run() -> bool:
    """SOUNAR_DRY_RUN=1: print every change instead of making it."""
    return os.environ.get("SOUNAR_DRY_RUN", "") not in ("", "0")
