"""Stand-ins for a real system: an in-memory PipeWire and Easy Effects that record every change."""
from pathlib import Path

import sim
from sounar.pipewire import Feed, Sink, Stream


def controls_of(text):
    """What a live node reports back for a graph: every control value, as PipeWire does."""
    out = {}
    for nd in sim.parse(text)["nodes"]:
        for k, v in nd.get("control", {}).items():
            out[f"{nd['name']}:{k}"] = float(v)
    return out


class FakePipeWire:
    def __init__(self, sinks=(), combine="sounar", version=(1, 6, 8)):
        self.dry_run = False
        self.combine = combine
        self._sinks = {s.name: s for s in sinks}
        self.ids = {name: 100 + i for i, name in enumerate(self._sinks)}
        self.graphs, self.live = {}, {}
        self.feed_list = [Feed(200 + i, s.name, False, 65536) for i, s in enumerate(sinks)
                          if s.name.startswith("bluez_output.") or s.bus in ("usb", "built-in", "hdmi")]
        self.stream_list = [Stream(300, "pw-cat", False, False, "sounar_keepalive", "Sounar keep-alive")]
        self.default = combine
        self.calls = []
        self.units = {"sounar-keepalive.service": True, "sounar-guard.service": True}
        self._version = version
        self.link_text = ""
        self.out_ports = ""

    def log(self, *a):
        self.calls.append(a)

    # outputs
    def sinks(self):
        return list(self._sinks.values())

    def add_sink(self, sink):
        self._sinks[sink.name] = sink
        self.ids.setdefault(sink.name, 100 + len(self.ids))
        self.feed_list.append(Feed(200 + len(self.feed_list), sink.name, False, 65536))

    def remove_sink(self, name):
        self._sinks.pop(name)
        self.live.pop(self.ids.pop(name, None), None)       # a reconnecting node comes back plain
        self.feed_list = [f for f in self.feed_list if f.sink != name]

    def node_ids(self, names):
        return {n: i for n, i in self.ids.items() if n in names and n in self._sinks}

    def set_graph(self, node_id, text):
        self.log("set_graph", node_id)
        self.graphs[node_id] = text
        self.live[node_id] = controls_of(text)

    def live_controls(self, node_id):
        return dict(self.live.get(node_id, {}))

    def set_volume(self, sink, raw):
        self.log("set_volume", sink, raw)
        self._sinks[sink].volume_raw = int(raw)

    def set_port_offset(self, card, port, us):
        self.log("set_port_offset", card, port, us)
        for s in self._sinks.values():
            if s.card == card and s.port == port:
                s.offset_us = int(us)

    def default_sink(self):
        return self.default

    def set_default_sink(self, name):
        self.log("set_default_sink", name)
        self.default = name

    # streams
    def feeds(self, combine):
        return [f for f in self.feed_list if f.sink in self._sinks]

    def set_feed_mute(self, index, on):
        self.log("set_feed_mute", index, on)
        for f in self.feed_list:
            if f.index == index:
                f.mute = on

    def set_feed_volume(self, index, raw):
        self.log("set_feed_volume", index, raw)
        for f in self.feed_list:
            if f.index == index:
                f.volume_raw = raw

    def streams(self, combine="sounar"):
        return list(self.stream_list)

    def set_stream_mute(self, index, on):
        self.log("set_stream_mute", index, on)
        for s in self.stream_list:
            if s.index == index:
                s.mute = on

    # the rest
    def sources(self):
        return ["alsa_input.test-mic", "alsa_input.usb-mic"]

    def default_source(self):
        return "alsa_input.test-mic"

    def links(self):
        return self.link_text

    def ports_out(self):
        return self.out_ports

    def play(self, path, target):
        self.log("play", target, str(path))

    def systemctl(self, *args):
        self.log("systemctl", *args)
        if args and args[0] in ("start", "restart"):
            self.units[args[-1]] = True

    def unit_active(self, unit):
        return self.units.get(unit, False)

    def version(self):
        return self._version


class FakeEffects:
    def __init__(self, presets=None, available=True):
        from sounar import paths
        from sounar.effects import Effects
        self._real = Effects(which=lambda _: None, preset_dir=paths.package_data() / "easyeffects",
                             socket_path="/nonexistent")
        self._available = available
        self.loaded = None
        self.target = None
        self.bypassed = False
        self.pid_value = "4242"
        self.calls = []
        self._presets = presets

    def available(self):
        return self._available

    def running(self):
        return self._available

    def pid(self):
        return self.pid_value if self._available else None

    def presets(self):
        return self._presets or self._real.presets()

    def data(self, name):
        return self._real.data(name)

    def current(self):
        return self.loaded if self._available else None

    def load(self, name):
        if self._available:
            self.calls.append(("load", name))
            self.loaded = name

    def bypass(self):
        return self.bypassed if self._available else None

    def set_bypass(self, on):
        if self._available:
            self.calls.append(("bypass", on))
            self.bypassed = on

    def leveling(self, name):
        return self._real.leveling(name)

    def target_live(self):
        return self.target if self._available else None

    def set_target(self, t):
        if self._available:
            self.calls.append(("target", t))
            self.target = t

    def set_leveling(self, on, names):
        self.calls.append(("leveling", on, tuple(names)))


def bt_sink(n, description, volume_raw=60000, offset_us=0, codec="sbc"):
    mac = f"00_00_00_00_00_{n:02d}"
    return Sink(f"bluez_output.{mac}.1", description, ["FL", "FR"], volume_raw, codec, f"bluez_card.{mac}",
                "headset-output", offset_us, "bluetooth")


def original_room_system():
    """Two Bluetooth outputs, as in the original room (setups/stereo-box-and-twin-right)."""
    return FakePipeWire([bt_sink(1, "Speaker A", 65536, 140000), bt_sink(2, "Speaker B", 54000, 0, "aptx"),
                         Sink("sounar", "Sounar", ["FL", "FR"], 65536, bus="virtual")])


class FakePlayers:
    """Media players: paused at the start of a measurement, resumed at the end."""
    def __init__(self):
        self.paused = self.resumed = False

    def __enter__(self):
        self.paused = True
        return self

    def __exit__(self, *exc):
        self.resumed = True


class FakeRoom:
    """A room with a microphone at the seat: each speaker is heard after its output's Bluetooth
    delay, the output's current graph delay and its own distance, at its own level."""
    def __init__(self, pw, setup, link_ms, acoustic_ms, gain_db, noise=0.0005, jitter_ms=0.0, seed=7):
        import numpy as np
        self.np, self.pw, self.setup = np, pw, setup
        self.link_ms, self.acoustic_ms, self.gain_db = link_ms, acoustic_ms, gain_db
        self.noise, self.jitter_ms = noise, jitter_ms
        self.rng = np.random.default_rng(seed)
        self.on_play = None

    def graph_delay_ms(self, node):
        live = self.pw.live_controls(self.pw.ids[node])
        vals = [v for k, v in live.items() if k.endswith(":Delay (s)") and k.split(":")[0] in ("dfl", "dfr", "dmono")]
        return 1000 * vals[0] if vals else 0.0

    def play_record(self, L, R, switches, feeds):
        np = self.np
        from sounar.measure import RATE
        if self.on_play:
            self.on_play()
        n = len(L)
        rec = np.zeros(n + 2 * RATE)
        bounds = [int(t * RATE) for t, _ in switches] + [n]
        for (_, idx), a, b in zip(switches, bounds, bounds[1:], strict=False):
            a = max(0, a)
            jit = self.rng.uniform(-self.jitter_ms, self.jitter_ms) if self.jitter_ms else 0.0
            for j, feed in enumerate(feeds):
                if idx is not None and j != idx:
                    continue
                out = next(o for o in self.setup.outputs if o.node == feed.sink)
                for s in out.speakers:
                    seg = np.zeros(b - a)
                    for ch in s.channels:
                        seg += L[a:b] if ch == "FL" else R[a:b] if ch == "FR" else 0.5 * (L[a:b] + R[a:b])
                    d_ms = (500.0 + self.link_ms[out.id] + self.graph_delay_ms(out.node)
                            + self.acoustic_ms.get(s.id, 0.0) + jit)
                    at = a + int(round(d_ms / 1000 * RATE))
                    rec[at:at + len(seg)] += seg * 10 ** (self.gain_db.get(s.id, 0.0) / 20)
        return rec + self.noise * self.rng.standard_normal(len(rec))


def bt_names(pw):
    """The Bluetooth outputs' node names, in order."""
    return sorted(s.name for s in pw.sinks() if s.bus == "bluetooth")


LEGACY_PRESETS = Path(__file__).parent / "fixtures/prototype/presets-0.1.toml"


def legacy_presets():
    """The nine presets of 0.1 (club, party, tv, ...) as a user presets file: tests of the
    engine use them; 0.2 ships three."""
    from sounar.presets import load_presets
    return load_presets(LEGACY_PRESETS)
