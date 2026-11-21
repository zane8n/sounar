"""Everything Sounar asks of PipeWire goes through here, using its command-line tools (pactl,
pw-dump, pw-cli, pw-link, pw-play, pw-record). With dry_run every change is printed
("would: ...") instead of made; reading still happens, so a dry run shows real decisions."""
import json
import re
import shlex
import subprocess
from dataclasses import dataclass

import numpy as np

CHANNEL = {"front-left": "FL", "front-right": "FR", "mono": "MONO"}


def raw_to_db(raw):
    lin = (raw / 65536) ** 3
    return float(20 * np.log10(lin)) if lin > 0 else -120.0


def db_to_raw(db):
    return int(round(65536 * (10 ** (db / 20)) ** (1 / 3)))


def pct(db):
    return round(100 * (10 ** (db / 20)) ** (1 / 3))


@dataclass
class Sink:
    name: str
    description: str
    channels: list
    volume_raw: int
    codec: str = ""
    card: str = ""
    port: str = ""
    offset_us: int = 0
    bus: str = ""
    mute: bool = False

    @property
    def vol_db(self):
        return raw_to_db(self.volume_raw)


@dataclass
class Feed:
    """One of the combined output's streams into an output."""
    index: int
    sink: str
    mute: bool
    volume_raw: int


@dataclass
class Stream:
    """An app playing sound."""
    index: int
    app: str
    mute: bool
    corked: bool
    node: str
    media: str


def _avg_volume(vol):
    vals = [v["value"] for v in vol.values()] if isinstance(vol, dict) else []
    return round(sum(vals) / len(vals)) if vals else 65536


def _bus(sink):
    p = sink.get("properties", {})
    name = sink["name"]
    if name.startswith("bluez_output."):
        return "bluetooth"
    if not p.get("device.api") and not p.get("device.name"):
        return "virtual"
    if "HDMI" in name or "hdmi" in name:
        return "hdmi"
    if p.get("device.bus") == "usb":
        return "usb"
    return "built-in"


class PipeWire:
    def __init__(self, dry_run=False, say=print, run=subprocess.run):
        self.dry_run, self.say, self.run = dry_run, say, run

    # ------------------------------------------------------------ plumbing
    def _read(self, *args):
        try:
            return self.run(list(args), capture_output=True, text=True, check=False).stdout or ""
        except FileNotFoundError:
            return ""

    def _do(self, *args, check=False):
        if self.dry_run:
            self.say("would: " + shlex.join(str(a) for a in args))
            return ""
        r = self.run([str(a) for a in args], capture_output=True, text=True, check=False)
        if check and (r.returncode or "rror" in (r.stderr or "") + (r.stdout or "")):
            raise RuntimeError(f"{args[0]} failed: {((r.stderr or r.stdout) or '').strip()[:200]}")
        return r.stdout or ""

    def _pactl(self, what):
        try:
            return json.loads(self._read("pactl", "-f", "json", "list", what) or "[]")
        except ValueError:
            return []

    # ------------------------------------------------------------ outputs
    def sinks(self) -> list:
        cards = {c["name"]: c for c in self._pactl("cards")}
        out = []
        for s in self._pactl("sinks"):
            p = s.get("properties", {})
            chans = [CHANNEL.get(c, c.upper()) for c in str(s.get("channel_map", "")).split(",") if c]
            card = p.get("device.name", "")
            port = s.get("active_port") or ""
            if s["name"].startswith("bluez_output.") and not port:
                port = "headset-output"
            lat = cards.get(card, {}).get("ports", {}).get(port, {}).get("latency_offset", "0 usec")
            out.append(Sink(s["name"], s.get("description", s["name"]), chans, _avg_volume(s.get("volume")),
                            p.get("api.bluez5.codec", ""), card, port, int(str(lat).split()[0] or 0), _bus(s),
                            bool(s.get("mute"))))
        return out

    def node_ids(self, names) -> dict:
        ids = {}
        try:
            nodes = json.loads(self._read("pw-dump", "Node") or "[]")
        except ValueError:
            return ids
        for n in nodes:
            name = (n.get("info") or {}).get("props", {}).get("node.name", "")
            if name in names:
                ids[name] = n["id"]
        return ids

    def set_graph(self, node_id, text) -> None:
        arg = '{ params = [ "audioconvert.filter-graph.0" "' + text.replace('"', '\\"') + '" ] }'
        try:
            self._do("pw-cli", "set-param", str(node_id), "Props", arg, check=True)
        except RuntimeError as e:
            raise RuntimeError(f"PipeWire refused the filter graph: {e}") from None

    def live_controls(self, node_id) -> dict:
        out = {}
        try:
            objs = json.loads(self._read("pw-dump", str(node_id)) or "[]")
        except ValueError:
            return out
        for o in objs:
            if o.get("id") != node_id:
                continue
            for prop in (o.get("info") or {}).get("params", {}).get("Props", []) or []:
                pr = prop.get("params")
                if isinstance(pr, list):
                    out.update({pr[i]: pr[i + 1] for i in range(0, len(pr) - 1, 2)})
        return out

    def set_volume(self, sink, raw) -> None:
        self._do("pactl", "set-sink-volume", sink, str(int(raw)))

    def set_port_offset(self, card, port, us) -> None:
        self._do("pactl", "set-port-latency-offset", card, port, str(int(us)))

    def default_sink(self) -> str:
        return self._read("pactl", "get-default-sink").strip()

    def set_default_sink(self, name) -> None:
        self._do("pactl", "set-default-sink", name)

    # ------------------------------------------------------------ streams
    def feeds(self, combine) -> list:
        prefix = f"output.{combine}_"
        out = []
        for si in self._pactl("sink-inputs"):
            node = si.get("properties", {}).get("node.name", "")
            if node.startswith(prefix):
                vals = [v["value"] for v in si.get("volume", {}).values()]
                out.append(Feed(si["index"], node[len(prefix):], bool(si.get("mute")), min(vals) if vals else 65536))
        out.sort(key=lambda f: f.sink)
        return out

    def set_feed_mute(self, index, on) -> None:
        self._do("pactl", "set-sink-input-mute", str(index), "1" if on else "0")

    def set_feed_volume(self, index, raw) -> None:
        self._do("pactl", "set-sink-input-volume", str(index), str(int(raw)))

    def streams(self, combine="sounar") -> list:
        out = []
        for si in self._pactl("sink-inputs"):
            p = si.get("properties", {})
            node = p.get("node.name", "")
            if node.startswith(f"output.{combine}_"):
                continue
            app = p.get("application.process.binary") or p.get("application.name") or node
            out.append(Stream(si["index"], app, bool(si.get("mute")), bool(si.get("corked")), node,
                              p.get("media.name", "")))
        return out

    def set_stream_mute(self, index, on) -> None:
        self._do("pactl", "set-sink-input-mute", str(index), "1" if on else "0")

    # ------------------------------------------------------------ the rest
    def sources(self) -> list:
        return [ln.split("\t")[1] for ln in self._read("pactl", "list", "short", "sources").splitlines()
                if "\t" in ln and ".monitor" not in ln.split("\t")[1]]

    def default_source(self) -> str:
        return self._read("pactl", "get-default-source").strip()

    def links(self) -> str:
        return self._read("pw-link", "-l")

    def ports_out(self) -> str:
        """Every output port, linked or not (`links` only shows linked ones)."""
        return self._read("pw-link", "-o")

    def play(self, path, target) -> None:
        """Play a sound file straight to one output (setup's identification tones)."""
        self._do("pw-play", "--target", target, str(path))

    def systemctl(self, *args) -> None:
        self._do("systemctl", "--user", *args)

    def unit_active(self, unit) -> bool:
        return self._read("systemctl", "--user", "is-active", unit).strip() == "active"

    def version(self) -> tuple:
        m = re.search(r"libpipewire (\d+)\.(\d+)\.(\d+)", self._read("pipewire", "--version"))
        return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)
