"""The engine: a speaker map, a preset, the calibration and the sync delays in, one filter graph
per output out. Pure: it never talks to PipeWire. The formulas are explained in docs/layouts.md.

  stereo  every output plays as wired; only sync delays
  spread  like stereo with L' = M + w*S, R' = M - w*S (party)
  room    front speakers share the stereo picture with the vocals anchored in the speaker you
          face; side and rear speakers play delayed, band-limited ambience; every speaker plays
          the same mono bass, and all of it lands together at the seat
"""
import math
from dataclasses import dataclass, field, replace

from sounar import paths
from sounar.graph import Graph, bq
from sounar.speakers import anchor

REF_SIDE_GAIN = 1.7      # side gain of Sounar Default's widening: the loudness reference (stereo + Default)
MISSING = "Some speakers are missing: plain stereo for now"


def db(x):
    return 10 ** (x / 20)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


@dataclass
class Gains:
    mid: float = 0.0          # high band of M (front speakers)
    side: float = 0.0         # high band of S (front speakers)
    bass: float = 1.0         # the shared low band
    amb: float = 0.0          # ambience (surround speakers), sign = polarity
    split: bool = False       # a lone front stereo box: M + side*S on FL, M - side*S on FR


@dataclass
class OutputPlan:
    output_id: str
    node: str
    text: str
    controls: dict
    delays_ms: dict = field(default_factory=dict)


@dataclass
class Plan:
    layout: str
    wanted: str
    outputs: list
    note: str = ""


def room_gains(speakers, params, levels) -> dict:
    """Per speaker id: how much of each signal it plays in the room layout (design §5)."""
    c = db(-params["centre"])
    front = [s for s in speakers if s.zone == "front"]
    surround = [s for s in speakers if s.zone == "surround"]
    lv = {s.id: float(levels.get(s.id, 0.0)) for s in speakers}
    ref = [lv[s.id] for s in (front or speakers)]
    mean_front = sum(ref) / len(ref) if ref else 0.0
    bass = db(params["bass"])
    out = {}
    a = anchor(front)
    weights = {}
    for s in front:
        if a is None:
            weights[s.id] = (1.0, float(s.side))
        elif s is a:
            weights[s.id] = (1.0, s.side * c)
        else:
            weights[s.id] = (c, float(s.side))
    lone = len(front) == 1 and front[0].stereo_box
    if lone:
        weights[front[0].id] = (1.0, 1.0)
    g = math.sqrt(len(front) / sum(m * m for m, _ in weights.values())) if front else 1.0
    for s in front:
        m, sd = weights[s.id]
        tr = db(clamp(mean_front - lv[s.id], -5.0, 5.0))
        out[s.id] = Gains(mid=g * tr * m, side=g * tr * sd, bass=bass if s.bass_boost else 1.0, split=lone)
    for s in surround:
        pol = 1.0 if len(surround) == 1 or s.side == 0 else float(s.side)
        amb = g * db(params["fill"]) * db(clamp(mean_front - lv[s.id], -10.0, 10.0)) / math.sqrt(len(surround))
        out[s.id] = Gains(bass=bass if s.bass_boost else 1.0, amb=pol * amb)
    for s in speakers:
        if s.zone == "sub":
            out[s.id] = Gains(bass=bass if s.bass_boost else 1.0)
    return out


def _vocal(params):
    v = params["vocal"]
    return [bq("peaking", 2500, v, 0.8), bq("peaking", 900, 0.4 * v, 1.0)] if abs(v) >= 0.05 else []


def _corr(output):
    return [bq(f["type"], f["freq"], f["gain"], f["q"]) for f in output.correction]


def _out_channels(output):
    return ["MONO"] if output.channels == ["MONO"] else ["FL", "FR"]


def _finish(G, name, port, corr):
    """Optional correction EQ at the very end of a chain. Returns the chain's last port."""
    if not corr:
        return port
    G.eq(name, *corr)
    G.link(port, f"{name}:In 1")
    return f"{name}:Out 1"


def _text(G, ports, output):
    outs = []
    for ch in _out_channels(output):
        if ch not in ports:                         # no speaker on this channel: silence
            if "z:Out" not in outs:
                G.mixer("z", 0.0)
                G.link("cr:Out", "z:In 1")
            ports[ch] = "z:Out"
        outs.append(ports[ch])
    return G.text(["cl:In", "cr:In"], outs)


def _wired_graph(output, params, delay_s, spread):
    G = Graph()
    L, R = G.head()
    if spread:
        w = params["width"]
        M, S = G.mid_side(L, R)
        G.mixer("l", 1.0, w); G.mixer("r", 1.0, -w)
        G.link(M, "l:In 1"); G.link(S, "l:In 2"); G.link(M, "r:In 1"); G.link(S, "r:In 2")
        L, R = "l:Out", "r:Out"
    src = {"FL": L, "FR": R}
    if "MONO" in _out_channels(output):
        G.mixer("mono", 0.5, 0.5)
        G.link(L, "mono:In 1"); G.link(R, "mono:In 2")
        src["MONO"] = "mono:Out"
    corr, ports = _corr(output), {}
    for ch in _out_channels(output):
        d = f"d{ch.lower()}"
        G.delay(d, delay_s)
        G.link(src[ch], f"{d}:In")
        ports[ch] = _finish(G, f"e{ch.lower()}", f"{d}:Out", corr)
    return G, _text(G, ports, output)


UPMIX_MAX = 8                      # chains the upmix plugin knows about
UPMIX_LATENCY_S = 2048 / 48000     # its delay (the same on every chain); a sub waits as long
SPLIT_DEG = 15                     # a lone stereo box's channels, this far either side of it


def upmix_chains(speakers, gains):
    """The chains the upmixer places sounds between: every front and surround speaker, a lone
    front stereo box as two (just left and right of it). None if there are too many."""
    chains = []
    for s in speakers:
        if s.zone == "sub":
            continue
        if gains[s.id].split:
            chains += [{"sid": s.id, "sign": 1.0, "angle": s.angle - SPLIT_DEG, "zone": 1},
                       {"sid": s.id, "sign": -1.0, "angle": s.angle + SPLIT_DEG, "zone": 1}]
        else:
            chains.append({"sid": s.id, "sign": None, "angle": s.angle, "zone": 1 if s.zone == "front" else 2})
    return chains if 0 < len(chains) <= UPMIX_MAX else None


def _upmix_graph(output, params, gains, delays, chains):
    """The room layout with sounds placed (docs/upmix.md): one sounar_upmix instance per chain
    renders that chain's bass, room mix and placed sounds; a surround's ambience then gets the
    usual band, dip and delay. Placement 0 would be the plain room mix (that path is used then)."""
    G = Graph()
    L, R = G.head()
    if any(s.zone == "sub" for s in output.speakers):
        M, _ = G.mid_side(L, R)
        x = params["xover"]
        G.eq("mlo", bq("lowpass", x), bq("lowpass", x))
        G.link(M, "mlo:In 1")
    geo = {}
    for i in range(UPMIX_MAX):
        c = chains[i] if i < len(chains) else None
        geo[f"Angle {i + 1}"] = round(c["angle"], 1) if c else 0.0
        geo[f"Zone {i + 1}"] = c["zone"] if c else 0
    index = {(c["sid"], c["sign"]): i for i, c in enumerate(chains)}
    corr, ports = _corr(output), {}
    for i, s in enumerate(output.speakers):
        g = gains[s.id]
        specs = [(["FL"], 1.0), (["FR"], -1.0)] if g.split else [(s.channels, None)]
        for j, (chs, sign) in enumerate(specs):
            n = f"s{i}{'lr'[j] if g.split else ''}"
            wait = 0.0
            if s.zone == "sub":
                G.mixer(n, g.bass)
                G.link("mlo:Out 1", f"{n}:In 1")
                src, wait = f"{n}:Out", UPMIX_LATENCY_S
            else:
                front = s.zone == "front"
                G.ladspa(f"{n}u", "sounar_upmix", "sounar_upmix", {
                    "Placement": params["place"], "Wrap": params["wrap"], "Ambience front": 0.5,
                    "Crossover": params["xover"], "Vocal": params["vocal"], "Match": 1,
                    "Self": index[(s.id, sign)] + 1, "Count": len(chains), **geo, "Bass": g.bass,
                    "Base M": g.mid if front else 0.0, "Base S": g.side * (sign or 1.0) if front else 0.0,
                    "Base amb": 0.0 if front else g.amb})
                G.link(L, f"{n}u:In L"); G.link(R, f"{n}u:In R")
                if front:
                    src = f"{n}u:Out"
                else:
                    G.eq(f"{n}a", bq("highpass", params["amb_lo"]), bq("highpass", params["amb_lo"]),
                         bq("peaking", 2500, params["dip"], 0.6), bq("lowpass", params["amb_hi"]))
                    G.link(f"{n}u:Amb", f"{n}a:In 1")
                    G.delay(f"{n}h", params["haas"] / 1000)
                    G.link(f"{n}a:Out 1", f"{n}h:In")
                    G.mixer(n, 1.0, 1.0)
                    G.link(f"{n}u:Out", f"{n}:In 1"); G.link(f"{n}h:Out", f"{n}:In 2")
                    src = f"{n}:Out"
            G.delay(f"{n}d", delays[s.id] + wait)
            G.link(src, f"{n}d:In")
            last = _finish(G, f"{n}e", f"{n}d:Out", corr)
            if len(chs) == 1:
                ports[chs[0]] = last
            else:
                for ch in chs:
                    G.copy(f"{n}{ch.lower()}")
                    G.link(last, f"{n}{ch.lower()}:In")
                    ports[ch] = f"{n}{ch.lower()}:Out"
    return G, _text(G, ports, output)


def _room_graph(output, params, gains, delays):
    G = Graph()
    L, R = G.head()
    M, S = G.mid_side(L, R)
    x = params["xover"]
    G.eq("mlo", bq("lowpass", x), bq("lowpass", x))                     # Linkwitz-Riley crossover: bass
    G.eq("mhi", bq("highpass", x), bq("highpass", x), *_vocal(params))
    G.eq("shi", bq("highpass", x), bq("highpass", x))
    G.link(M, "mlo:In 1"); G.link(M, "mhi:In 1"); G.link(S, "shi:In 1")
    if any(s.zone == "surround" for s in output.speakers):
        # ambience = S (no centre, so vocals stay in front), band-limited, vocal-band dip, late
        G.eq("amb", bq("highpass", params["amb_lo"]), bq("highpass", params["amb_lo"]),
             bq("peaking", 2500, params["dip"], 0.6), bq("lowpass", params["amb_hi"]))
        G.link(S, "amb:In 1")
        G.delay("adly", params["haas"] / 1000)
        G.link("amb:Out 1", "adly:In")
    corr, ports = _corr(output), {}
    for i, s in enumerate(output.speakers):
        g = gains[s.id]
        chains = [(["FL"], 1.0), (["FR"], -1.0)] if g.split else [(s.channels, 1.0)]
        for j, (chs, sign) in enumerate(chains):
            n = f"s{i}{'lr'[j] if g.split else ''}"
            if s.zone == "front":
                G.mixer(n, g.bass, g.mid, g.side * sign)
                G.link("mlo:Out 1", f"{n}:In 1"); G.link("mhi:Out 1", f"{n}:In 2"); G.link("shi:Out 1", f"{n}:In 3")
            elif s.zone == "surround":
                G.mixer(n, g.bass, g.amb)
                G.link("mlo:Out 1", f"{n}:In 1"); G.link("adly:Out", f"{n}:In 2")
            else:
                G.mixer(n, g.bass)
                G.link("mlo:Out 1", f"{n}:In 1")
            G.delay(f"{n}d", delays[s.id])
            G.link(f"{n}:Out", f"{n}d:In")
            last = _finish(G, f"{n}e", f"{n}d:Out", corr)
            if len(chs) == 1:
                ports[chs[0]] = last
            else:
                for ch in chs:
                    G.copy(f"{n}{ch.lower()}")
                    G.link(last, f"{n}{ch.lower()}:In")
                    ports[ch] = f"{n}{ch.lower()}:Out"
    return G, _text(G, ports, output)


def speaker_delays(setup, layout, st, playing) -> dict:
    """Seconds per speaker id. Sync lines the outputs up; in the room layout every speaker also
    waits for the farthest one (calibrated arrival offsets), so all the bass lands together."""
    sync = st.get("sync_ms", {})
    offs = st["calibration"].get("offsets_ms", {})
    out = {}
    tmax = max((float(offs.get(s.id, 0.0)) for s in playing), default=0.0)
    for s in playing:
        d = float(sync.get(setup.output_of(s.id).id, 0.0))
        if layout == "room":
            d += tmax - float(offs.get(s.id, 0.0))
        out[s.id] = d / 1000
    return out


def room_cuts(st) -> list:
    """The gentle bass cuts from `sounar room measure` (the same on every output), as correction
    filters; none when room correction is off or the room wasn't measured."""
    r = st.get("room") or {}
    if not r.get("correction", True):
        return []
    return [{"type": "peaking", "freq": c["freq"], "gain": c["gain"], "q": c["q"]} for c in r.get("cuts", [])]


def plan(setup, eff, st, connected, force_layout=None) -> Plan:
    cuts = room_cuts(st)
    outs = [o if not cuts else replace(o, correction=list(o.correction) + cuts)
            for o in setup.outputs if o.node in connected]
    wanted = force_layout or eff.layout
    if not outs:
        return Plan("stereo", wanted, [], "No speakers connected")
    layout, note = wanted, ""
    if layout in ("room", "spread") and len(outs) < len(setup.outputs):
        layout, note = "stereo", MISSING
    playing = [s for o in outs for s in o.speakers]
    delays = speaker_delays(setup, layout, st, playing)
    gains = room_gains(playing, eff.params, st["calibration"].get("levels", {})) if layout == "room" else None
    chains = None
    if layout == "room" and eff.params.get("place", 0) > 0 and paths.upmix_available():
        chains = upmix_chains(playing, gains)
    plans = []
    for o in outs:
        if layout == "room" and chains:
            G, text = _upmix_graph(o, eff.params, gains, delays, chains)
        elif layout == "room":
            G, text = _room_graph(o, eff.params, gains, delays)
        else:
            G, text = _wired_graph(o, eff.params, delays[o.speakers[0].id], layout == "spread")
        plans.append(OutputPlan(o.id, o.node, text, dict(G.ctl), {s.id: delays[s.id] * 1000 for s in o.speakers}))
    return Plan(layout, wanted, plans, note)


def loudness_db(setup, eff, st, side_gain) -> float:
    """Modelled loudness at the seat (200 Hz-5 kHz) against stereo with Sounar Default, from the
    calibrated levels. Music is modelled as mid power 1 and side power 0.2 before Easy Effects'
    widening (side ~7 dB under mid, typical for pop)."""
    spk = setup.speakers()
    levels = st["calibration"].get("levels", {})
    P = {s.id: 10 ** (float(levels.get(s.id, 0.0)) / 10) for s in spk}
    total = sum(P.values())
    pm, ps = 1.0, 0.2 * side_gain ** 2
    ref = total * (1.0 + 0.2 * REF_SIDE_GAIN ** 2)
    if eff.layout == "room":
        p = eff.params
        gn = room_gains(spk, p, levels)
        pm *= 0.7 + 0.3 * 10 ** (p["vocal"] / 10)                       # presence lift on the centre
        amb = 0.6 * 10 ** (p["dip"] / 10 * 0.3)                          # band limits and dip
        power = 0.0
        for s in spk:
            g = gn[s.id]
            if s.zone == "front":
                power += P[s.id] * (g.mid ** 2 * pm + g.side ** 2 * ps)
            elif s.zone == "surround":
                power += P[s.id] * g.amb ** 2 * ps * amb
    elif eff.layout == "spread":
        w = eff.params["width"]
        power = total * (pm + w * w * ps)
    else:
        power = total * (pm + ps)
    return 10 * math.log10(power / ref)
