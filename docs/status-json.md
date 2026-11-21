# `sounar status --json`

Everything a widget needs, in one call (about a second). The bar panel uses only this and the
commands below, so any desktop widget can do what it does.

**Versioning:** `version` is 1. New fields may appear at any time; a renamed or removed field
means version 2.

```json
{
  "version": 1,
  "setup": {"done": true, "file": "~/.config/sounar/setup.toml"},
  "preset": "music-room", "previous": "tv",
  "label": "Music Room", "tagline": "Vocals up front, ...", "icon": "󰝚",
  "layout": "room",              // the preset's layout
  "live_layout": "room",         // what is playing: "stereo" while an output is missing
  "note": "",                    // e.g. "Some speakers are missing: plain stereo for now"
  "applied": true,               // every output runs the preset's graph
  "measuring": null,             // "sync" / "calibrate" while measuring: changes are refused
  "presets": [{"id", "label", "tagline", "icon", "layout", "ee", "tweaked"}],
  "params": [{"key": "vocal", "label": "Vocal presence", "unit": "dB", "step": 0.5,
              "advanced": false, "hint": "...", "value": 1.5, "preset": 1.0,
              "min": -3, "max": 6, "tweaked": true}],
  "tweaked": true,               // the active preset has fine-tuning
  "effects": {"available": true, "preset": "Sounar Room", "preset_default": "Sounar Room",
              "live": "Sounar Room", "presets": ["..."], "bypass": false, "leveling": true,
              "target": -12, "target_live": -12},
  "loudness_db": -1.4,           // modelled loudness against stereo (made up by the leveling target)
  "outputs": [{"id": "front-left", "name": "Front left", "label": "Front left",
               "connected": true, "volume": 100, "codec": "SBC", "bus": "bluetooth",
               "sync_ms": 0.0, "balance_db": 0.2,
               "speakers": [{"id": "front-left", "name": "Front left", "nickname": "",
                             "at": 11, "zone": "front", "channels": ["FL", "FR"],
                             "level_db": 0.0, "offset_ms": 0.0, "delay_ms": 3.0}]}],
  "sync_text": "Front right + Rear right waits 17.9 ms",
  "room": null,                  // after `sounar room measure`: echo, box, cuts, correction, rough
                                 // distances per speaker (docs/room.md)
  "sync": {                      // `sounar sync undo` / `sounar sync stable`; each may be null
    "undo": {"text": "…", "sync_ms": {…}, "what": "sync", "ts": "…", "steps": 2},
    "stable": {"text": "…", "sync_ms": {…}, "what": "calibration", "ts": "…", "active": true}
  },
  "calibration": {"source": "sounar calibrate", "ts": "2026-10-24T13:43:55", "measured": true},
  "files": {"setup": "...", "presets": "...", "log": "...", "docs": "..."}
}
```

`params` lists only the values that do something on this setup in this layout (no Room fill
without a speaker beside or behind you). Without a setup, `setup.done` is false and `outputs`
and `params` are empty.

## Commands a widget uses

| Do | Command |
|---|---|
| switch preset | `sounar -q set <id>`, `sounar -q back` |
| fine-tune | `sounar -q tune vocal=1.5 bass=2`, `sounar -q reset` |
| Easy Effects preset for this preset | `sounar -q effects "<name>"`, `sounar -q effects preset` |
| leveling / effects | `sounar -q effects leveling on|off`, `sounar -q effects off|on` |
| balance / sync by ear | `sounar -q balance <speaker> ±dB`, `sounar -q nudge <speaker> ±ms` |
| measure | `sounar sync`, `sounar calibrate` (they notify themselves) |
| sync back | `sounar -q sync undo`, `sounar -q sync stable` |

`-q` skips the notification: the widget shows the change itself. Changes are serialised by a
lock, and refused (exit 1, message on stdout) while a measurement runs.
