import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Every test gets its own config, state, cache and XDG folders, so no test can touch the
    real ones (Sounar writes Omarchy menu rows and PipeWire config under XDG_CONFIG_HOME)."""
    for name in ("config", "state", "cache"):
        d = tmp_path / name
        d.mkdir()
        monkeypatch.setenv(f"SOUNAR_{name.upper()}_DIR", str(d))
    for xdg in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        d = tmp_path / xdg.lower()
        d.mkdir()
        monkeypatch.setenv(xdg, str(d))
    monkeypatch.setenv("SOUNAR_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "ladspa").mkdir()
    monkeypatch.setenv("SOUNAR_LADSPA_DIR", str(tmp_path / "ladspa"))     # no upmix plugin unless a test adds one
    monkeypatch.delenv("SOUNAR_DRY_RUN", raising=False)


@pytest.fixture
def tmp_home(tmp_path):
    return tmp_path
