from fakes import legacy_presets
import os
import threading
import time

import pytest

from sounar import SounarError, paths
from sounar.state import (Lock, Measuring, effective, history, history_append, load_state, log, measuring,
                          save_state, set_tweak)


@pytest.fixture
def ps():
    return legacy_presets()


def test_defaults(tmp_home, ps):
    st = load_state(ps)
    assert st["preset"] == "music-room" and st["previous"] is None
    assert st["tweaks"] == {} and st["sync_ms"] == {}
    assert st["calibration"] == {"levels": {}, "offsets_ms": {}, "source": "not measured", "ts": None}


def test_save_and_reload(tmp_home, ps):
    st = load_state(ps)
    st["preset"] = "club"
    save_state(st)
    assert load_state(ps)["preset"] == "club"


def test_current_file_written(tmp_home, ps):
    st = load_state(ps)
    st["preset"] = "party"
    save_state(st)
    assert (paths.state_dir() / "current").read_text() == "party\n"


def test_effective_applies_tweaks(tmp_home, ps):
    st = load_state(ps)
    set_tweak(st, "club", "bass", 4.0, ps["club"].params["bass"])
    set_tweak(st, "club", "ee", "My EQ", ps["club"].ee)
    e = effective(st, ps, "club")
    assert e.params["bass"] == 4.0 and e.base["bass"] == 3.0
    assert e.ee == "My EQ" and e.base_ee == "Sounar Room"


def test_tweak_equal_to_base_is_dropped(tmp_home, ps):
    st = load_state(ps)
    set_tweak(st, "club", "bass", 4.0, 3.0)
    set_tweak(st, "club", "bass", 3.0, 3.0)
    assert st["tweaks"] == {}


def test_unknown_preset_falls_back(tmp_home, ps):
    st = load_state(ps)
    st["preset"] = "gone"
    assert effective(st, ps).id == "music-room"


def test_lock_refuses_during_measuring(tmp_home):
    with Measuring("sync"):
        assert measuring() == "sync"
        with pytest.raises(SounarError, match="being measured"):
            with Lock():
                pass
        with Lock(allow_measuring=True):
            pass
    assert measuring() is None


def test_stale_marker_ignored(tmp_home):
    paths.state_dir().mkdir(parents=True, exist_ok=True)
    (paths.state_dir() / "measuring").write_text('{"kind": "calibrate", "pid": 999999}')
    assert measuring() is None
    with Lock():
        pass


def test_lock_serialises(tmp_home):
    order = []

    def hold():
        with Lock():
            order.append("a in")
            time.sleep(0.3)
            order.append("a out")

    t = threading.Thread(target=hold)
    t.start()
    time.sleep(0.05)
    with Lock():
        order.append("b in")
    t.join()
    assert order == ["a in", "a out", "b in"]


def test_history_round_trip(tmp_home):
    history_append("balance", {"volumes": {"x": 1}}, {"volumes": {"x": 2}}, "x louder")
    h = history()
    assert len(h) == 1 and h[0]["action"] == "balance" and h[0]["after"] == {"volumes": {"x": 2}}
    assert "ts" in h[0]


def test_log_appends(tmp_home):
    log("hello")
    log("again")
    lines = (paths.state_dir() / "log").read_text().splitlines()
    assert len(lines) == 2 and lines[1].endswith("again")


def test_measuring_marker_has_pid(tmp_home):
    with Measuring("calibrate"):
        assert str(os.getpid()) in (paths.state_dir() / "measuring").read_text()
