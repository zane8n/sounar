import pytest

from sounar import SounarError
from sounar.presets import DEFAULTS, PARAMS, applicable, load_presets
from sounar.speakers import Output, Setup, Speaker, examples, load_setup


def test_builtin_order():
    assert list(load_presets(None)) == ["music-room", "immersive", "movie"]


def test_builtin_values():
    ps = load_presets(None)
    music, imm = ps["music-room"], ps["immersive"]
    assert music.layout == imm.layout == "room" and music.ee == "Sounar Room"
    assert music.params["amb_hi"] == DEFAULTS["room"]["amb_hi"]
    # Immersive is clearly different, not a variant: the ambience louder, later, wider
    assert imm.params["fill"] - music.params["fill"] >= 5 and imm.params["haas"] > music.params["haas"]
    assert imm.params["amb_hi"] > music.params["amb_hi"] and imm.ee == "Sounar Classical Room"
    assert ps["movie"].layout == "stereo" and ps["movie"].params == {} and ps["movie"].ee == "Sounar Movie Night"


def test_old_presets_come_back_as_a_user_file():
    from fakes import legacy_presets
    ps = legacy_presets()
    assert {"club", "party", "tv", "concert", "late-night", "vocal"} <= set(ps)
    assert ps["party"].layout == "spread" and ps["party"].params == {"width": 0.5}


def test_user_override_and_hidden(tmp_path):
    f = tmp_path / "presets.toml"
    f.write_text('[music-room]\nee = "My EQ"\nbass = 4\n[movie]\nhidden = true\n'
                 '[kitchen]\nlabel = "Kitchen"\nlayout = "spread"\nwidth = 0.2\n')
    ps = load_presets(f)
    assert ps["music-room"].ee == "My EQ" and ps["music-room"].params["bass"] == 4.0
    assert ps["music-room"].params["xover"] == 150.0
    assert "movie" not in ps
    assert list(ps)[-1] == "kitchen" and ps["kitchen"].params == {"width": 0.2}


def test_unknown_key_error(tmp_path):
    f = tmp_path / "presets.toml"
    f.write_text('[music-room]\nbas = 4\n')
    with pytest.raises(SounarError, match=r"presets\.toml.*music-room.*bas"):
        load_presets(f)


def test_value_out_of_range(tmp_path):
    f = tmp_path / "presets.toml"
    f.write_text('[music-room]\nbass = 40\n')
    with pytest.raises(SounarError, match="-6"):
        load_presets(f)


def test_param_table_complete():
    for layout, vals in DEFAULTS.items():
        for k in vals:
            p = PARAMS[k]
            assert p.lo <= vals[k] <= p.hi, (layout, k)


def _ex(name):
    return load_setup(examples()[name])


def test_applicable_stereo_pair_room():
    a = applicable(_ex("stereo-pair"), "room")
    assert "fill" not in a and "centre" not in a
    assert {"vocal", "xover", "bass"} <= set(a)


def test_applicable_original_room():
    assert applicable(_ex("stereo-box-and-twin-right"), "room") == [
        "vocal", "bass", "centre", "fill", "haas", "dip", "xover", "amb_lo", "amb_hi"]


def test_applicable_lcr_has_centre():
    assert "centre" in applicable(_ex("left-centre-right"), "room")


def test_applicable_no_bass_boost():
    s = Setup([Output("a", [Speaker("front-left", ["FL"], 10, bass_boost=False),
                            Speaker("front-right", ["FR"], 2, bass_boost=False)])])
    assert "bass" not in applicable(s, "room")


def test_applicable_spread_and_stereo():
    s = _ex("quad")
    assert applicable(s, "spread") == ["width"]
    assert applicable(s, "stereo") == []
