# The upmixer: placing sounds

In the room layout, **Placement** (0–1, a slider in the panel; `sounar place +0.1` or
`sounar tune place=0.6`) decides how much each sound comes from its own direction. At 0 you hear
the plain room mix; Music uses 0.6, Immersive 0.8.

## What it does

Many times a second the music is split into about a thousand frequency slices. For each slice:

- **Direct or ambient.** What both channels share is a direct sound (a voice, a guitar); what
  they don't is ambience (reverb, room). A sound in one channel only is direct too, and content
  that is out of phase between two equally loud channels counts as wide ambience.
- **Where it sits.** A direct sound's left-right position picks a direction on the stage between
  your outermost front speakers, with the centre straight ahead. It plays from the two speakers
  around that direction, so a centred voice comes from both speakers in front of you rather than
  from one. **Wrap** carries the stage's edges towards a speaker beside or behind (one within
  120°), so a hard-panned sound leans that way; there is no wrap towards a side with no speaker.
- **Ambience.** Half stays in the front speakers (left ones take the left channel's, right ones
  the right's: that keeps the sound full and wide), the rest goes to the surround speakers, with
  their usual ambience band, vocal-band dip and delay.
- **Bass** stays mono on every speaker, as in the room layout, and the vocal lift stays on the
  centre.

The result is mixed with the plain room mix (`1 - Placement` of it), and each speaker's level is
matched slowly (over a few seconds) to what the room mix gives it. That matters: without it, a
speaker that mostly plays the side signal in the room mix (a front speaker on one side) would
lose most of its sound and the music would seem to shrink.

## Where it runs

`sounar_upmix` is a small LADSPA plugin (C, in `sounar/upmix/`), one instance in each front and
surround speaker's filter graph (a lone stereo box gets two, one per channel; a subwoofer
none). Every instance knows the whole speaker map (up to 8 chains) and renders its own speaker's
share. It adds 2048 samples (42.7 ms at 48 kHz) to every speaker alike, so sync is unchanged; a
subwoofer waits the same time; the stereo layout and measurements don't use it, so they keep
their timing. It costs a few percent of one CPU core per speaker.

`install.sh` builds it when a C compiler and `make` are there and installs it in
`~/.local/lib/ladspa`, with a drop-in that puts that folder on WirePlumber's `LADSPA_PATH` (in an
output's filter graph PipeWire only searches there, even for an absolute path). Without the plugin,
Placement isn't offered and the room layout plays its plain mix.

## Settings

| Value | Range | |
|---|---|---|
| `place` | 0–1 | how much each sound comes from its own direction (0 = the plain room mix) |
| `wrap` | 0–1 | how far sounds panned to one side reach towards a speaker beside or behind |
