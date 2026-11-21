# Troubleshooting

Start with `sounar status` (what it thinks is playing) and `sounar doctor` (a report you can
paste into an issue). `sounar guard status` shows what the guard repaired recently.

**Voices sound doubled or smeared.** The outputs are out of sync, usually after a Bluetooth
speaker reconnected: `sounar sync`. Fine-tune by ear with `sounar nudge <speaker> ±1`.

**One side is louder.** `sounar calibrate`, or by ear: `sounar balance <speaker> ±0.5`.

**"chirps not heard clearly".** The volume was too low or the room too loud. Turn the
"Sounar" output up to a normal listening level, keep quiet, and try again. Nothing was changed.

**"readings unsteady".** A Bluetooth link hiccupped during the measurement. Try again; nothing
was changed.

**No sound from an app after setup.** Some apps (Spotify especially) keep the output they had
when PipeWire restarted: restart the app. Check that "Sounar" is the default output.

**A speaker is missing.** Room and spread presets play plain stereo on what's connected until it's
back; the guard re-applies everything when it reconnects.

**The panel or `sounar status` says "not applied".** Something replaced an output's filter graph
(usually a reconnect). The guard fixes it within seconds; `sounar apply` does it right away.

**Restarting WirePlumber or PipeWire.** Easy Effects can lose its connection to the "Sounar"
output: restart Easy Effects (the guard does this itself when it sees Easy Effects playing into
a single speaker). Apps may need restarting too (see above).

**Measuring with other sounds playing.** Sounar pauses media players and mutes other apps while it
measures. If a measurement was killed halfway, the guard unmutes them.

**Removing it.** `sounar uninstall` (add `--purge` to also remove your setup and calibration).
Then `systemctl --user restart pipewire pipewire-pulse wireplumber` removes the "Sounar" output.

Still stuck? Open a [bug report](https://github.com/zane8n/sounar/issues/new?template=bug_report.yml)
with the output of `sounar doctor`.

## No sound after the laptop wakes up (WirePlumber crashed at suspend)

On some machines (an NVIDIA driver, for one) the kernel switches the virtual terminal at every
suspend. For that moment the login session counts as inactive, and WirePlumber's seat
monitoring shut its whole Bluetooth monitor down while a speaker's link was dropping: WirePlumber
crashed in `libspa-bluez5`, and PipeWire stayed stuck (`pactl` hangs) until it was restarted.

- `sounar setup` writes `~/.config/wireplumber/wireplumber.conf.d/sounar-bluetooth.conf`, which
  turns seat monitoring off for Bluetooth (it only matters when several people share one
  machine's seats). Remove the file to bring it back.
- If the audio server hangs anyway, the guard notices (two failed checks, `pactl info` timing
  out), restarts PipeWire, its pulse server and WirePlumber, then Easy Effects and the
  keep-alive, and tells you. At most once per 10 minutes; by hand:
  `systemctl --user restart pipewire pipewire-pulse wireplumber`.

## Easy Effects plays the wrong effects after a restart

Easy Effects starts on its own last-saved preset, not the active Sounar preset's. The guard sees
a new Easy Effects process and loads the right one; it also restarts Easy Effects if it runs
with its output linked to nothing (what a WirePlumber restart can leave behind).
