"""Builds the PipeWire filter-graph strings that run inside each output (the
`audioconvert.filter-graph.0` property, PipeWire 1.4+). Every control value is recorded in
`ctl`, which is what the live node reports back, so a graph can be verified after it is set."""
import hashlib

MAX_DELAY = 0.5          # seconds, per delay node


def fnum(v):
    return f"{v:.6f}"


def bq(kind, freq, gain=0.0, q=0.707):
    return f"{{ type = bq_{kind} freq = {freq:g} gain = {gain:g} q = {q:g} }}"


class Graph:
    def __init__(self):
        self.nodes, self.links, self.ctl = [], [], {}

    def node(self, name, label, config=None, control=None):
        s = f"{{ type = builtin name = {name} label = {label}"
        if config:
            s += f" config = {{ {config} }}"
        if control:
            s += " control = { " + " ".join(f'"{k}" = {fnum(v)}' for k, v in control.items()) + " }"
            self.ctl.update({f"{name}:{k}": float(fnum(v)) for k, v in control.items()})
        self.nodes.append(s + " }")

    def copy(self, name):
        self.node(name, "copy")

    def ladspa(self, name, plugin, label, control):
        """A LADSPA plugin node. `plugin` is a name found on LADSPA_PATH: in an output's filter
        graph PipeWire puts every search directory in front of it, even of an absolute path."""
        self.nodes.append(f'{{ type = ladspa name = {name} plugin = "{plugin}" label = {label} control = {{ '
                          + " ".join(f'"{k}" = {fnum(v)}' for k, v in control.items()) + " } }")
        self.ctl.update({f"{name}:{k}": float(fnum(v)) for k, v in control.items()})

    def mixer(self, name, *gains):
        self.node(name, "mixer", control={f"Gain {i + 1}": g for i, g in enumerate(gains)})

    def eq(self, name, *filters):
        self.node(name, "param_eq", config="filters = [ " + " ".join(filters) + " ]")

    def delay(self, name, seconds):
        self.node(name, "delay", config=f'"max-delay" = {MAX_DELAY}',
                  control={"Delay (s)": min(max(seconds, 0.0), MAX_DELAY - 0.01)})

    def link(self, out, inp):
        self.links.append(f'{{ output = "{out}" input = "{inp}" }}')

    def text(self, inputs, outputs):
        """One line. A signature in the otherwise unused Gain 8 of mixer `snr` identifies the
        exact graph, so a stale graph is detected even when all other controls match."""
        def build(sig):
            nodes = [n.replace("@SIG@", sig) for n in self.nodes]
            return ("{ nodes = [ " + " ".join(nodes) + " ] links = [ " + " ".join(self.links)
                    + " ] inputs = [ " + " ".join(f'"{i}"' for i in inputs) + " ] outputs = [ "
                    + " ".join(f'"{o}"' for o in outputs) + " ] }")
        sig = int(hashlib.sha1(build("0").encode()).hexdigest()[:6], 16) / 2 ** 24
        self.ctl["snr:Gain 8"] = float(fnum(sig))
        return build(fnum(sig))

    def head(self):
        """cl/cr input copies, with `snr` (gain 1, signature) on the left. Returns L, R ports."""
        self.copy("cl"); self.copy("cr")
        self.nodes.append('{ type = builtin name = snr label = mixer '
                          'control = { "Gain 1" = 1.000000 "Gain 8" = @SIG@ } }')
        self.ctl["snr:Gain 1"] = 1.0
        self.link("cl:Out", "snr:In 1")
        return "snr:Out", "cr:Out"

    def mid_side(self, L, R):
        self.mixer("mid", 0.5, 0.5); self.mixer("sid", 0.5, -0.5)
        for n in ("mid", "sid"):
            self.link(L, f"{n}:In 1"); self.link(R, f"{n}:In 2")
        return "mid:Out", "sid:Out"
