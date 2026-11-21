# Presets

The built-in presets are in `sounar/data/presets.toml`. Yours go in
`~/.config/sounar/presets.toml`, in the same format: add new ones, or change fields of a
built-in one by using its id. The menu and the panel follow by themselves.

```toml
# A new preset
[kitchen]
label = "Kitchen"
tagline = "Background music while cooking"
icon = "󰆘"                 # any Nerd Font glyph
layout = "spread"
width = 0.2
ee = "Sounar Late Night"

# Changes to a built-in one
[music-room]
bass = 4
ee = "My Bass EQ"

# Hide one you never use
[immersive]
hidden = true
```

Sounar ships three presets (Music, Immersive, Movie): one per thing you do, each clearly
different. Until 0.2 it shipped nine; the ones that went, ready to paste into your file:

```toml
[club]
label = "Club"
tagline = "Afrobeats, amapiano, hip-hop, EDM: deep, punchy bass"
icon = "󰓃"
layout = "room"
centre = 6
fill = -10
haas = 12
vocal = 1.0
bass = 3.0
xover = 160
dip = -5
ee = "Sounar Room"

[late-night]
label = "Late Night"
tagline = "Low volume: clear vocals, even loudness, no boom"
icon = "󰖔"
layout = "room"
centre = 6
fill = -12
haas = 12
vocal = 1.5
bass = 0
dip = -5
ee = "Sounar Late Night"

[party]
label = "Party"
tagline = "Anywhere in the room: every speaker plays the whole song"
icon = "󰍖"
layout = "spread"
width = 0.5
ee = "Sounar Default"
```

(Vocal Focus, Concert Hall and TV are in `tests/fixtures/prototype/presets-0.1.toml`.)

| Setting | |
|---|---|
| `label`, `tagline`, `icon` | how it's shown |
| `layout` | `stereo`, `room` or `spread` ([layouts](layouts.md)) |
| `ee` | the Easy Effects preset loaded with it (optional) |
| values | for `room`: `vocal`, `bass`, `centre`, `fill`, `haas`, `dip`, `xover`, `amb_lo`, `amb_hi`; for `spread`: `width` |

| Value | Range | Meaning |
|---|---|---|
| `vocal` | −3 to 6 dB | presence lift on the centre only |
| `bass` | −6 to 6 dB | bass of the speakers that may be boosted |
| `centre` | 0 to 15 dB | how much more of the vocals the speaker you face carries |
| `fill` | −24 to 0 dB | ambience from beside and behind, as it reaches your seat |
| `haas` | 5 to 30 ms | how late the ambience arrives |
| `dip` | −12 to 0 dB | ambience cut around 2.5 kHz |
| `xover` | 80 to 250 Hz | below this every speaker plays the same bass |
| `amb_lo`, `amb_hi` | 120–600 / 5000–16000 Hz | the ambience band |
| `place` | 0 to 1 | placement: how much each sound comes from its own direction ([upmixer](upmix.md)) |
| `wrap` | 0 to 1 | how far sounds panned to one side reach towards a speaker beside or behind |
| `width` | 0 to 1 | spread: 0 = mono everywhere, 1 = full stereo |

Values your speakers can't use (no rear speaker means no `fill`) are simply ignored.

**Fine-tuning** (`sounar vocal +0.5`, the panel's sliders) is saved per preset in Sounar's state,
not in this file; `sounar reset` undoes it. Copy a value you like into this file to make it the
preset's own.

**Song leveling** (Easy Effects' Autogain) is on in all the shipped Easy Effects presets. Sounar
moves its target per preset so presets play equally loud; if you use your own Easy Effects
preset, put Autogain first in its chain for the same effect.

Made a preset others would enjoy? [Share it](../CONTRIBUTING.md).
