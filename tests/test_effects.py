import json

from sounar import paths
from sounar.effects import AUTOGAIN_CEILING, Effects, autogain_target, side_gain

SHIPPED = ["Sounar Classical", "Sounar Classical Room", "Sounar Default", "Sounar Late Night",
           "Sounar Movie Night", "Sounar Room"]


def shipped(name):
    return json.loads((paths.package_data() / "easyeffects" / f"{name}.json").read_text())["output"]


def test_shipped_presets_exist():
    assert sorted(p.stem for p in (paths.package_data() / "easyeffects").glob("*.json")) == SHIPPED
    for name in SHIPPED:
        assert "autogain#0" in shipped(name)


def test_side_gain():
    assert side_gain(shipped("Sounar Default")) == 1.7
    assert side_gain({}) == 1.0
    assert side_gain({"stereo_tools#0": {"stereo-base": 0.5, "bypass": True}}) == 1.0


def test_autogain_target_clamped():
    assert autogain_target({"autogain#0": {"target": -14}}, -8.0) == AUTOGAIN_CEILING == -10
    assert autogain_target({"autogain#0": {"target": -14}}, 1.4) == -15
    assert autogain_target({"autogain#0": {"target": -14, "bypass": True}}, 0) is None
    assert autogain_target({}, 0) is None


def test_unavailable_degrades(tmp_path):
    calls = []
    ee = Effects(which=lambda _: None, socket_path=tmp_path / "nothing", preset_dir=tmp_path,
                 run=lambda *a, **k: calls.append(a))
    assert not ee.available()
    assert ee.current() is None and ee.bypass() is None and ee.target_live() is None
    ee.load("Sounar Default")
    ee.set_target(-12)
    assert calls == []


def _dir_with(tmp_path, names):
    for n in names:
        (tmp_path / f"{n}.json").write_text(json.dumps({"output": {"autogain#0": {"bypass": False, "target": -14}}}))
    return tmp_path


def test_presets_lists_dir(tmp_path):
    ee = Effects(which=lambda _: None, socket_path=tmp_path / "x", preset_dir=_dir_with(tmp_path, ["B", "A"]))
    assert ee.presets() == ["A", "B"]
    assert ee.data("A")["autogain#0"]["target"] == -14
    assert ee.data("missing") == {}


def test_set_leveling_only_named_files(tmp_path):
    d = _dir_with(tmp_path, ["A", "B"])
    ee = Effects(which=lambda _: None, socket_path=tmp_path / "x", preset_dir=d)
    ee.set_leveling(False, ["A"])
    assert json.loads((d / "A.json").read_text())["output"]["autogain#0"]["bypass"] is True
    assert json.loads((d / "B.json").read_text())["output"]["autogain#0"]["bypass"] is False
    assert ee.leveling("A") is False and ee.leveling("B") is True


def test_dry_run_changes_nothing(tmp_path):
    d = _dir_with(tmp_path, ["A"])
    said = []
    ee = Effects(which=lambda _: "/usr/bin/easyeffects", socket_path=tmp_path / "x", preset_dir=d,
                 run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")), dry_run=True, say=said.append)
    ee.load("A")
    ee.set_bypass(True)
    ee.set_leveling(False, ["A"])
    assert json.loads((d / "A.json").read_text())["output"]["autogain#0"]["bypass"] is False
    assert len(said) == 3 and all(s.startswith("would:") for s in said)


def test_reads_dont_start_effects_when_not_running(tmp_path):
    ran = []

    def run(args, **kw):
        ran.append(args)
        import subprocess
        return subprocess.CompletedProcess(args, 0 if args[0] != "pgrep" else 1, "", "")
    ee = Effects(which=lambda n: f"/usr/bin/{n}", socket_path=tmp_path / "x", preset_dir=tmp_path, run=run)
    assert ee.current() is None and ee.bypass() is None
    assert all(a[0] == "pgrep" for a in ran)                  # never `easyeffects ...` while it's not running


def test_reads_use_the_socket_not_the_command_line(tmp_path):
    # `easyeffects -a/--bypass` needs a display; speaker-guard starts before Hyprland has one
    import socket
    import threading
    path = tmp_path / "ee.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen()
    answers = {"get_last_loaded_preset:output": "Sounar Room", "get_global_bypass": "2"}
    asked = []

    def serve():
        for _ in answers:
            c, _addr = srv.accept()
            with c:
                q = c.recv(4096).decode().strip()
                asked.append(q)
                c.sendall((answers[q] + "\n").encode())
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    ee = Effects(which=lambda n: f"/usr/bin/{n}", socket_path=path, preset_dir=tmp_path,
                 run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    assert ee.current() == "Sounar Room" and ee.bypass() is False
    t.join(2)
    srv.close()
    assert asked == ["get_last_loaded_preset:output", "get_global_bypass"]
