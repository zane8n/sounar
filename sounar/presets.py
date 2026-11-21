"""Presets: a layout, its values, and the Easy Effects preset that goes with it. Built-in ones
ship in data/presets.toml; ~/.config/sounar/presets.toml adds presets or overrides fields of
built-in ones by id (`hidden = true` hides one)."""
import tomllib
from dataclasses import dataclass
from pathlib import Path

from sounar import SounarError, paths
from sounar.speakers import anchor

LAYOUTS = ("stereo", "room", "spread")


@dataclass(frozen=True)
class Param:
    label: str
    unit: str
    step: float
    advanced: bool
    hint: str
    lo: float
    hi: float


# What each value means, its limits, and how the panel shows it (slider step, behind "More").
PARAMS = {
    "vocal": Param("Vocal presence", "dB", 0.5, False, "Lifts the voice band (900 Hz-2.5 kHz) in the centre only",
                   -3, 6),
    "bass": Param("Bass", "dB", 0.5, False, "Bass of the speakers that may be boosted; 0 = the same bass as stereo",
                  -6, 6),
    "centre": Param("Centre focus", "dB", 0.5, False,
                    "How much more of the vocals the speaker you face carries than the other front speakers", 0, 15),
    "fill": Param("Room fill", "dB", 0.5, False,
                  "Ambience from beside and behind you, as loud as it reaches your seat", -24, 0),
    "haas": Param("Ambience delay", "ms", 1.0, False,
                  "How much later the ambience arrives: more feels like a bigger room", 5, 30),
    "width": Param("Stereo width", "", 0.05, False, "0 = the whole song from every speaker, 1 = full stereo", 0, 1),
    "dip": Param("Ambience vocal cut", "dB", 0.5, True, "Cut around 2.5 kHz so vocal echo doesn't come from behind",
                 -12, 0),
    "xover": Param("Bass split", "Hz", 10.0, True,
                   "Below this every speaker plays the same bass, lined up at your seat", 80, 250),
    "amb_lo": Param("Ambience low cut", "Hz", 10.0, True, "The ambience starts here", 120, 600),
    "amb_hi": Param("Ambience high cut", "Hz", 500.0, True, "and stops here", 5000, 16000),
    "place": Param("Placement", "", 0.05, False,
                   "How much each sound comes from its own direction: 0 = the plain room mix", 0, 1),
    "wrap": Param("Wrap", "", 0.05, True,
                  "How far sounds panned to one side reach towards a speaker beside or behind", 0, 1),
}

DEFAULTS = {
    "room": {"centre": 6.0, "fill": -9.0, "haas": 12.0, "vocal": 1.0, "bass": 1.0,
             "xover": 150.0, "amb_lo": 250.0, "amb_hi": 11000.0, "dip": -5.0, "place": 0.0, "wrap": 0.15},
    "spread": {"width": 0.5},
    "stereo": {},
}

INFO_KEYS = ("label", "tagline", "icon", "layout", "ee", "hidden")


@dataclass
class Preset:
    id: str
    label: str
    tagline: str
    icon: str
    layout: str
    ee: str | None
    params: dict


def _read(path):
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise SounarError(f"{path}: {e}") from None


def _build(path, pid, raw):
    layout = raw.get("layout", "stereo")
    if layout not in LAYOUTS:
        raise SounarError(f"{path}: preset '{pid}' has layout '{layout}'; choose from {', '.join(LAYOUTS)}")
    params = dict(DEFAULTS[layout])
    for k, v in raw.items():
        if k in INFO_KEYS:
            continue
        if k not in PARAMS:
            raise SounarError(f"{path}: preset '{pid}' has an unknown setting '{k}'")
        if k not in params:
            raise SounarError(f"{path}: preset '{pid}': '{k}' doesn't apply to the {layout} layout")
        p = PARAMS[k]
        if not isinstance(v, (int, float)) or not p.lo <= v <= p.hi:
            raise SounarError(f"{path}: preset '{pid}': {k} = {v!r} is outside {p.lo:g} to {p.hi:g}")
        params[k] = float(v)
    return Preset(pid, raw.get("label", pid), raw.get("tagline", ""), raw.get("icon", "󰓃"), layout,
                  raw.get("ee"), params)


def load_presets(user_path: Path | None = None) -> dict:
    builtin = paths.package_data() / "presets.toml"
    raw = {pid: dict(p) for pid, p in _read(builtin).items()}
    src = {pid: builtin for pid in raw}
    if user_path is not None and Path(user_path).exists():
        for pid, p in _read(user_path).items():
            if not isinstance(p, dict):
                raise SounarError(f"{user_path}: '{pid}' should be a [section]")
            raw.setdefault(pid, {}).update(p)
            src[pid] = user_path
    out = {}
    for pid, p in raw.items():
        if p.get("hidden"):
            continue
        out[pid] = _build(src[pid], pid, p)
    if not out:
        raise SounarError("every preset is hidden")
    return out


def applicable(setup, layout) -> list:
    """The values that do something on this setup in this layout, in PARAMS order."""
    if layout == "spread":
        return ["width"]
    if layout != "room":
        return []
    spk = setup.speakers()
    front = [s for s in spk if s.zone == "front"]
    has = set(DEFAULTS["room"])
    if anchor(front) is None or len(front) < 2:
        has.discard("centre")
    if not any(s.zone == "surround" for s in spk):
        has -= {"fill", "haas", "dip", "amb_lo", "amb_hi"}
    if not any(s.bass_boost for s in spk):
        has.discard("bass")
    if not paths.upmix_available():
        has -= {"place", "wrap"}
    return [k for k in PARAMS if k in has]
