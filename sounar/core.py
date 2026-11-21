"""The changes Sounar makes: switching presets, fine-tuning, the Easy Effects choice, balance,
sync by ear, re-applying and repairing. Every change runs under the lock, is logged, and
notifies once (unless quiet)."""
import math
import time
from dataclasses import dataclass, field
from collections.abc import Callable

from sounar import SounarError, paths, syncpoints
from sounar.effects import Effects, autogain_target, side_gain
from sounar.graph import MAX_DELAY
from sounar.layouts import loudness_db, plan
from sounar.pipewire import PipeWire, db_to_raw, pct
from sounar.presets import PARAMS, applicable, load_presets
from sounar.speakers import load_setup
from sounar.state import (Lock, effective, history, history_append, load_state, log, save_state, set_tweak)

UNIT_SUFFIX = {"dB": " dB", "ms": " ms", "Hz": " Hz", "": ""}


def _noop(*a, **k):
    pass


@dataclass
class Ctx:
    setup: object
    pw: object
    ee: object
    presets: dict
    quiet: bool = False
    say: Callable = print
    notify: Callable = _noop
    settle: float = 0.5                 # seconds for Easy Effects to load a preset
    recorder: object = None             # measuring backend (sounar.measure.Recorder when None)
    extra: dict = field(default_factory=dict)

    def tell(self, title, body="", icon="audio-speakers"):
        if not self.quiet:
            self.notify(title, body, icon)


def load_ctx(quiet=False, need_setup=True) -> Ctx:
    from sounar import desktop
    dry = paths.dry_run()
    setup = None
    if paths.setup_file().exists():
        setup = load_setup(paths.setup_file())
    elif need_setup:
        raise SounarError("no setup yet; run `sounar setup` to tell Sounar about your speakers")
    notify = desktop.notify if (setup is None or setup.notify) and not dry else _noop
    return Ctx(setup, PipeWire(dry_run=dry), Effects(dry_run=dry), load_presets(paths.user_presets_file()),
               quiet=quiet, notify=notify)


# ---------------------------------------------------------------- applying

def connected(ctx) -> set:
    present = {s.name for s in ctx.pw.sinks()}
    return {o.node for o in ctx.setup.outputs if o.node in present}


def current_plan(ctx, st, eff=None, force_layout=None):
    eff = eff or effective(st, ctx.presets)
    return plan(ctx.setup, eff, st, connected(ctx), force_layout)


def apply_plan(ctx, p) -> None:
    ids = ctx.pw.node_ids({o.node for o in p.outputs})
    for o in p.outputs:
        if o.node in ids:
            ctx.pw.set_graph(ids[o.node], o.text)


def leveling_target(ctx, eff, st):
    if not eff.ee:
        return None
    data = ctx.ee.data(eff.ee)
    return autogain_target(data, loudness_db(ctx.setup, eff, st, side_gain(data)))


def apply_effects(ctx, eff, st, load=True):
    """Load the preset's Easy Effects preset (if it isn't already) and set its leveling target."""
    if not eff.ee or not ctx.ee.available():
        return None
    if load and ctx.ee.current() != eff.ee:
        ctx.ee.load(eff.ee)
        time.sleep(ctx.settle)
    tgt = leveling_target(ctx, eff, st)
    if tgt is not None and ctx.ee.current() == eff.ee:
        ctx.ee.set_target(tgt)
    return tgt


def _apply_all(ctx, st, load_effects=False):
    eff = effective(st, ctx.presets)
    p = current_plan(ctx, st, eff)
    apply_plan(ctx, p)
    tgt = apply_effects(ctx, eff, st, load=load_effects)
    return eff, p, tgt


# ---------------------------------------------------------------- presets

def resolve(ctx, pid):
    if pid in ctx.presets:
        return pid
    hits = [k for k, v in ctx.presets.items() if pid.lower() in f"{k} {v.label}".lower()]
    if len(hits) != 1:
        raise SounarError(f"no preset '{pid}'; choose from: {', '.join(ctx.presets)}")
    return hits[0]


def switch(ctx, pid):
    with Lock():
        st = load_state(ctx.presets)
        pid = resolve(ctx, pid)
        if pid != st["preset"]:
            st["previous"], st["preset"] = st["preset"], pid
        save_state(st)
        eff, p, tgt = _apply_all(ctx, st, load_effects=True)
    log(f"preset {pid} ({p.layout}), effects {eff.ee}, leveling {tgt}" + (f"; {p.note}" if p.note else ""))
    ctx.tell(f"{eff.icon}  {eff.label}", eff.tagline + (f"\n{p.note}" if p.note else ""))
    return p


def step(ctx, delta):
    st = load_state(ctx.presets)
    ids = list(ctx.presets)
    i = ids.index(st["preset"]) if st["preset"] in ids else 0
    return switch(ctx, ids[(i + delta) % len(ids)])


def back(ctx):
    st = load_state(ctx.presets)
    prev = st.get("previous")
    if prev not in ctx.presets:
        prev = next(k for k in ctx.presets if k != st["preset"])
    return switch(ctx, prev)


# ---------------------------------------------------------------- fine-tuning

def _fmt(k, v):
    unit = PARAMS[k].unit
    return f"{v:+g}{UNIT_SUFFIX[unit]}" if unit == "dB" else f"{v:g}{UNIT_SUFFIX[unit]}"


def set_params(ctx, changes, how):
    """Save fine-tuning for the active preset ({value: absolute}) and apply it."""
    with Lock():
        st = load_state(ctx.presets)
        eff = effective(st, ctx.presets)
        allowed = applicable(ctx.setup, eff.layout)
        for k in changes:
            if k not in eff.params:
                has = ", ".join(eff.params) or "nothing to tune"
                raise SounarError(f"{eff.label} has no '{k}' (it has: {has})")
            if k not in allowed:
                raise SounarError(f"{PARAMS[k].label} doesn't apply to your speakers")
        done = []
        for k, v in changes.items():
            p = PARAMS[k]
            v = round(max(p.lo, min(p.hi, float(v))), 2)
            done.append((k, eff.params[k], v))
            set_tweak(st, eff.id, k, v, eff.base[k])
        save_state(st)
        eff, _, tgt = _apply_all(ctx, st)
    log(f"{how} {eff.id} " + ", ".join(f"{k} {a:g} -> {b:g}" for k, a, b in done) + f", leveling {tgt}")
    for k, a, b in done:
        ctx.say(f"{eff.label}: {PARAMS[k].label.lower()} {a:g} -> {b:g}{UNIT_SUFFIX[PARAMS[k].unit]}")
    if len(done) == 1:
        k, _, v = done[0]
        ctx.tell(f"{eff.icon}  {eff.label}", f"{PARAMS[k].label} {_fmt(k, v)}")


def tweak(ctx, key, arg):
    key = "centre" if key == "center" else key
    eff = effective(load_state(ctx.presets), ctx.presets)
    if key not in eff.params:
        raise SounarError(f"{eff.label} has no '{key}' (it has: {', '.join(eff.params) or 'nothing to tune'})")
    cur = eff.params[key]
    if arg is None:
        ctx.say(f"{eff.label}: {PARAMS[key].label.lower()} {cur:g}{UNIT_SUFFIX[PARAMS[key].unit]}")
        return
    try:
        # +N / -N: relative ("fill -1" = one dB less); =N or a bare number: absolute
        v = cur + float(arg) if arg[0] in "+-" else float(arg.lstrip("="))
    except ValueError:
        raise SounarError(f"'{arg}' isn't a number (use +N, -N or =N)") from None
    set_params(ctx, {key: v}, "tweak")


def tune(ctx, args):
    """`sounar tune vocal=1.5 bass=2`: absolute values, several at once (the bar panel)."""
    changes = {}
    for a in args:
        k, sep, v = a.partition("=")
        if not sep:
            raise SounarError(f"'{a}' should look like name=value")
        try:
            changes["centre" if k == "center" else k] = float(v)
        except ValueError:
            raise SounarError(f"'{v}' isn't a number") from None
    if changes:
        set_params(ctx, changes, "tune")


def reset(ctx):
    with Lock():
        st = load_state(ctx.presets)
        gone = st["tweaks"].pop(st["preset"], {})
        save_state(st)
        eff, _, _ = _apply_all(ctx, st, load_effects="ee" in gone)
    log(f"reset {eff.id}: dropped {gone}")
    ctx.say(f"{eff.label}: back to the preset's own values" + (f" (dropped {gone})" if gone else ""))


def set_effects(ctx, name):
    """Pick another Easy Effects preset for the active Sounar preset (`preset`: its own one)."""
    with Lock():
        st = load_state(ctx.presets)
        eff = effective(st, ctx.presets)
        if name.lower() in ("preset", "reset", "default-for-preset", ""):
            set_tweak(st, eff.id, "ee", eff.base_ee, eff.base_ee)
        else:
            names = ctx.ee.presets()
            hits = [n for n in names if n.lower() == name.lower()] or [n for n in names if name.lower() in n.lower()]
            if len(hits) != 1:
                raise SounarError(f"no Easy Effects preset '{name}'; choose from: {', '.join(names)}")
            set_tweak(st, eff.id, "ee", hits[0], eff.base_ee)
        save_state(st)
        eff = effective(st, ctx.presets)
        tgt = apply_effects(ctx, eff, st)
    log(f"effects {eff.id} -> {eff.ee}, leveling {tgt}")
    ctx.say(f"{eff.label}: Easy Effects {eff.ee}" + (f", leveling {tgt} LUFS" if tgt is not None else ""))
    ctx.tell(f"{eff.icon}  {eff.label}", f"Easy Effects: {eff.ee}")


# ---------------------------------------------------------------- balance and sync by ear

def snapshot(sinks) -> dict:
    return {"volumes": {s.name: s.volume_raw for s in sinks},
            "offsets": {f"{s.card}:{s.port}": s.offset_us for s in sinks if s.card},
            "names": {s.name: s.description for s in sinks}}


def balance(ctx, target, d):
    """Make one output louder (+) or quieter (-) by d dB: it goes up towards 100 % first, the
    rest lowers the others. The calibrated levels move with it, so room presets stay balanced."""
    d = float(d)
    with Lock():
        st = load_state(ctx.presets)
        out = ctx.setup.find(target)
        sinks = {s.name: s for s in ctx.pw.sinks()}
        conn = [o for o in ctx.setup.outputs if o.node in sinks]
        if len(conn) < 2:
            raise SounarError("balance needs at least two outputs connected")
        if out.node not in sinks:
            raise SounarError(f"{out.label} isn't connected")
        x, others = sinks[out.node], [sinks[o.node] for o in conn if o is not out]
        was = {s.name: s.vol_db for s in [x, *others]}
        before = snapshot([x, *others])
        before["levels"] = dict(st["calibration"]["levels"])
        if d > 0:
            up = min(d, -x.vol_db)
            changes = [(x, up)] + [(o, -(d - up)) for o in others]
        else:
            changes = [(x, max(d, -40 - x.vol_db))]
        for s, delta in changes:
            if abs(delta) >= 0.01:
                ctx.pw.set_volume(s.name, db_to_raw(s.vol_db + delta))
        now = {s.name: s for s in ctx.pw.sinks()}
        moved = ((now[x.name].vol_db - was[x.name])
                 - sum(now[o.name].vol_db - was[o.name] for o in others) / len(others))
        lv = st["calibration"]["levels"]
        for spk in out.speakers:
            lv[spk.id] = round(lv.get(spk.id, 0.0) + moved, 2)
        after = snapshot([now[x.name], *(now[o.name] for o in others)])
        after["levels"] = dict(lv)
        desc = ", ".join(f"{s.description} {pct(was[s.name])}%→{pct(now[s.name].vol_db)}%" for s in [x, *others])
        history_append("balance", before, after, f"sounar balance {out.id} {d:+g} dB: {desc}")
        save_state(st)
        _apply_all(ctx, st)
    msg = f"{out.label} {moved:+.1f} dB against the others ({desc})"
    log(f"balance {out.id} {d:+g} dB: {msg}")
    ctx.say(f"balance: {msg}")
    ctx.tell("Speaker balance", msg)


def normalise_sync(setup, sync):
    """Only delays exist: shift so the earliest-needing output has 0, clamped to what fits."""
    vals = {o.id: float(sync.get(o.id, 0.0)) for o in setup.outputs}
    lo = min(vals.values())
    top = MAX_DELAY * 1000 - 60
    return {k: round(min(top, v - lo), 1) for k, v in vals.items()}


def nudge(ctx, target, ms):
    """Sync by ear: that output plays `ms` later (negative = sooner)."""
    with Lock():
        st = load_state(ctx.presets)
        out = ctx.setup.find(target)
        before = dict(st["sync_ms"])
        sync = dict(st["sync_ms"])
        sync[out.id] = float(sync.get(out.id, 0.0)) + float(ms)
        st["sync_ms"] = normalise_sync(ctx.setup, sync)
        syncpoints.remember(st, "nudge", before)
        save_state(st)
        _apply_all(ctx, st)
    msg = sync_text(ctx.setup, st)
    log(f"nudge {out.id} {float(ms):+g} ms: {st['sync_ms']}")
    ctx.say(f"sync: {msg}")
    ctx.tell("Speaker sync", msg)


def restore_effects(ctx):
    """Easy Effects starts on its own last-saved preset, not the active Sounar preset's (after a
    boot or a restart, music could run through the wrong chain until a preset was picked again).
    Returns what it did ("" if it was right), or None if Easy Effects can't say yet."""
    st = load_state(ctx.presets)
    eff = effective(st, ctx.presets)
    if not eff.ee or not ctx.ee.available():
        return ""
    cur = ctx.ee.current()
    if cur is None:
        return None
    if cur == eff.ee:
        return ""
    apply_effects(ctx, eff, st, load=True)
    return f"Easy Effects came up on {cur}; loaded {eff.ee} for {eff.label}"


def move_speaker(ctx, target, at) -> str:
    """`sounar speaker <speaker> at <hour>`: where a speaker sits, as an hour on the clock face
    around the seat (the panel's map drags it). Saved in setup.toml and played right away: the
    room layout and the upmixer follow the new direction. Levels and timing stay as calibrated
    until `sounar calibrate`."""
    from sounar.speakers import save_setup
    hour = float(at) % 12
    hour = 12.0 if hour == 0 else round(hour, 2)
    with Lock():
        q = str(target).lower().strip()
        spk = next((s for s in ctx.setup.speakers() if q in (s.id, s.nickname.lower()) or q == s.name.lower()), None)
        if spk is None:
            raise SounarError(f"no speaker '{target}'; choose from: {', '.join(s.id for s in ctx.setup.speakers())}")
        was = spk.at
        spk.at = hour
        save_setup(ctx.setup, paths.setup_file())
        st = load_state(ctx.presets)
        _apply_all(ctx, st)
    msg = f"{spk.label} now at {hour:g} o'clock (was {was:g})"
    log(f"speaker {spk.id} at {was} -> {hour}")
    ctx.say(msg)
    return msg


def undo_sync(ctx) -> str:
    """`sounar sync undo`: back to the delays before the last sync, calibration, switch to stable
    or run of nudges. Nothing is played."""
    with Lock():
        st = load_state(ctx.presets)
        pts = syncpoints.points(st)
        if not pts:
            raise SounarError("no earlier sync to go back to")
        p = pts.pop()
        before = dict(st["sync_ms"])
        st["sync_ms"] = dict(p["sync_ms"])
        st["sync_undo"] = pts
        save_state(st)
        _apply_all(ctx, st)
    when = f"the {syncpoints.WHAT.get(p['what'], p['what'])} at {p['ts'][11:16]}"
    msg = f"{sync_text(ctx.setup, st)} (from before {when})"
    log(f"sync undo: {before} -> {st['sync_ms']} (from before {when} of {p['ts'][:10]})")
    ctx.say(f"sync: {msg}")
    ctx.tell("Speaker sync", f"Back to {msg}")
    return msg


def stable_sync(ctx) -> str:
    """`sounar sync stable`: back to the delays the last calibration or settled sync found.
    Nothing is played; it is one undo step."""
    with Lock():
        st = load_state(ctx.presets)
        s = syncpoints.stable_point(st)
        if not s:
            raise SounarError("no stable sync yet: `sounar calibrate` or a `sounar sync` that settles sets it")
        before = dict(st["sync_ms"])
        if before != s["sync_ms"]:
            st["sync_ms"] = dict(s["sync_ms"])
            syncpoints.remember(st, "stable", before)
            save_state(st)
            _apply_all(ctx, st)
    msg = syncpoints.describe(s, sync_text(ctx.setup, {"sync_ms": s["sync_ms"]}))
    if before == s["sync_ms"]:
        ctx.say(f"sync: already stable ({msg})")
        ctx.tell("Speaker sync", f"Already on the stable sync: {msg}")
        return msg
    log(f"sync stable: {before} -> {st['sync_ms']} ({s['what']} of {s['ts']})")
    ctx.say(f"sync: {msg}")
    ctx.tell("Speaker sync", f"Stable sync: {msg}")
    return msg


def sync_text(setup, st) -> str:
    sync = st.get("sync_ms", {})
    waits = [(o.label, float(sync.get(o.id, 0.0))) for o in setup.outputs if float(sync.get(o.id, 0.0)) >= 0.05]
    if not waits:
        return "no output waits for another"
    return ", ".join(f"{name} waits {v:.1f} ms" for name, v in waits)


# ---------------------------------------------------------------- keeping it right

def apply(ctx):
    with Lock():
        st = load_state(ctx.presets)
        _, p, _ = _apply_all(ctx, st)
    return p


def verify(ctx, st=None) -> list:
    """Ids of the outputs whose live graph differs from the active preset."""
    st = st or load_state(ctx.presets)
    p = current_plan(ctx, st)
    ids = ctx.pw.node_ids({o.node for o in p.outputs})
    bad = []
    for o in p.outputs:
        if o.node not in ids:
            continue
        live = ctx.pw.live_controls(ids[o.node])
        if any(k not in live or abs(float(live[k]) - v) > 2e-4 for k, v in o.controls.items()):
            bad.append(o.output_id)
    return bad


def repair(ctx):
    """Called by the guard: re-apply graphs that differ (after a reconnect WirePlumber gives an
    output its plain graph) and the leveling target. Returns what it did, or None."""
    try:
        with Lock():
            st = load_state(ctx.presets)
            msgs = []
            bad = verify(ctx, st)
            if bad:
                apply_plan(ctx, current_plan(ctx, st))
                msgs.append("graph re-applied on " + ", ".join(bad))
            eff = effective(st, ctx.presets)
            if eff.ee and ctx.ee.available() and ctx.ee.current() == eff.ee:
                want, live = leveling_target(ctx, eff, st), ctx.ee.target_live()
                if want is not None and live is not None and live != want:
                    ctx.ee.set_target(want)
                    msgs.append(f"leveling target {live} -> {want} LUFS")
            return "; ".join(msgs) or None
    except SounarError:                 # a measurement owns the speakers right now
        return None


def undo(ctx) -> str:
    """Put back the volumes, latency offsets and levels from before the last recorded change."""
    h = [e for e in history() if e["action"] != "undo"]
    if not h:
        raise SounarError("nothing to undo")
    last = h[-1]
    with Lock():
        sinks = {s.name: s for s in ctx.pw.sinks()}
        before = snapshot(list(sinks.values()))
        for name, raw in last["before"].get("volumes", {}).items():
            if name in sinks:
                ctx.pw.set_volume(name, raw)
        for key, us in last["before"].get("offsets", {}).items():
            card, port = key.split(":", 1)
            ctx.pw.set_port_offset(card, port, us)
        st = load_state(ctx.presets)
        if "levels" in last["before"]:
            st["calibration"]["levels"] = dict(last["before"]["levels"])
            save_state(st)
        history_append("undo", before, snapshot(ctx.pw.sinks()), f"undid {last['action']} from {last['ts']}")
        _apply_all(ctx, st)
    msg = f"undid the {last['action']} from {last['ts']}"
    log(msg)
    ctx.say(msg)
    return msg


def output_balance_db(ctx, st) -> dict:
    """Each output's loudness at the seat (its speakers summed), against the others' average."""
    lv = st["calibration"]["levels"]
    per = {}
    for o in ctx.setup.outputs:
        p = sum(10 ** (float(lv.get(s.id, 0.0)) / 10) for s in o.speakers)
        per[o.id] = 10 * math.log10(p) if p > 0 else -120.0
    mean = sum(per.values()) / len(per) if per else 0.0
    return {k: round(v - mean, 1) for k, v in per.items()}
