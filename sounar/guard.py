"""`sounar guard`: the background service that keeps the speaker setup as it should be. It plays
no sound and never measures. Every few seconds, and right after device changes or when the
computer wakes from a stall, it checks and quietly repairs:

  - the default output is the combined output (a reconnecting speaker can grab the default)
  - Easy Effects runs and plays into the combined output (not straight into one speaker)
  - the combined output's feeds are unmuted at 100 % (an interrupted measurement can leave one muted)
  - each output's latency offset is the recorded one; after a reconnect, its volume too
  - every output runs the active preset's graph, and Easy Effects' leveling target matches
  - streams left muted by an interrupted measurement are unmuted
  - the keep-alive stream runs (Bluetooth links change their delay whenever their stream stops)
  - a new Easy Effects process runs the active preset's effects (it starts on its last-saved one)
    and its output is linked (a WirePlumber restart can leave it running into nothing: silence)
  - the audio server answers: if checks keep failing and `pactl` itself hangs (seen after
    WirePlumber crashed at suspend), it restarts PipeWire, its pulse server and WirePlumber, then
    Easy Effects and the keep-alive, at most once per 10 minutes, and says so

Left alone on purpose: the combined output's own volume and mute (your volume keys), and volume
changes you make by hand while a speaker stays connected. After a Bluetooth speaker reconnects
its delay may have shifted; that can only be measured with sound, so the guard sends one
notification suggesting `sounar sync` instead."""
import os
import shutil
import subprocess
import threading
import time

from sounar import SounarError, core
from sounar.measure import KEEPALIVE, left_muted, unmute_streams
from sounar.state import history, log, measuring

INTERVAL = 5            # seconds between routine checks
GRAPH_EVERY = 6         # check graphs every N routine checks (reading them is heavier)
STALL_S = 3.0           # woke this much later than planned = the computer stalled or slept
EE_UNIT = "app-com.github.wwmm.easyeffects@autostart.service"
KEEPALIVE_UNIT = "sounar-keepalive.service"
AUDIO_STACK = ("pipewire.service", "pipewire-pulse.service", "wireplumber.service")
STUCK_AFTER = 2         # failed checks in a row before asking whether the audio server hangs
RESTART_GAP_S = 600     # at most one automatic audio restart per 10 minutes


def pulse_ok(timeout=8) -> bool:
    """The pulse side of PipeWire answers (it hung for hours after WirePlumber crashes)."""
    try:
        return subprocess.run(["pactl", "info"], capture_output=True, timeout=timeout).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _systemctl(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, check=False)


class Guard:
    def __init__(self, make_ctx, settle=2.0, pulse_ok=pulse_ok, systemctl=_systemctl, clock=time.monotonic,
                 sleep=time.sleep, notify=None):
        self.make_ctx, self.settle = make_ctx, settle
        self.pulse_ok, self.systemctl, self.clock, self.sleep = pulse_ok, systemctl, clock, sleep
        self.notify = notify
        self.known = None            # setup outputs present last pass
        self.reconnected = set()
        self.wake = threading.Event()
        self.fails = 0               # failed checks in a row
        self.last_restart = None     # clock() of the last automatic audio restart
        self.gave_up = False
        self.ee_pid = None           # the Easy Effects process last seen
        self.ee_unlinked = 0         # checks in a row that found its output linked to nothing

    def check(self, graphs=True) -> list:
        if measuring():
            return []
        ctx = self.make_ctx()
        if ctx.setup is None:
            return []
        msgs = []
        nodes = {o.node for o in ctx.setup.outputs}
        present = {s.name for s in ctx.pw.sinks()} & nodes
        if self.known is not None:
            new = present - self.known
            self.reconnected |= new
            if new and self.settle:
                time.sleep(self.settle)          # let WirePlumber and the combined output settle
        self.known = present
        sinks = {s.name: s for s in ctx.pw.sinks()}
        if ctx.setup.combine_node not in sinks:
            return []
        self._default(ctx, nodes, msgs)
        self._effects(ctx, nodes, msgs)
        self._feeds(ctx, msgs)
        self._offsets_volumes(ctx, sinks, msgs)
        self._keepalive(ctx, sinks, msgs)
        self._mutes(ctx, msgs)
        if graphs or self.reconnected:
            m = core.repair(ctx)
            if m:
                msgs.append(m)
        back = self.reconnected & present
        if back:
            self.reconnected -= back
            outs = [o for o in ctx.setup.outputs if o.node in back]
            names = ", ".join(o.label for o in outs)
            msgs.append(f"{names} reconnected: settings restored")
            if any(sinks[o.node].bus == "bluetooth" for o in outs if o.node in sinks):
                body = (f"{names}: settings restored. Its Bluetooth delay may have shifted; "
                        "run `sounar sync` when convenient")
                ctx.notify("Speaker reconnected", body, "audio-speakers")
        for m in msgs:
            log(f"guard: {m}")
        return msgs

    def _default(self, ctx, nodes, msgs):
        d = ctx.pw.default_sink()
        if d in nodes:
            ctx.pw.set_default_sink(ctx.setup.combine_node)
            msgs.append(f"default output was one speaker; set back to {ctx.setup.combine_node}")

    def _effects(self, ctx, nodes, msgs):
        if not ctx.ee.available():
            return
        if hasattr(ctx.ee, "running") and not ctx.ee.running():
            ctx.pw.systemctl("start", EE_UNIT)
            msgs.append("Easy Effects wasn't running; started it")
            return
        pid = ctx.ee.pid() if hasattr(ctx.ee, "pid") else None
        if pid and pid != self.ee_pid:
            m = core.restore_effects(ctx)
            if m is not None:
                self.ee_pid = pid
                if m:
                    msgs.append(m)
        targets, cur = [], None
        for line in ctx.pw.links().splitlines():
            if not line.startswith(" "):
                cur = line.strip()
            elif cur and cur.startswith("ee_soe_output_level:output_") and "|->" in line:
                targets.append(line.split("|->")[1].strip().split(":")[0])
        if not targets:
            # running but feeding nothing; restart it if that lasts two checks (it links a moment
            # after it starts)
            if hasattr(ctx.pw, "ports_out") and "ee_soe_output_level:output_" in ctx.pw.ports_out():
                self.ee_unlinked += 1
                if self.ee_unlinked >= 2:
                    self.ee_unlinked = 0
                    ctx.pw.systemctl("restart", EE_UNIT)
                    msgs.append("Easy Effects was running with its output linked to nothing (silence); restarted it")
            return
        self.ee_unlinked = 0
        if not any(t == ctx.setup.combine_node for t in targets) and any(t in nodes for t in targets):
            ctx.pw.systemctl("restart", EE_UNIT)
            msgs.append("Easy Effects was playing into one speaker only; restarted it")

    def _feeds(self, ctx, msgs):
        for f in ctx.pw.feeds(ctx.setup.combine_node):
            if f.mute:
                ctx.pw.set_feed_mute(f.index, False)
                msgs.append(f"feed to {f.sink} was muted; unmuted")
            if f.volume_raw != 65536:
                ctx.pw.set_feed_volume(f.index, 65536)
                msgs.append(f"feed to {f.sink} was at {round(100 * f.volume_raw / 65536)}%; set to 100%")

    def _offsets_volumes(self, ctx, sinks, msgs):
        h = history()
        exp = h[-1]["after"] if h else None
        if not exp:
            return
        for o in ctx.setup.outputs:
            s = sinks.get(o.node)
            if not s:
                continue
            want = exp.get("offsets", {}).get(f"{s.card}:{s.port}")
            if want is not None and want != s.offset_us:
                ctx.pw.set_port_offset(s.card, s.port, want)
                msgs.append(f"{o.label} latency offset was {s.offset_us // 1000} ms; restored {want // 1000} ms")
            if s.name in self.reconnected:
                want = exp.get("volumes", {}).get(s.name)
                if want is not None and abs(want - s.volume_raw) > 200:
                    ctx.pw.set_volume(s.name, want)
                    msgs.append(f"{o.label} came back at {round(100 * s.volume_raw / 65536)}%; restored")

    def _keepalive(self, ctx, sinks, msgs):
        if not any(sinks[o.node].bus == "bluetooth" for o in ctx.setup.outputs if o.node in sinks):
            return
        if not any(s.media in KEEPALIVE for s in ctx.pw.streams(ctx.setup.combine_node)):
            ctx.pw.systemctl("restart", KEEPALIVE_UNIT)
            msgs.append("keep-alive stream was missing; restarted it (sync drifts without it)")

    def _mutes(self, ctx, msgs):
        left = left_muted()
        if left:
            unmute_streams(ctx.pw, left, ctx.setup.combine_node, settle=0)
            msgs.append(f"{len(left)} app stream(s) left muted by an interrupted measurement; unmuted")

    # ------------------------------------------------------------ the service
    def safe_check(self, graphs=True):
        """One pass that never dies on a transient PipeWire error, and logs a repeated error
        (a broken setup.toml, say) once instead of every few seconds."""
        try:
            msgs = self.check(graphs)
            self.last_error = None
            self.fails = 0
            return msgs
        except Exception as e:
            err = f"{e}" if isinstance(e, SounarError) else repr(e)
            if err != getattr(self, "last_error", None):
                log(f"guard: check failed: {err}")
                self.last_error = err
            self.fails += 1
            if self.fails >= STUCK_AFTER:
                try:
                    self.recover()
                except Exception as e2:
                    log(f"guard: audio recovery failed: {e2!r}")
            return []

    def _tell(self, title, body):
        if self.notify:
            self.notify(title, body)
        else:
            from sounar.desktop import notify
            notify(title, body)

    def recover(self) -> str:
        """Checks keep failing. If `pactl` itself hangs, the audio server is stuck: restart the
        stack and what plays through it. Returns what happened ("" = not stuck)."""
        if self.pulse_ok():
            return ""
        t = self.clock()
        if self.last_restart is not None and t - self.last_restart < RESTART_GAP_S:
            if not self.gave_up:
                self.gave_up = True
                log("guard: the audio server is stuck again right after a restart; leaving it")
                self._tell("Audio is stuck", "By hand: systemctl --user restart pipewire pipewire-pulse wireplumber")
            return "gave up"
        self.last_restart, self.gave_up = t, False
        log("guard: the audio server stopped answering (pactl hangs); restarting PipeWire")
        self._tell("Audio stuck: restarting it", "PipeWire stopped answering; restarting the audio services")
        keepalive = self.systemctl("is-active", KEEPALIVE_UNIT)
        users = [EE_UNIT] + ([KEEPALIVE_UNIT] if (getattr(keepalive, "stdout", "") or "").strip() == "active" else [])
        self.systemctl("restart", *AUDIO_STACK)
        for _ in range(20):
            self.sleep(1)
            if self.pulse_ok(3):
                break
        else:
            log("guard: PipeWire was restarted but still doesn't answer")
            self._tell("Audio is stuck", "PipeWire was restarted but still doesn't answer")
            return "failed"
        self.systemctl("restart", *users)
        self.known, self.ee_pid, self.fails = None, None, 0
        log(f"guard: audio restarted ({', '.join(AUDIO_STACK + tuple(users))})")
        self._tell("Audio is back", "PipeWire was stuck and has been restarted")
        return "restarted"

    def _events(self):
        """Wake on device changes (not on the constant volume/level 'change' events)."""
        if not shutil.which("pactl"):
            return
        p = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE, text=True)
        for line in p.stdout:
            if ("'new'" in line or "'remove'" in line) and (" sink " in line or " card " in line
                                                          or "sink-input" in line):
                self.wake.set()

    def run(self):
        log("guard: started")
        threading.Thread(target=self._events, daemon=True).start()
        n = 0
        while True:
            self.safe_check(graphs=(n % GRAPH_EVERY == 0))
            n += 1
            planned = time.monotonic() + INTERVAL
            woke = self.wake.wait(INTERVAL)
            self.wake.clear()
            late = time.monotonic() - planned
            if not woke and late > STALL_S:
                log(f"guard: computer stalled for ~{late + INTERVAL:.0f} s; checking everything")
                n = 0
            elif woke:
                time.sleep(1.5)                 # let the device change settle
                n = 0


def keepalive(ctx):
    """`sounar keepalive` (sounar-keepalive.service): silence into the combined output, forever."""
    comb = ctx.setup.combine_node
    for _ in range(30):
        if comb in {s.name for s in ctx.pw.sinks()}:
            break
        time.sleep(1)
    os.execvp("pw-cat", ["pw-cat", "--playback", "--raw", "--format", "s16", "--rate", "48000", "--channels", "2",
                         "--target", comb, "-P", f'{{ media.name = "{KEEPALIVE[0]}" node.name = "sounar_keepalive" '
                         'node.dont-reconnect = false }', "/dev/zero"])
