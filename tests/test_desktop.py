from sounar import desktop
from sounar.presets import load_presets


def only(*names):
    return lambda n: f"/usr/bin/{n}" if n in names else None


def test_launcher_order():
    assert desktop.launcher("auto", only("fuzzel", "dmenu")) == ["fuzzel", "--dmenu", "--prompt", "Sounar "]
    assert desktop.launcher("auto", only("omarchy-menu", "fuzzel"))[0] == "omarchy-menu"
    assert desktop.launcher("auto", only("rofi"))[:2] == ["rofi", "-dmenu"]
    assert desktop.launcher("auto", only()) is None
    assert desktop.launcher("wofi", only("fuzzel")) is None                  # asked for one that isn't there
    assert desktop.launcher("terminal", only("fuzzel")) is None


def test_menu_lines_mark_current():
    lines, actions = desktop.menu_lines(load_presets(None), "immersive")
    assert any(line.startswith("✓") and "Immersive" in line for line in lines)
    assert actions[lines.index(next(x for x in lines if "Movie" in x))] == ("set", "movie")
    assert ("sync",) in actions and ("calibrate",) in actions


def test_omarchy_block_replaced(tmp_path):
    f = tmp_path / "omarchy-menu.jsonc"
    f.write_text('{\n  "mine": {"label": "Mine"},\n  // sounar:begin old\n  "sounar": {},\n  // sounar:end\n}\n')
    desktop.write_omarchy_menu(f, load_presets(None))
    txt = f.read_text()
    assert '"mine": {"label": "Mine"}' in txt and txt.count("sounar:begin") == 1
    assert '"sounar.immersive"' in txt and "sounar set immersive" in txt and txt.rstrip().endswith("}")


def test_omarchy_menu_created_when_missing(tmp_path):
    f = tmp_path / "ext" / "omarchy-menu.jsonc"
    desktop.write_omarchy_menu(f, load_presets(None))
    assert '"sounar.movie"' in f.read_text()
