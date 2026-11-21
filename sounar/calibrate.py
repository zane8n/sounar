"""`sounar sync` (after a reconnect, ~15 s) and `sounar calibrate` (once, and after moving a
speaker, ~1 min). See docs/measuring.md."""
import math

from sounar import SounarError, syncpoints
from sounar.core import apply_effects, snapshot
from sounar.graph import MAX_DELAY
from sounar.measure import Session, measure_arrivals, measure_levels
from sounar.pipewire import db_to_raw, pct
from sounar.state import effective, history_append, load_state, log, now, save_state

SYNC_OK_MS = 3.0            # residual gap that needs no correction (graph delays are exact)
SPREAD_LIMIT_MS = 10.0      # readings disagreeing more than this = a Bluetooth hiccup: change nothing
BALANCE_TOLERANCE_DB = 1.0
MAX_STEP_DB = 6.0


def new_sync(sync_ms, arrivals_ms) -> dict:
    """Sync delays that cancel the measured arrivals. Each arrival includes the output's current
    delay, so the link itself is arrival - delay; only the earlier links get delayed."""
    raw = {k: float(sync_ms.get(k, 0.0)) - float(a) for k, a in arrivals_ms.items()}
    lo, top = min(raw.values()), MAX_DELAY * 1000 - 60
    return {k: round(min(top, v - lo), 1) for k, v in raw.items()}


def plan_balance(vol_db, levels, max_step=MAX_STEP_DB) -> list:
    """Volume changes (dB) that even the outputs at the mic: quieter ones go up towards 100 %
    first, then louder ones come down (at most max_step per run), so nothing passes 100 %."""
    headroom = [max(0.0, -v) for v in vol_db]
    target = min(lv + h for lv, h in zip(levels, headroom, strict=True))
    return [max(-max_step, min(h, target - lv)) for lv, h in zip(levels, headroom, strict=True)]


def _outputs(ctx):
    present = {s.name for s in ctx.pw.sinks()}
    feeds = {f.sink: f for f in ctx.pw.feeds(ctx.setup.combine_node)}
    outs = [o for o in ctx.setup.outputs if o.node in present and o.node in feeds]
    return outs, [feeds[o.node] for o in outs], {o.id: i for i, o in enumerate(outs)}


def _sync_loop(ctx, session, outs, feeds, index, passes=3, first=None, what="sync"):
    """Measure, correct, verify. Each pass is saved as it goes, and the undo point is kept with
    the first change, so a run that breaks off part way can still be undone."""
    steps, kept = [], False
    for n in range(passes):
        if n == 0 and first:
            arr = first
        else:
            got = measure_arrivals(ctx, [o.reference for o in outs], 5, feeds, index)
            arr = {o.id: got[o.reference.id] for o in outs}
        arrivals = {k: v[0] for k, v in arr.items()}
        spread = max(v[1] for v in arr.values())
        gap = max(arrivals.values()) - min(arrivals.values())
        late = max(arrivals, key=arrivals.get)
        st = load_state(ctx.presets)
        ctx.say(f"  pass {n + 1}: {ctx.setup.output_of(late).label} {gap:.1f} ms behind (spread {spread:.1f} ms), "
                f"delays {st['sync_ms'] or 'none'}")
        if spread > SPREAD_LIMIT_MS:
            raise SounarError(f"readings unsteady (±{spread:.0f} ms) - nothing changed; try again")
        steps.append(gap)
        if gap < SYNC_OK_MS:
            return gap, steps
        before = dict(st["sync_ms"])
        st["sync_ms"] = new_sync({o.id: st["sync_ms"].get(o.id, 0.0) for o in outs}, arrivals)
        if not kept:
            syncpoints.remember(st, what, before)
            kept = st["sync_ms"] != before
        save_state(st)
        session.apply_stereo()
    return steps[-1], steps


def sync(ctx) -> str:
    outs, feeds, index = _outputs(ctx)
    if len(outs) < 2:
        raise SounarError("sync needs at least two outputs connected")
    ctx.tell("Syncing speakers…", "Chirps for about 15 seconds, music paused")
    with Session(ctx, "sync") as se:
        before = dict(load_state(ctx.presets)["sync_ms"])
        res, steps = _sync_loop(ctx, se, outs, feeds, index)
    st = load_state(ctx.presets)
    if res < SYNC_OK_MS:                    # settled: what `sounar sync stable` goes back to
        syncpoints.mark_stable(st, "sync")
        save_state(st)
    log(f"sync: {', '.join(f'{s:+.1f}' for s in steps)} ms; delays {before} -> {st['sync_ms']}")
    if res < SYNC_OK_MS:
        msg = f"Within {res:.0f} ms" + (f" (was {steps[0]:.0f} ms off)" if len(steps) > 1 else ", nothing to change")
        ctx.tell("Speakers in sync", msg)
    else:
        msg = (f"Still {res:.0f} ms apart after {len(steps)} passes; run `sounar sync` again, "
               "or go back with `sounar sync stable`")
        ctx.tell("Speaker sync incomplete", msg)
    ctx.say(msg)
    return msg


def calibrate(ctx) -> str:
    outs, feeds, index = _outputs(ctx)
    missing = [o.label for o in ctx.setup.outputs if o not in outs]
    if missing:
        raise SounarError(f"connect every speaker before calibrating (missing: {', '.join(missing)})")
    ctx.tell("Calibrating speakers…", "About a minute of chirps and noise, music paused. Stay quiet")
    res = 0.0
    with Session(ctx, "calibrate") as se:
        spk = [s for o in outs for s in o.speakers]
        ctx.say("timing of each speaker…")
        arr = measure_arrivals(ctx, spk, 5, feeds, index)
        offsets = {s.id: round(arr[s.id][0] - arr[o.reference.id][0], 1) for o in outs for s in o.speakers}
        for sid, off in offsets.items():
            if off:
                ctx.say(f"  {sid} arrives {off:+.1f} ms after its output's front speaker")
        if len(outs) > 1:
            res, _ = _sync_loop(ctx, se, outs, feeds, index, first={o.id: arr[o.reference.id] for o in outs},
                                what="calibrate")
        ctx.say("level of each speaker…")
        levels, ptp = measure_levels(ctx, spk, feeds, index)
        ctx.say("  " + "   ".join(f"{k} {v:+.1f} dB (±{ptp[k] / 2:.1f})" for k, v in levels.items()))
        sinks = {s.name: s for s in ctx.pw.sinks()}
        out_lv = [10 * math.log10(sum(10 ** (levels[s.id] / 10) for s in o.speakers)) for o in outs]
        deltas = [0.0] * len(outs)
        desc = ""
        if len(outs) > 1 and max(out_lv) - min(out_lv) >= BALANCE_TOLERANCE_DB:
            deltas = plan_balance([sinks[o.node].vol_db for o in outs], out_lv)
            before = snapshot(list(sinks.values()))
            was = {o.node: sinks[o.node].vol_db for o in outs}
            for o, d in zip(outs, deltas, strict=True):
                if abs(d) >= 0.05:
                    ctx.pw.set_volume(o.node, db_to_raw(was[o.node] + d))
            desc = ", ".join(f"{o.label} {pct(was[o.node])}%→{pct(was[o.node] + d)}%"
                             for o, d in zip(outs, deltas, strict=True) if abs(d) >= 0.05)
            history_append("calibrate", before, snapshot(ctx.pw.sinks()), f"sounar calibrate: {desc}")
            ctx.say(f"  balance: {desc}")
        lv = {s.id: levels[s.id] + deltas[i] for i, o in enumerate(outs) for s in o.speakers}
        ref = lv[spk[0].id]
        st = load_state(ctx.presets)
        st["calibration"] = {"levels": {k: round(v - ref, 2) for k, v in lv.items()}, "offsets_ms": offsets,
                             "source": "sounar calibrate", "ts": now()}
        if res < SYNC_OK_MS:               # settled: what `sounar sync stable` goes back to
            syncpoints.mark_stable(st, "calibrate")
        save_state(st)
    st = load_state(ctx.presets)
    apply_effects(ctx, effective(st, ctx.presets), st, load=False)
    c = st["calibration"]
    log(f"calibrate: levels {c['levels']}, offsets {c['offsets_ms']}, sync {st['sync_ms']} (residual {res:+.1f})")
    msg = (f"Levels {', '.join(f'{k} {v:+.1f} dB' for k, v in c['levels'].items())}; "
           f"sync within {abs(res):.0f} ms" + (f"; {desc}" if desc else ""))
    ctx.tell("Speakers calibrated", msg)
    ctx.say(msg)
    return msg
