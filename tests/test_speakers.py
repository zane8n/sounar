import pytest

from sounar import SounarError
from sounar.speakers import Output, Setup, Speaker, examples, load_setup, save_setup, standard_name


@pytest.mark.parametrize("hour,name,sid", [
    (12, "Centre", "centre"), (11, "Front left", "front-left"), (2, "Front right", "front-right"),
    (3, "Side right", "side-right"), (4, "Rear right", "rear-right"), (6, "Rear centre", "rear-centre"),
    (9, "Side left", "side-left"), (11.5, "Centre", "centre"), (0, "Centre", "centre"),
    (8, "Rear left", "rear-left"), (10, "Front left", "front-left"),
])
def test_names_table(hour, name, sid):
    assert standard_name(hour, False) == (name, sid)


def test_sub_name():
    assert standard_name(None, True) == ("Sub", "sub")


@pytest.mark.parametrize("hour,zone", [(2, "front"), (3, "surround"), (9, "surround"), (10, "front"),
                                       (12, "front"), (6, "surround"), (4, "surround")])
def test_zones(hour, zone):
    assert Speaker("x", ["FL"], hour).zone == zone


def test_sides():
    assert Speaker("a", ["FL"], 11).side == 1      # left
    assert Speaker("b", ["FL"], 2).side == -1      # right
    assert Speaker("c", ["FL"], 12).side == 0
    assert Speaker("d", ["FL"], 6).side == 0


def test_duplicate_ids(tmp_path):
    f = tmp_path / "s.toml"
    f.write_text('version = 1\n[[output]]\nnode = "a"\n[[output.speaker]]\nchannels = ["FL"]\nat = 10\n'
                 '[[output.speaker]]\nchannels = ["FR"]\nat = 10\n')
    s = load_setup(f)
    assert [sp.id for sp in s.speakers()] == ["front-left", "front-left-2"]


def _original():
    return Setup(outputs=[
        Output("bluez_output.00_00_00_00_00_01.1", [Speaker("front-left", ["FL", "FR"], 11, bass_boost=False)],
               correction=[{"type": "highpass", "freq": 50.0, "gain": 0.0, "q": 0.707}]),
        Output("bluez_output.00_00_00_00_00_02.1", [Speaker("front-right", ["FL"], 2),
                                                    Speaker("rear-right", ["FR"], 4, nickname="Shelf")]),
    ], mic="alsa_input.test")


def test_round_trip(tmp_path):
    s = _original()
    save_setup(s, tmp_path / "setup.toml")
    assert load_setup(tmp_path / "setup.toml") == s


def test_output_ids_and_channels():
    s = _original()
    assert [o.id for o in s.outputs] == ["front-left", "front-right"]
    assert s.outputs[0].channels == ["FL", "FR"]
    assert s.outputs[0].speakers[0].stereo_box


def test_examples_load():
    ex = examples()
    assert set(ex) == {"stereo-box-and-twin-right", "stereo-pair", "pair-plus-rear", "quad",
                       "left-centre-right", "pair-with-sub"}
    for path in ex.values():
        load_setup(path)


def test_original_example_positions():
    s = load_setup(examples()["stereo-box-and-twin-right"])
    assert [(sp.id, sp.at, sp.channels) for sp in s.speakers()] == [
        ("front-left", 11, ["FL", "FR"]), ("front-right", 2, ["FL"]), ("rear-right", 4, ["FR"])]
    assert s.speakers()[0].bass_boost is False


def test_bad_hour(tmp_path):
    f = tmp_path / "setup.toml"
    f.write_text('[[output]]\nnode = "a"\n[[output.speaker]]\nchannels = ["FL", "FR"]\nat = 13\n')
    with pytest.raises(SounarError, match=r"setup\.toml.*between 0 and 12"):
        load_setup(f)


@pytest.mark.parametrize("channels", [("MONO", "FL"), ("FR", "MONO")])
def test_mono_cannot_be_mixed_with_stereo_channels(tmp_path, channels):
    f = tmp_path / "setup.toml"
    speakers = "".join(
        f'[[output.speaker]]\nchannels = ["{channel}"]\nat = 12\n' for channel in channels
    )
    f.write_text('[[output]]\nnode = "a"\n' + speakers)
    with pytest.raises(SounarError, match=r"setup\.toml.*output 1.*MONO.*FL/FR"):
        load_setup(f)


def test_mono_only_is_valid(tmp_path):
    f = tmp_path / "setup.toml"
    f.write_text('[[output]]\nnode = "a"\n'
                 '[[output.speaker]]\nchannels = ["MONO"]\nat = 12\n')
    load_setup(f)


def test_missing_at(tmp_path):
    f = tmp_path / "setup.toml"
    f.write_text('[[output]]\nnode = "a"\n[[output.speaker]]\nchannels = ["FL", "FR"]\n')
    with pytest.raises(SounarError, match="where it sits"):
        load_setup(f)


def test_unknown_key(tmp_path):
    f = tmp_path / "setup.toml"
    f.write_text('[[output]]\nnode = "a"\n[[output.speaker]]\nchannels = ["FL"]\nat = 2\npostion = 3\n')
    with pytest.raises(SounarError, match="postion"):
        load_setup(f)


def test_broken_toml(tmp_path):
    f = tmp_path / "setup.toml"
    f.write_text("[[output]\n")
    with pytest.raises(SounarError, match="setup.toml"):
        load_setup(f)


def test_find_by_any_speaker():
    s = _original()
    assert s.find("rear-right").node.endswith("02.1")
    assert s.find("shelf").node.endswith("02.1")
    assert s.find("front-left").node.endswith("01.1")
    with pytest.raises(SounarError, match="front-left"):
        s.find("nothing")


def test_output_of():
    s = _original()
    assert s.output_of("rear-right") is s.outputs[1]
