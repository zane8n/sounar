"""The speaker map (setup.toml): which outputs Sounar plays to, which speakers each one feeds,
and where every speaker sits, as an hour on a clock face around the seat (12 = straight
ahead, 3 = right, 6 = behind, 9 = left). Speakers are named by their position."""
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from sounar import SounarError

CHANNELS = ("FL", "FR", "MONO")
EQ_TYPES = ("lowpass", "highpass", "peaking", "lowshelf", "highshelf")


def angle_of(at):
    """Clock hour -> degrees in (-180, 180]; negative = left."""
    a = (float(at) % 12) * 30.0
    return a - 360.0 if a > 180.0 else a


def standard_name(at, sub):
    """(name, id) for a position, e.g. 2 -> ("Front right", "front-right")."""
    if sub:
        return "Sub", "sub"
    a = angle_of(at)
    side = "right" if a > 0 else "left"
    m = abs(a)
    if m <= 15:
        return "Centre", "centre"
    if m < 75:
        return f"Front {side}", f"front-{side}"
    if m <= 105:
        return f"Side {side}", f"side-{side}"
    if m < 165:
        return f"Rear {side}", f"rear-{side}"
    return "Rear centre", "rear-centre"


@dataclass
class Speaker:
    id: str
    channels: list
    at: float | None = None
    sub: bool = False
    bass_boost: bool = True
    nickname: str = ""

    @property
    def angle(self):
        return 0.0 if self.at is None else angle_of(self.at)

    @property
    def zone(self):
        if self.sub:
            return "sub"
        return "front" if abs(self.angle) < 90 else "surround"

    @property
    def side(self):
        """+1 left, -1 right, 0 straight ahead or straight behind."""
        m = abs(self.angle)
        if self.sub or m <= 15 or m >= 165:
            return 0
        return 1 if self.angle < 0 else -1

    @property
    def name(self):
        return standard_name(self.at, self.sub)[0]

    @property
    def stereo_box(self):
        return len(self.channels) == 2

    @property
    def label(self):
        return self.nickname or self.name


@dataclass
class Output:
    node: str
    speakers: list
    correction: list = field(default_factory=list)

    @property
    def reference(self):
        """The front-most speaker: sync lines outputs up by it."""
        return min(self.speakers, key=lambda s: 999.0 if s.sub else abs(s.angle))

    @property
    def id(self):
        return self.reference.id

    @property
    def channels(self):
        used = {c for s in self.speakers for c in s.channels}
        return [c for c in CHANNELS if c in used]

    @property
    def label(self):
        return " + ".join(s.label for s in self.speakers)


@dataclass
class Setup:
    outputs: list
    mic: str = ""
    combine_node: str = "sounar"
    combine_managed: bool = True
    menu: str = "auto"
    notify: bool = True

    def speakers(self):
        return [s for o in self.outputs for s in o.speakers]

    def output_of(self, speaker_id):
        for o in self.outputs:
            if any(s.id == speaker_id for s in o.speakers):
                return o
        raise SounarError(f"no speaker '{speaker_id}'")

    def find(self, query):
        """The output holding a speaker, by speaker id, output id or nickname."""
        q = query.lower().strip()
        for o in self.outputs:
            for s in o.speakers:
                if q in (s.id, s.nickname.lower()) or q == s.name.lower():
                    return o
        choices = ", ".join(s.id for s in self.speakers())
        raise SounarError(f"no speaker '{query}'; choose from: {choices}")


def anchor(front):
    """The front speaker that carries the vocals: the one nearest 12 o'clock. None when the
    nearest ones are a symmetric pair (same angle, opposite sides) or there are no front speakers."""
    if not front:
        return None
    m = min(abs(s.angle) for s in front)
    near = [s for s in front if abs(abs(s.angle) - m) < 1.0]
    centred = [s for s in near if s.side == 0]
    if centred:
        return centred[0]
    if {s.side for s in near} == {1, -1}:
        return None
    return near[0]


def assign_ids(speakers):
    """Give speakers without an id their standard one, numbering repeats (front-left-2)."""
    taken = {s.id for s in speakers if s.id}
    for s in speakers:
        if s.id:
            continue
        base = standard_name(s.at, s.sub)[1]
        sid, n = base, 2
        while sid in taken:
            sid, n = f"{base}-{n}", n + 1
        s.id = sid
        taken.add(sid)


# ---------------------------------------------------------------- reading

def _check_keys(path, table, known, where):
    for k in table:
        if k not in known:
            raise SounarError(f"{path}: unknown setting '{k}' in {where} (known: {', '.join(known)})")


def _speaker(path, raw, where):
    _check_keys(path, raw, ("id", "channels", "at", "sub", "bass_boost", "nickname"), where)
    label = raw.get("id") or where
    ch = raw.get("channels")
    if not isinstance(ch, list) or not ch or any(c not in CHANNELS for c in ch) or len(set(ch)) != len(ch):
        raise SounarError(f"{path}: speaker {label} needs `channels`, a list of {', '.join(CHANNELS)}")
    sub = bool(raw.get("sub", False))
    at = raw.get("at")
    if at is None and not sub:
        raise SounarError(f"{path}: speaker {label} needs `at` (where it sits, as an hour on a clock face: "
                          f"12 = straight ahead, 3 = your right) or `sub = true`")
    if at is not None:
        if not isinstance(at, (int, float)) or isinstance(at, bool) or not 0 <= at <= 12:
            raise SounarError(f"{path}: speaker {label} is at {at!r}; positions are hours between 0 and 12 "
                              f"(12 = straight ahead)")
        at = float(at)
    return Speaker(raw.get("id", ""), list(ch), at, sub, bool(raw.get("bass_boost", True)),
                   str(raw.get("nickname", "")))


def _correction(path, items, where):
    out = []
    for f in items or []:
        _check_keys(path, f, ("type", "freq", "gain", "q"), f"{where} correction")
        if f.get("type") not in EQ_TYPES or not isinstance(f.get("freq"), (int, float)):
            raise SounarError(f"{path}: each correction filter needs a type ({', '.join(EQ_TYPES)}) and a freq")
        out.append({"type": f["type"], "freq": float(f["freq"]), "gain": float(f.get("gain", 0.0)),
                    "q": float(f.get("q", 0.707))})
    return out


def load_setup(path) -> Setup:
    path = Path(path)
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise SounarError(f"{path}: {e}") from None
    except OSError:
        raise SounarError(f"no setup yet ({path}); run `sounar setup`") from None
    _check_keys(path, raw, ("version", "mic", "combine", "options", "output"), "the file")
    if raw.get("version", 1) != 1:
        raise SounarError(f"{path}: version {raw['version']} is newer than this Sounar understands")
    outputs = []
    for i, o in enumerate(raw.get("output", []), 1):
        where = f"output {i}"
        _check_keys(path, o, ("node", "correction", "speaker"), where)
        spk = [_speaker(path, s, f"speaker {j} of {where}") for j, s in enumerate(o.get("speaker", []), 1)]
        if not spk:
            raise SounarError(f"{path}: {where} has no [[output.speaker]]")
        used = [c for s in spk for c in s.channels]
        if "MONO" in used and ("FL" in used or "FR" in used):
            raise SounarError(f"{path}: {where} cannot mix MONO with FL/FR channels")
        if len(used) != len(set(used)):
            raise SounarError(f"{path}: two speakers of {where} use the same channel")
        outputs.append(Output(str(o.get("node", "")), spk, _correction(path, o.get("correction"), where)))
    if not outputs:
        raise SounarError(f"{path}: no [[output]] in the setup")
    nodes = [o.node for o in outputs if o.node]
    if len(nodes) != len(set(nodes)):
        raise SounarError(f"{path}: the same output is listed twice")
    allspk = [s for o in outputs for s in o.speakers]
    assign_ids(allspk)
    ids = [s.id for s in allspk]
    if len(ids) != len(set(ids)):
        raise SounarError(f"{path}: two speakers have the same id")
    comb, opts = raw.get("combine", {}), raw.get("options", {})
    _check_keys(path, comb, ("node", "managed"), "[combine]")
    _check_keys(path, opts, ("menu", "notify"), "[options]")
    return Setup(outputs, str(raw.get("mic", "")), str(comb.get("node", "sounar")), bool(comb.get("managed", True)),
                 str(opts.get("menu", "auto")), bool(opts.get("notify", True)))


# ---------------------------------------------------------------- writing

HEADER = """\
# Sounar speaker setup, written by `sounar setup` (safe to edit by hand).
# Positions are hours on a clock face around your seat:
# 12 = straight ahead, 3 = your right, 6 = behind you, 9 = your left.
"""


def _s(v):
    return json.dumps(v, ensure_ascii=False)


def _n(v):
    return f"{v:g}"


def dumps(setup: Setup) -> str:
    out = [HEADER, "version = 1", f"mic = {_s(setup.mic)}", "",
           "[combine]", f"node = {_s(setup.combine_node)}", f"managed = {str(setup.combine_managed).lower()}", "",
           "[options]", f"menu = {_s(setup.menu)}", f"notify = {str(setup.notify).lower()}"]
    for o in setup.outputs:
        out += ["", "[[output]]", f"node = {_s(o.node)}"]
        if o.correction:
            out.append("correction = [")
            out += [f'  {{ type = {_s(f["type"])}, freq = {_n(f["freq"])}, gain = {_n(f["gain"])}, '
                    f'q = {_n(f["q"])} }},' for f in o.correction]
            out.append("]")
        for s in o.speakers:
            out += ["", "  [[output.speaker]]", f"  id = {_s(s.id)}",
                    "  channels = [" + ", ".join(_s(c) for c in s.channels) + "]"]
            if s.sub:
                out.append("  sub = true")
            if s.at is not None:
                out.append(f"  at = {_n(s.at)}")
            if not s.bass_boost:
                out.append("  bass_boost = false")
            if s.nickname:
                out.append(f"  nickname = {_s(s.nickname)}")
    return "\n".join(out) + "\n"


def save_setup(setup: Setup, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dumps(setup))
    tmp.replace(path)


def examples() -> dict:
    """The example setups shipped in setups/: name -> path."""
    d = Path(__file__).resolve().parent.parent / "setups"
    return {p.stem: p for p in sorted(d.glob("*.toml"))}
