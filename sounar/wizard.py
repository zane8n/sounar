"""`sounar setup`: find the outputs, play a soft tone on each so you know which is which, ask
where every speaker sits (an hour on a clock face around your seat), then write the setup, the
combined "Sounar" output and the services, and offer to calibrate. Nothing changes the sound
until you say yes to restarting PipeWire."""
import time

import numpy as np

from sounar import SounarError, paths
from sounar.measure import RATE, write_stereo
from sounar.speakers import Output, Setup, Speaker, assign_ids, examples, load_setup, save_setup

MIN_PIPEWIRE = (1, 4, 0)
WHERE = ("Where is it? An hour on a clock face around your seat: 12 = straight ahead, 3 = your right, "
         "6 = behind you, 9 = your left (11.5 is fine), or s for a subwoofer: ")


def candidates(pw) -> list:
    """Real outputs: not Easy Effects, not a combined output, not other virtual sinks."""
    return [s for s in pw.sinks() if s.bus != "virtual" and not s.name.startswith("easyeffects")]


def _check_pipewire(pw):
    v = pw.version()
    if v < MIN_PIPEWIRE:
        raise SounarError(f"Sounar needs PipeWire 1.4 or newer (this is {'.'.join(map(str, v))}): "
                          "it puts its filters inside each output, which older versions can't do")


def _tone(pw, sink, channel=None):
    """A soft 440 Hz tone, 1 s, -30 dBFS, on one channel (L/R) or both, straight to one output."""
    t = np.arange(RATE) / RATE
    x = 10 ** (-30 / 20) * np.sin(2 * np.pi * 440 * t) * np.hanning(RATE) ** 0.25 / 0.8
    L = x if channel in (None, "FL") else np.zeros_like(x)
    R = x if channel in (None, "FR") else np.zeros_like(x)
    d = paths.cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = d / "tone.wav"
    write_stereo(f, L, R)
    pw.play(f, sink)
    f.unlink(missing_ok=True)


def _choice(ask, q, valid):
    while True:
        a = ask(q).strip().lower()
        if a in valid:
            return a


def _yes(ask, q, default=True):
    a = _choice(ask, q + (" [Y/n] " if default else " [y/N] "), {"", "y", "yes", "n", "no"})
    return default if a == "" else a.startswith("y")


def _position(ask, say):
    while True:
        a = ask(WHERE).strip().lower()
        if a in ("s", "sub", "subwoofer"):
            return None, True
        try:
            h = float(a)
        except ValueError:
            say("  An hour from 0 to 12, please (or s).")
            continue
        if 0 <= h <= 12:
            return h, False
        say("  An hour from 0 to 12, please (or s).")


def _box(ask, say, channels):
    at, sub = _position(ask, say)
    limits = _yes(ask, "Does this speaker limit its own bass (it pumps or distorts when bass is boosted)?",
                  default=False)
    nick = ask("A name for it, if you like (Enter to skip): ").strip()
    return Speaker("", channels, at, sub, not limits, nick)


def _pick(ask, cands, q, many):
    while True:
        a = ask(q).strip().replace(",", " ")
        try:
            nums = [int(x) for x in a.split()]
        except ValueError:
            continue
        ok = nums and all(1 <= n <= len(cands) for n in nums) and len(set(nums)) == len(nums)
        if ok and (many or len(nums) == 1):
            return [cands[n - 1] for n in nums]


def _describe(s):
    return f"{s.description} ({s.bus}{', ' + s.codec.upper() if s.codec else ''})"


def _mic(ctx, ask, say):
    srcs = [s for s in ctx.pw.sources() if not s.startswith("easyeffects")]
    default = ctx.pw.default_source()
    if len(srcs) <= 1:
        return ""
    say("Microphones:")
    for i, s in enumerate(srcs, 1):
        say(f"  {i}) {s}{'  (default)' if s == default else ''}")
    while True:
        a = ask("Which microphone sits where you listen? (Enter = the default) ").strip()
        if a == "":
            return ""
        if a.isdigit() and 1 <= int(a) <= len(srcs):
            return srcs[int(a) - 1]


def run(ctx, ask=input, say=print, example=None) -> Setup:
    pw = ctx.pw
    _check_pipewire(pw)
    cands = candidates(pw)
    if not cands:
        raise SounarError("no outputs found: connect your speakers (Bluetooth, USB, ...) and run `sounar setup` again")
    say("Outputs PipeWire can play to:")
    for i, s in enumerate(cands, 1):
        say(f"  {i}) {_describe(s)}")
    outputs = []
    if example:
        ex = examples()
        if example not in ex:
            raise SounarError(f"no example setup '{example}'; choose from: {', '.join(ex)}")
        for o in load_setup(ex[example]).outputs:
            names = " + ".join(f"{s.name}" + (" (stereo box)" if s.stereo_box and len(o.speakers) == 1 else "")
                               for s in o.speakers)
            sink = _pick(ask, cands, f"Which output is the {names}? ", many=False)[0]
            outputs.append(Output(sink.name, o.speakers))
    else:
        chosen = _pick(ask, cands, "Which ones should Sounar use? (numbers, e.g. 1 2) ", many=True)
        for sink in chosen:
            say(f"\n{sink.description}: playing a soft tone…")
            _tone(pw, sink.name)
            while not _yes(ask, "Did you hear it?"):
                say("  Turn it up a little; playing it again…")
                _tone(pw, sink.name)
            if sink.channels == ["MONO"] or len(sink.channels) == 1:
                outputs.append(Output(sink.name, [_box(ask, say, ["MONO"])]))
                continue
            two = _choice(ask, "Is it one box (stereo or mono) [1], or do its left and right go to two "
                          "separate boxes [2]? ", {"1", "2"}) == "2"
            if not two:
                outputs.append(Output(sink.name, [_box(ask, say, ["FL", "FR"])]))
                continue
            spk = []
            for ch, side in (("FL", "left"), ("FR", "right")):
                say(f"  The box on its {side} channel: playing a tone there…")
                _tone(pw, sink.name, ch)
                spk.append(_box(ask, say, [ch]))
            outputs.append(Output(sink.name, spk))
    mic = _mic(ctx, ask, say)
    setup = Setup(outputs, mic)
    assign_ids(setup.speakers())
    say("\nYour speakers:")
    for s in setup.speakers():
        where = "subwoofer" if s.sub else f"{s.at:g} o'clock"
        say(f"  {s.name:<12} {where:<12} {s.nickname}")
    if not _yes(ask, "Save this setup?"):
        raise SounarError("setup not saved; nothing changed")
    return setup


# ---------------------------------------------------------------- the files setup writes

def combine_conf(setup) -> str:
    matches = " ".join(f'{{ media.class = "Audio/Sink" node.name = "{o.node}" }}' for o in setup.outputs)
    return f"""# Written by `sounar setup`: the combined "Sounar" output every app plays to. It feeds each
# of your speakers; Sounar sets what each one plays. Remove with `sounar uninstall`.
context.modules = [
    {{   name = libpipewire-module-combine-stream
        args = {{
            combine.mode = sink
            node.name = "{setup.combine_node}"
            node.description = "Sounar"
            combine.latency-compensate = true
            combine.props = {{ audio.position = [ FL FR ] }}
            stream.props = {{ }}
            stream.rules = [
                {{   matches = [ {matches} ]
                    actions = {{ create-stream = {{ }} }}
                }}
            ]
        }}
    }}
]
"""


def correction_conf(setup) -> str | None:
    """WirePlumber rule giving each output its correction EQ the moment it connects."""
    from sounar.graph import bq
    rules = {"bluez": [], "alsa": []}
    for o in setup.outputs:
        if not o.correction:
            continue
        filters = " ".join(bq(f["type"], f["freq"], f["gain"], f["q"]) for f in o.correction)
        graph = ("{ nodes = [ { type = builtin name = eq label = param_eq config = { filters = [ "
                 + filters + " ] } } ] }")
        kind = "bluez" if o.node.startswith("bluez_") else "alsa"
        rules[kind].append(f'  {{ matches = [ {{ node.name = "{o.node}" }} ]\n    actions = {{ update-props = {{\n'
                           f'      audioconvert.filter-graph.0 = "{graph}"\n    }} }} }}')
    if not any(rules.values()):
        return None
    out = ["# Written by `sounar setup`: each output's correction EQ, from the moment it connects."]
    for kind, rs in rules.items():
        if rs:
            out += [f"monitor.{kind}.rules = [", *rs, "]"]
    return "\n".join(out) + "\n"


BT_CONF = """# Written by `sounar setup`: keep WirePlumber's Bluetooth monitor running when the login session
# goes inactive. On some machines (an NVIDIA driver, say) the kernel switches VT at every suspend;
# for that moment logind reports the session as inactive and WirePlumber shut its Bluetooth
# monitor down while a speaker link was dropping. That crashed WirePlumber (libspa-bluez5) and
# left PipeWire stuck until a restart. Seat monitoring only matters for several people on one
# machine's seats. Remove this file to bring it back.
wireplumber.profiles = {
  main = {
    monitor.bluez.seat-monitoring = disabled
  }
}
"""


def bluetooth_conf(setup):
    """The WirePlumber Bluetooth drop-in, for setups with a Bluetooth output (else None)."""
    return BT_CONF if any(o.node.startswith("bluez_") for o in setup.outputs) else None


def finish(ctx, setup, ask=input, say=print) -> None:
    from sounar import core
    pw = ctx.pw
    _check_pipewire(pw)
    save_setup(setup, paths.setup_file())
    say(f"Saved {paths.setup_file()}")
    if setup.combine_managed:
        f = paths.xdg_config_home() / "pipewire/pipewire.conf.d/sounar.conf"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(combine_conf(setup))
    corr = correction_conf(setup)
    if corr:
        f = paths.xdg_config_home() / "wireplumber/wireplumber.conf.d/sounar-correction.conf"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(corr)
    bt = bluetooth_conf(setup)
    if bt:
        f = paths.xdg_config_home() / "wireplumber/wireplumber.conf.d/sounar-bluetooth.conf"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(bt)
    if not _yes(ask, "Sounar needs to restart PipeWire once so the \"Sounar\" output appears. Music apps "
                "(Spotify especially) may need a restart afterwards. Restart now?"):
        say("Nothing changed yet. When it suits you: systemctl --user restart pipewire pipewire-pulse "
            "wireplumber, then `sounar setup --finish`.")
        return
    activate(ctx, setup, ask, say, core)


def activate(ctx, setup, ask, say, core):
    pw = ctx.pw
    pw.systemctl("restart", "pipewire.service", "pipewire-pulse.service", "wireplumber.service")
    for _ in range(30):
        if setup.combine_node in {s.name for s in pw.sinks()}:
            break
        time.sleep(0.5)
    else:
        raise SounarError(f"the \"{setup.combine_node}\" output didn't appear; see `journalctl --user -u pipewire`")
    prev = pw.default_sink()
    if prev and prev != setup.combine_node:
        paths.state_dir().mkdir(parents=True, exist_ok=True)
        (paths.state_dir() / "previous-default").write_text(prev + "\n")
    pw.set_default_sink(setup.combine_node)
    units = ["sounar-guard.service"]
    if any(o.node.startswith("bluez_") for o in setup.outputs):
        units.append("sounar-keepalive.service")
    pw.systemctl("enable", "--now", *units)
    ctx.setup = setup
    core.apply(ctx)
    from sounar.state import effective, load_state
    st = load_state(ctx.presets)
    core.apply_effects(ctx, effective(st, ctx.presets), st)
    say("Sounar is on. Try `sounar list`, then `sounar <preset>`.")
    if _yes(ask, "Calibrate now? About a minute of chirps and noise: sit where you listen and keep the room "
            "quiet."):
        from sounar.calibrate import calibrate
        calibrate(ctx)
    else:
        say("Later: `sounar calibrate` (it balances and lines up the speakers for your seat).")


def main(ctx, args, ask=input, say=print) -> int:
    if "--list-examples" in args:
        for name, path in examples().items():
            first = path.read_text().splitlines()[0].lstrip("# ")
            say(f"{name:<28} {first}")
        return 0
    if "--finish" in args:
        if ctx.setup is None:
            raise SounarError("no setup yet; run `sounar setup`")
        from sounar import core
        activate(ctx, ctx.setup, ask, say, core)
        return 0
    example = args[args.index("--example") + 1] if "--example" in args and args.index("--example") + 1 < len(args) \
        else None
    if ctx.setup is not None and "--yes" not in args:
        if not _yes(ask, "You already have a setup. Replace it (calibration will need redoing)?", default=False):
            return 0
    setup = run(ctx, ask, say, example)
    finish(ctx, setup, ask, say)
    return 0
