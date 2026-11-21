"""Sync undo points and the stable sync: `sounar sync undo` and `sounar sync stable`.

Every sync, calibration, run of by-ear nudges and switch to stable leaves an undo point: the
delays from before it. The stable sync is the set of delays the last measurement settled on (a
calibration, or a `sounar sync` that ended in sync). Nudges, undo and a sync that didn't settle
never change it, so it's the fixed point to go back to. Nothing ever syncs on its own: delays
change only when you ask.

Points belong to one set of outputs: a point saved while another output was connected (a TV
joined for a while, say) holds delays for a different system and is not offered."""
import time
from datetime import datetime

from sounar.state import now

NUDGE_RUN_S = 60         # nudges this close together are one undo step
KEEP = 20                # undo points kept
WHAT = {"sync": "sync", "calibrate": "calibration", "nudge": "by-ear changes", "stable": "switch to stable"}


def remember(st, what, before) -> None:
    """Keep `before` as an undo point if the delays in `st` moved away from it."""
    if st.get("sync_ms", {}) == before:
        return
    pts = st.setdefault("sync_undo", [])
    last = pts[-1] if pts else None
    t = now()
    if (what == "nudge" and last and last["what"] == "nudge"
            and time.time() - datetime.fromisoformat(last["last"]).timestamp() < NUDGE_RUN_S):
        last["last"] = t                 # one run of clicks is one step back
        return
    pts.append({"what": what, "ts": t, "last": t, "sync_ms": dict(before)})
    del pts[:-KEEP]


def points(st) -> list:
    """The undo points that still apply (same outputs as now), oldest first."""
    keys = set(st.get("sync_ms", {}))
    pts = [p for p in st.get("sync_undo", []) if _same_outputs(p["sync_ms"], keys)]
    while pts and pts[-1]["sync_ms"] == st.get("sync_ms", {}):      # nudged away and back again
        pts.pop()
    return pts


def mark_stable(st, what) -> None:
    """A measurement just settled on the delays in `st`: they become the stable sync."""
    st["sync_stable"] = {"what": what, "ts": now(), "sync_ms": dict(st.get("sync_ms", {}))}


def stable_point(st):
    """The stable sync, or None (nothing settled yet, or it was for other outputs)."""
    s = st.get("sync_stable")
    return s if s and _same_outputs(s["sync_ms"], set(st.get("sync_ms", {}))) else None


def _same_outputs(sync_ms, keys) -> bool:
    """Delays for the same outputs as now. None at all (a fresh install) fits any outputs."""
    return not sync_ms or not keys or set(sync_ms) == keys


def describe(p, text) -> str:
    return f"{text}, from the {WHAT.get(p['what'], p['what'])} at {p['ts'][11:16]} of {p['ts'][:10]}"


def info(st, text_of) -> dict:
    """For `sounar status --json`: the next undo step and the stable sync."""
    pts = points(st)
    undo = None
    if pts:
        u = pts[-1]
        undo = {"text": text_of(u), "sync_ms": u["sync_ms"], "what": WHAT.get(u["what"], u["what"]),
                "ts": u["ts"], "steps": len(pts)}
    s = stable_point(st)
    stable = None
    if s:
        stable = {"text": text_of(s), "sync_ms": s["sync_ms"], "what": WHAT.get(s["what"], s["what"]),
                  "ts": s["ts"], "active": s["sync_ms"] == st.get("sync_ms", {})}
    return {"undo": undo, "stable": stable}
