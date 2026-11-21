"""sounar (sound + arrange): several speakers arranged into one system. A preset sets how the
speakers share the music (layout), the balance and delays that make them land together at your
seat, and the Easy Effects preset that goes with it.

  sounar                          status
  sounar list                     the presets
  sounar <preset> | set <preset>  switch preset (e.g. `sounar club`)
  sounar next | prev | back       cycle presets, or the one before this one
  sounar menu                     preset picker (Omarchy menu, Walker, Fuzzel, Rofi, Wofi, bemenu, dmenu)
  sounar <value> [+N|-N|=N]       fine-tune the active preset: vocal, bass, centre, fill, haas, width,
                                  dip, xover, amb_lo, amb_hi (+N/-N relative, =N absolute)
  sounar tune vocal=1.5 bass=2    several at once; `sounar reset` undoes the fine-tuning
  sounar effects "<name>"|preset  Easy Effects preset for the active preset (`preset` = its own)
  sounar effects off|on|next      Easy Effects bypass, or try the next Easy Effects preset
  sounar effects leveling on|off  song leveling (Autogain) in the presets Sounar uses
  sounar balance <speaker> ±dB    make that speaker's output louder or quieter
  sounar nudge <speaker> ±ms      sync by ear: that output plays later (+) or sooner (-)
  sounar sync                     re-sync after a Bluetooth reconnect (~15 s of chirps, music paused)
  sounar sync undo                back to the sync before the last sync, calibration or nudges
  sounar sync stable              back to the sync the last calibration or settled sync found
                                  (nothing ever syncs on its own; these play nothing)
  sounar calibrate                once, and after moving a speaker (~1 min of test sounds)
  sounar room                     the room: echo, size, rough speaker distances, bass cuts
  sounar room measure             ~30 s of sweeps (built-in speaker + each speaker), music paused
  sounar room correction on|off   the gentle bass cuts for the room's booms (on after measuring)
  sounar room walls L R A B       the walls by hand: metres from your seat to each
  sounar setup [--example NAME]   tell Sounar about your speakers (`--list-examples` shows the examples)
  sounar speakers                 the speaker map
  sounar status --json            everything, for widgets (docs/status-json.md)
  sounar doctor                   a report for bug reports (addresses and names stripped)
  sounar undo | history           undo the last volume change / list recent ones
  sounar apply | verify           re-apply the active preset / exit 0 if it is what's playing
  sounar guard run|check|status   the background repair service
  sounar menu-install             write the Omarchy menu rows
  sounar uninstall [--purge]      remove Sounar (keeps ~/.config/sounar unless --purge)
  sounar -q <command>             no notification (widgets show the change themselves)
  SOUNAR_DRY_RUN=1 sounar ...     print every change instead of making it

<speaker> is a speaker id such as front-left or rear-right (see `sounar speakers`) or a nickname.
"""
import json
import os
import sys

from sounar import SounarError, __version__, core, desktop, paths
from sounar.presets import PARAMS, load_presets
from sounar.state import history, load_state


def make_ctx(quiet=False, need_setup=True):
    return core.load_ctx(quiet=quiet, need_setup=need_setup)


def _number(v, what):
    try:
        return float(v)
    except (TypeError, ValueError):
        raise SounarError(f"{what} should be a number, like +1 or -0.5") from None


def _speakers(ctx):
    if ctx.setup is None:
        return "No setup yet: run `sounar setup`."
    lines = []
    for i, o in enumerate(ctx.setup.outputs, 1):
        lines.append(f"output {i}{' (' + o.node + ')' if o.node else ''}")
        for s in o.speakers:
            where = "subwoofer" if s.sub else f"{s.at:g} o'clock"
            extra = (", no bass boost" if not s.bass_boost else "") + (f', "{s.nickname}"' if s.nickname else "")
            lines.append(f"  {s.id:<14} {s.name:<12} {where:<12} channels {'/'.join(s.channels)}{extra}")
    return "\n".join(lines)


def _effects(ctx, args):
    sub = args[0] if args else ""
    if sub in ("off", "on"):
        ctx.ee.set_bypass(sub == "off")
        ctx.tell("Effects off" if sub == "off" else "Effects on",
                 "Pure signal (speaker correction stays on)" if sub == "off" else (ctx.ee.current() or ""))
    elif sub == "toggle":
        _effects(ctx, ["on" if ctx.ee.bypass() else "off"])
    elif sub == "leveling":
        on = args[1] if len(args) > 1 else "toggle"
        st = load_state(ctx.presets)
        eff = core.effective(st, ctx.presets)
        if on == "toggle":
            on = "off" if ctx.ee.leveling(eff.ee or "") else "on"
        names = sorted({p.ee for p in ctx.presets.values() if p.ee} | ({eff.ee} if eff.ee else set()))
        ctx.ee.set_leveling(on == "on", names)
        ctx.tell("Leveling on" if on == "on" else "Leveling off",
                 "Quiet songs lifted, loud ones tamed" if on == "on" else "Songs play at their own loudness")
    elif sub == "next":
        names = ctx.ee.presets()
        if not names:
            raise SounarError("no Easy Effects presets found")
        cur = ctx.ee.current()
        nxt = names[(names.index(cur) + 1) % len(names)] if cur in names else names[0]
        ctx.ee.load(nxt)
        ctx.tell(nxt, "Easy Effects preset (until the next Sounar switch)")
    elif args:
        core.set_effects(ctx, " ".join(args))
    else:
        raise SounarError("which Easy Effects preset? (`sounar effects \"<name>\"`, preset, off, on, next, leveling)")


def _uninstall(args):
    script = paths.data_dir() / "install.sh"
    if not script.exists():
        raise SounarError(f"the installer isn't at {script}; run ./install.sh --uninstall from your copy of Sounar")
    os.execv("/bin/sh", ["/bin/sh", str(script), "--uninstall", *args])


KNOWN = {"status", "-s", "list", "ls", "speakers", "set", "next", "prev", "back", "menu", "menu-install", "tune",
         "reset", "effects", "ee", "balance", "nudge", "sync", "calibrate", "apply", "verify", "is", "undo",
         "history", "doctor", "guard", "keepalive", "room"}


def run(cmd, a, quiet):
    params = set(PARAMS) | {"center"}
    if cmd not in KNOWN | params | {"-h", "--help", "help", "--version", "version", "uninstall", "setup"}:
        if cmd not in load_presets(paths.user_presets_file()):
            print(__doc__)
            return 2
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if cmd in ("--version", "version"):
        print(f"sounar {__version__}")
        return 0
    if cmd == "uninstall":
        _uninstall(a)
    if cmd == "setup":
        from sounar import wizard
        return wizard.main(make_ctx(quiet, need_setup=False), a)
    loose = cmd in ("status", "-s", "list", "ls", "doctor", "guard", "history", "menu-install", "speakers")
    ctx = make_ctx(quiet, need_setup=not loose)
    if cmd in ("status", "-s"):
        if "--json" in a:
            try:
                desktop.refresh_omarchy_menu(ctx.presets)
            except OSError:
                pass
            from sounar.status import status_json
            print(json.dumps(status_json(ctx), ensure_ascii=False))
        else:
            from sounar.status import status_text
            print(status_text(ctx))
    elif cmd in ("list", "ls"):
        cur = load_state(ctx.presets)["preset"]
        for pid, p in ctx.presets.items():
            mark = "▶" if pid == cur else " "
            print(f"{mark} {p.icon}  {pid:<11} {p.label:<13} {p.layout:<7} {p.ee or '':<22} {p.tagline}")
    elif cmd == "speakers":
        print(_speakers(ctx))
    elif cmd == "set" and len(a) == 1:
        core.switch(ctx, a[0])
    elif cmd in ("next", "prev"):
        core.step(ctx, 1 if cmd == "next" else -1)
    elif cmd == "back":
        core.back(ctx)
    elif cmd == "menu":
        act = desktop.menu(ctx.presets, load_state(ctx.presets)["preset"], ctx.setup.menu)
        if act and act[0] == "set":
            core.switch(ctx, act[1])
        elif act and act[0] == "sync":
            from sounar.calibrate import sync
            sync(ctx)
        elif act and act[0] == "calibrate":
            from sounar.calibrate import calibrate
            calibrate(ctx)
    elif cmd == "menu-install":
        desktop.write_omarchy_menu(desktop.omarchy_menu_file(), ctx.presets)
        print(f"menu: {len(ctx.presets)} presets in {desktop.omarchy_menu_file()}")
    elif cmd in params:
        core.tweak(ctx, cmd, a[0] if a else None)
    elif cmd == "tune":
        core.tune(ctx, a)
    elif cmd == "reset":
        core.reset(ctx)
    elif cmd in ("effects", "ee"):
        _effects(ctx, a)
    elif cmd == "balance":
        if len(a) != 2:
            raise SounarError("usage: sounar balance <speaker> ±dB (speakers: `sounar speakers`)")
        core.balance(ctx, a[0], _number(a[1], "the change"))
    elif cmd == "nudge":
        if len(a) != 2:
            raise SounarError("usage: sounar nudge <speaker> ±ms (speakers: `sounar speakers`)")
        core.nudge(ctx, a[0], _number(a[1], "the change"))
    elif cmd == "sync" and a[:1] == ["undo"]:
        core.undo_sync(ctx)
    elif cmd == "sync" and a[:1] == ["stable"]:
        core.stable_sync(ctx)
    elif cmd == "sync":
        from sounar.calibrate import sync
        sync(ctx)
    elif cmd == "calibrate":
        from sounar.calibrate import calibrate
        calibrate(ctx)
    elif cmd == "room":
        from sounar import roommeasure
        if a[:1] == ["measure"]:
            roommeasure.measure(ctx)
        elif a[:1] == ["correction"] and a[1:2] in (["on"], ["off"]):
            roommeasure.set_correction(ctx, a[1] == "on")
        elif a[:1] == ["walls"] and len(a) == 5:
            roommeasure.set_walls(ctx, *(_number(v, "a wall distance") for v in a[1:]))
        elif not a:
            ctx.say(roommeasure.room_text(ctx.setup, load_state(ctx.presets)))
        else:
            raise SounarError("usage: sounar room [measure | correction on|off | walls L R A B]")
    elif cmd == "apply":
        core.apply(ctx)
    elif cmd == "verify":
        bad = core.verify(ctx)
        print("ok" if not bad else "differs: " + ", ".join(bad))
        return 1 if bad else 0
    elif cmd == "is" and a:
        return 0 if load_state(ctx.presets)["preset"] == a[0] else 1
    elif cmd == "undo":
        core.undo(ctx)
    elif cmd == "history":
        n = int(a[a.index("-n") + 1]) if "-n" in a else 10
        for e in history()[-n:]:
            print(f"{e['ts']}  {e['action']:<10} {e['detail']}")
    elif cmd == "doctor":
        from sounar import doctor
        print(doctor.report(ctx))
    elif cmd == "guard":
        from sounar.guard import Guard
        sub = a[0] if a else "status"
        if sub == "run":
            Guard(lambda: make_ctx(True, need_setup=False)).run()
        elif sub == "check":
            for m in Guard(lambda: ctx, settle=0).check() or ["all good"]:
                print(m)
        else:
            print(f"sounar-guard: {'active' if ctx.pw.unit_active('sounar-guard.service') else 'not running'}")
            try:
                lines = [ln for ln in (paths.state_dir() / "log").read_text().splitlines() if "guard:" in ln]
                print("\n".join(lines[-15:]) or "(nothing logged yet)")
            except OSError:
                print("(nothing logged yet)")
    elif cmd == "keepalive":
        from sounar.guard import keepalive
        keepalive(ctx)
    elif cmd in ctx.presets:
        core.switch(ctx, cmd)
    else:
        print(__doc__)
        return 2
    return 0


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    quiet = False
    if a and a[0] in ("-q", "--quiet"):
        quiet, a = True, a[1:]
    cmd, rest = (a[0], a[1:]) if a else ("status", [])
    try:
        return run(cmd, rest, quiet)
    except SounarError as e:
        print(f"sounar: {e}")
        if not quiet and not sys.stdout.isatty():
            desktop.notify("Sounar", str(e))
        return 1
    except KeyboardInterrupt:
        return 130
