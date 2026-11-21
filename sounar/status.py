"""What Sounar is doing right now: `sounar status` (for people) and `sounar status --json` (for the
bar panel and anyone building a widget; the contract is docs/status-json.md)."""
from pathlib import Path

from sounar import paths, roommeasure, syncpoints
from sounar.core import current_plan, leveling_target, output_balance_db, sync_text, verify
from sounar.effects import side_gain
from sounar.layouts import loudness_db
from sounar.pipewire import pct
from sounar.presets import PARAMS, applicable
from sounar.state import effective, load_state, measuring

VERSION = 1


def _files():
    root = Path(__file__).resolve().parent.parent
    return {"setup": str(paths.setup_file()), "presets": str(paths.user_presets_file()),
            "log": str(paths.state_dir() / "log"), "docs": str(root / "README.md")}


def _effects(ctx, eff, st):
    ee = ctx.ee
    live = ee.current() if ee.available() else None
    return {"available": ee.available(), "preset": eff.ee, "preset_default": eff.base_ee, "live": live,
            "presets": ee.presets() if ee.available() else [], "bypass": ee.bypass(),
            "leveling": ee.leveling(live) if live else False,
            "target": leveling_target(ctx, eff, st) if ctx.setup else None, "target_live": ee.target_live()}


def status_json(ctx) -> dict:
    st = load_state(ctx.presets)
    eff = effective(st, ctx.presets)
    tw = st["tweaks"].get(eff.id, {})
    out = {
        "version": VERSION,
        "setup": {"done": ctx.setup is not None, "file": str(paths.setup_file())},
        "preset": eff.id, "previous": st.get("previous"),
        "label": eff.label, "tagline": eff.tagline, "icon": eff.icon,
        "layout": eff.layout, "live_layout": eff.layout, "note": "", "applied": False,
        "measuring": measuring(),
        "presets": [{"id": k, "label": v.label, "tagline": v.tagline, "icon": v.icon, "layout": v.layout,
                     "ee": v.ee, "tweaked": bool(st["tweaks"].get(k))} for k, v in ctx.presets.items()],
        "params": [], "tweaked": bool(tw),
        "effects": _effects(ctx, eff, st),
        "loudness_db": None, "outputs": [], "sync_text": "", "sync": {"undo": None, "stable": None},
        "room": roommeasure.info(st),
        "calibration": {"source": st["calibration"]["source"], "ts": st["calibration"]["ts"],
                        "measured": st["calibration"]["ts"] is not None},
        "files": _files(),
    }
    if ctx.setup is None:
        return out
    p = current_plan(ctx, st, eff)
    out.update(live_layout=p.layout, note=p.note, applied=bool(p.outputs) and not verify(ctx, st),
               sync_text=sync_text(ctx.setup, st),
               sync=syncpoints.info(st, lambda pt: sync_text(ctx.setup, {"sync_ms": pt["sync_ms"]})))
    out["loudness_db"] = round(loudness_db(ctx.setup, eff, st, side_gain(ctx.ee.data(eff.ee) if eff.ee else {})), 1)
    for k in applicable(ctx.setup, eff.layout):
        if k in eff.params:
            pr = PARAMS[k]
            out["params"].append({"key": k, "label": pr.label, "unit": pr.unit, "step": pr.step,
                                  "advanced": pr.advanced, "hint": pr.hint, "value": eff.params[k],
                                  "preset": eff.base[k], "min": pr.lo, "max": pr.hi, "tweaked": k in tw})
    sinks = {s.name: s for s in ctx.pw.sinks()}
    delays = {sid: ms for o in p.outputs for sid, ms in o.delays_ms.items()}
    bal = output_balance_db(ctx, st)
    lv, offs = st["calibration"]["levels"], st["calibration"].get("offsets_ms", {})
    for o in ctx.setup.outputs:
        s = sinks.get(o.node)
        out["outputs"].append({
            "id": o.id, "name": " + ".join(x.name for x in o.speakers), "label": o.label,
            "connected": s is not None, "volume": pct(s.vol_db) if s else None,
            "codec": s.codec.upper() if s else "", "bus": s.bus if s else "",
            "sync_ms": float(st["sync_ms"].get(o.id, 0.0)), "balance_db": bal.get(o.id, 0.0),
            "speakers": [{"id": x.id, "name": x.name, "nickname": x.nickname, "at": x.at, "zone": x.zone,
                          "channels": x.channels, "level_db": lv.get(x.id), "offset_ms": offs.get(x.id),
                          "delay_ms": round(delays[x.id], 1) if x.id in delays else None} for x in o.speakers]})
    return out


def _hour(at):
    return "sub" if at is None else f"{at:g} o'clock"


def status_text(ctx) -> str:
    j = status_json(ctx)
    lines = [f"Sounar: {j['label']} ({j['preset']}), {j['live_layout']} layout"
             + ("" if j["applied"] or not j["outputs"] else "  [not applied: `sounar apply`]")]
    if j["tagline"]:
        lines.append(f"  {j['tagline']}")
    if not j["setup"]["done"]:
        lines.append("  No setup yet: run `sounar setup` to tell Sounar about your speakers.")
        return "\n".join(lines)
    if j["note"]:
        lines.append(f"  {j['note']}")
    if j["params"]:
        lines.append("  " + "  ".join(f"{p['key']} {p['value']:g}{'*' if p['tweaked'] else ''}" for p in j["params"])
                     + ("   (* = fine-tuned, `sounar reset` undoes)" if j["tweaked"] else ""))
    e = j["effects"]
    if e["available"]:
        lines.append(f"  Easy Effects: {e['live'] or 'not running'}"
                     + ("" if e["live"] == e["preset"] else f" (preset wants {e['preset']})")
                     + (f", leveling {e['target_live']} LUFS" if e["target_live"] is not None else "")
                     + (", effects off" if e["bypass"] else ""))
    else:
        lines.append("  Easy Effects: not installed (layouts, balance and delay still work)")
    lines.append("  Speakers:")
    for o in j["outputs"]:
        state = f"{o['volume']}%, {o['codec'] or o['bus']}" if o["connected"] else "not connected"
        for sp in o["speakers"]:
            nick = f" \"{sp['nickname']}\"" if sp["nickname"] else ""
            delay = f", delay {sp['delay_ms']:.1f} ms" if sp["delay_ms"] is not None else ""
            lines.append(f"    {sp['name']}{nick} ({_hour(sp['at'])}): {state}{delay}")
    lines.append(f"  Sync: {j['sync_text']}")
    if j["sync"]["stable"]:
        s = j["sync"]["stable"]
        lines.append(f"    stable sync: {s['text']}, from the {s['what']} at {s['ts'][11:16]}"
                     + (" (active)" if s["active"] else " (`sounar sync stable` goes back to it)"))
    if j["sync"]["undo"]:
        u = j["sync"]["undo"]
        lines.append(f"    sync undo: back to {u['text']}, from before the {u['what']} at {u['ts'][11:16]}")
    c = j["calibration"]
    lines.append(f"  Calibration: {c['source']}" + (f", {c['ts']}" if c["ts"] else " (run `sounar calibrate`)"))
    return "\n".join(lines)
