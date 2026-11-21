# Changelog

All notable changes are listed here. Versions follow [Semantic Versioning](https://semver.org).

## Unreleased

- **Three presets instead of nine** (Music, Immersive, Movie): one per thing you do, each clearly
  different. The old ones are in docs/presets.md to paste into your own file; importing a
  prototype keeps them.
- **Stable sync and sync undo:** `sounar sync stable` goes back to the last sync that settled
  (a calibration or a `sounar sync` that ended in sync); `sounar sync undo` steps back through
  syncs, calibrations and by-ear nudges. Neither plays anything; nothing syncs on its own.
- **Guard:** restarts the audio stack when PipeWire stops answering (after WirePlumber crashed
  at suspend), restarts Easy Effects when its output is linked to nothing, and loads the active
  preset's effects into a newly started Easy Effects.
- **Room measurement** (`sounar room measure`, about 30 s): the room's echo time, the surfaces
  around the seat (a sonar sweep from the computer's built-in speaker), rough speaker distances,
  and at most two gentle bass cuts for booms every speaker shares at the seat (never a boost),
  measured without the room's own cuts. `sounar room correction on|off`, `sounar room walls`.
- **Setup:** writes a WirePlumber drop-in that keeps the Bluetooth monitor running when the
  session goes inactive at suspend (it crashed WirePlumber on some machines).

## 0.1.0

First public version, grown out of a one-room prototype.

- Speakers are named by where they sit (a clock face around your seat), so any room works:
  stereo pair, pair plus rear, two pairs, left-centre-right, pair with a subwoofer, and more.
- Three layouts (stereo as wired, room, spread) and nine presets, each with its Easy Effects
  preset and a matched song-leveling target. Live fine-tuning, saved per preset.
- `sounar setup` finds the outputs and asks where each speaker sits; `sounar calibrate`
  measures level and distance with the microphone; `sounar sync` re-aligns after a Bluetooth
  reconnect.
- The guard service repairs routing, volumes and filters after reconnects, silently.
- `sounar status --json` (version 1) for widgets; an Omarchy bar panel and menu entries.
- `sounar doctor` for bug reports, with device addresses and names stripped.
- Installer and uninstaller, per user, no sudo.
