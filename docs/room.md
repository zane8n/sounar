# The room: measurement and gentle correction

`sounar room measure` listens to your room for about 30 seconds (music paused, stay quiet and
seated) and then cuts the room's worst bass booms at your seat, gently. `sounar room` shows what
it found.

## What it measures

1. **The computer's sonar.** A sweep from the computer's built-in speaker, recorded on its
   microphone. Reflections that reach both channels of a two-channel microphone as the same wave
   are surfaces: the floor or table below, the ceiling, the nearest walls. The two channels tell
   a left wall from a right one; front and back can't be told apart, so the nearer centred wall
   is guessed to be ahead. Diffuse reverb is a different wave at each channel and is ignored.
2. **Each speaker alone.** A second of very quiet noise first (some speakers' amplifiers sleep
   after a few seconds of silence and would swallow the start), then a sweep. From each:
   - the **echo time** (RT60, per octave band, median over everything heard);
   - a **rough distance**, from how loud the direct sound is against the room's reverb (the
     critical distance from the room's volume and echo time). Bluetooth adds a delay of its own to
     each speaker that can't be read, so timing can't give the distance. Speakers on one output
     share a link, so their calibrated arrival differences are exact distance differences, and
     their estimates are fitted to keep them. Expect the estimates to be off by a third or more
     (furniture or your own body between a speaker and the microphone makes it worse);
   - the **bass response at your seat**.

## The correction (deliberately small)

In the room layout every speaker plays the same bass, so only booms they **all** share at your
seat are cut, and the same cut goes on every output, after its own correction EQ:

- at most two cuts, each half of the boom's excess and at most 4 dB, never a boost;
- the "excess" is measured against a running median, so a deep dip at your seat (a cancellation)
  doesn't make the peaks look bigger than they are;
- the room is always measured **without** the room's own cuts (with them on, a second
  measurement would only hear what was left and replace the cuts that worked).

`sounar room correction off` turns the cuts off (`on` brings them back); measuring again keeps
your choice.

## Walls by hand

`sounar room walls <left> <right> <ahead> <behind>` sets the walls in metres from your seat (the
Omarchy panel's map does this with drag handles). Walls you place win over the sonar's guesses in
later measurements and give the distance estimates a better room volume.

## Status

`sounar status --json` has a `room` object: the echo time, the box (`width`, `length`, `height`,
the seat at `laptop.x` from the left wall and `laptop.y` from the wall ahead, `user` when placed
by hand), the cuts, `correction`, and per speaker `dist_est`.
