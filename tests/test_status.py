import json
import os
import re

import pytest

from fakes import FakeEffects, original_room_system, bt_names, legacy_presets
from sounar import core, doctor
from sounar.speakers import examples, load_setup
from sounar.state import log
from sounar.status import status_json, status_text

TOP = {"version", "setup", "preset", "previous", "label", "tagline", "icon", "layout", "live_layout", "note",
       "applied", "measuring", "presets", "params", "tweaked", "effects", "loudness_db", "outputs", "sync_text",
       "calibration", "files"}


@pytest.fixture
def ctx(tmp_home):
    pw = original_room_system()
    s = load_setup(examples()["stereo-box-and-twin-right"])
    s.outputs[0].node, s.outputs[1].node = bt_names(pw)
    s.outputs[1].speakers[1].nickname = "Bookshelf"
    return core.Ctx(s, pw, FakeEffects(), legacy_presets(), say=lambda *_: None, settle=0)


def test_json_contract_keys(ctx):
    core.switch(ctx, "music-room")
    j = status_json(ctx)
    assert TOP <= set(j) and j["version"] == 1
    assert j["setup"]["done"] is True and j["applied"] is True
    assert {"available", "preset", "preset_default", "live", "presets", "bypass", "leveling", "target",
            "target_live"} <= set(j["effects"])
    json.dumps(j)                                                    # serialisable


def test_json_without_setup(tmp_home):
    c = core.Ctx(None, original_room_system(), FakeEffects(), legacy_presets(), say=lambda *_: None)
    j = status_json(c)
    assert j["setup"]["done"] is False and j["outputs"] == [] and j["params"] == []
    assert TOP <= set(j) and len(j["presets"]) == 9


def test_params_only_applicable(ctx):
    ctx.setup.outputs[1].speakers.pop()
    core.switch(ctx, "music-room")
    keys = [p["key"] for p in status_json(ctx)["params"]]
    assert "fill" not in keys and "vocal" in keys
    p = next(p for p in status_json(ctx)["params"] if p["key"] == "vocal")
    assert p["min"] == -3 and p["max"] == 6 and p["unit"] == "dB" and p["value"] == p["preset"] == 1.0


def test_outputs_section(ctx):
    core.switch(ctx, "music-room")
    outs = status_json(ctx)["outputs"]
    assert [o["id"] for o in outs] == ["front-left", "front-right"]
    assert outs[1]["connected"] is True and outs[0]["codec"] == "SBC" and outs[1]["codec"] == "APTX"
    sp = outs[1]["speakers"]
    assert [(x["id"], x["at"], x["zone"]) for x in sp] == [("front-right", 2.0, "front"),
                                                          ("rear-right", 4.0, "surround")]
    assert sp[1]["nickname"] == "Bookshelf" and "delay_ms" in sp[1]


def test_disconnected_output(ctx):
    ctx.pw.remove_sink(ctx.setup.outputs[1].node)
    j = status_json(ctx)
    assert j["outputs"][1]["connected"] is False and j["live_layout"] == "stereo" and "missing" in j["note"]


def test_status_text_mentions_preset(ctx):
    core.switch(ctx, "club")
    t = status_text(ctx)
    assert "Club" in t and "Front right" in t and "Bookshelf" in t


def test_doctor_strips_addresses_and_nicknames(ctx):
    core.switch(ctx, "club")
    log(f"something about {ctx.setup.outputs[0].node} in {os.path.expanduser('~')}/x")
    r = doctor.report(ctx)
    assert not re.search(r"[0-9A-Fa-f]{2}([:_][0-9A-Fa-f]{2}){5}", r)
    assert "Bookshelf" not in r and os.path.expanduser("~") not in r
    assert "front-left @ 11" in r and "Club" in r
