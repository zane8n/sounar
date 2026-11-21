"""Measuring with the microphone at the seat: test signals, playing them from one speaker at a
time, and finding when and how loud each one arrived.

One speaker plays at a time: its output's feed from the combined output is soloed (the others
muted) and the test track carries the signal only on the channels that feed that speaker.
Recordings stay in ~/.cache/sounar and are deleted after analysis."""
import json
import os
import subprocess
import time
import wave

import numpy as np

from sounar import SounarError, paths
from sounar.state import Lock, Measuring, load_state

RATE = 48000
CHIRP_SPACING = 1.0      # seconds between chirps
CHIRP_LEAD = 1.0         # first chirp time
REC_LEAD = 0.5           # the recording starts this much before playback
BAL_SEG = 2.5            # seconds of noise per speaker
BAL_WIN = (1.0, 2.3)     # analysed part of each segment (clear of Bluetooth latency + switching)
BAL_BAND = (200.0, 5000.0)
KEEPALIVE = ("Sounar keep-alive", "Speaker keep-alive")


# ---------------------------------------------------------------- signals and maths

def chirp(dur=0.08, f0=600.0, f1=9000.0):
    t = np.arange(int(dur * RATE)) / RATE
    k = np.log(f1 / f0) / dur
    sig = np.sin(2 * np.pi * f0 * (np.exp(k * t) - 1) / k)
    return sig * np.hanning(len(sig))


def pink_noise(seconds, lo=80.0, hi=12000.0, rms=0.12):
    n = int(seconds * RATE)
    spec = np.fft.rfft(np.random.default_rng(1).standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / RATE)
    spec *= np.where((f >= lo) & (f <= hi), 1 / np.sqrt(np.maximum(f, 1)), 0)
    x = np.fft.irfft(spec, n)
    return x * (rms / np.sqrt(np.mean(x ** 2)))


def band_db(x, lo, hi):
    spec = np.fft.rfft(x * np.hanning(len(x)))
    f = np.fft.rfftfreq(len(x), 1 / RATE)
    return float(10 * np.log10(np.sum(np.abs(spec[(f >= lo) & (f <= hi)]) ** 2) + 1e-20))


def detect_arrivals(rec, starts, c) -> list:
    """Arrival (ms after each chirp was sent, recording lead included) or None per chirp.
    A matched filter finds the chirps; each one gets its own window (0.55-1.45 s after it was
    sent, which covers playback + Bluetooth latency), and the onset is the first sample at half
    of that chirp's peak, so reflections and stray clicks don't fool it."""
    nfft = 1 << int(np.ceil(np.log2(len(rec) + len(c))))
    env = np.abs(np.fft.irfft(np.fft.rfft(rec, nfft) * np.conj(np.fft.rfft(c, nfft)), nfft)[:len(rec)])
    out = []
    for s in starts:
        lo, hi = min(len(env), s + int(0.55 * RATE)), min(len(env), s + int(1.45 * RATE))
        seg = env[lo:hi]
        if len(seg) == 0 or seg.max() < 8 * np.median(seg):
            out.append(None)
            continue
        out.append((lo + int(np.argmax(seg >= 0.5 * seg.max())) - s) / RATE * 1000)
    return out


def _gains(speaker):
    ch = speaker.channels
    return (1.0 if "FL" in ch or "MONO" in ch else 0.0), (1.0 if "FR" in ch or "MONO" in ch else 0.0)


# ---------------------------------------------------------------- playing and recording

def write_stereo(path, L, R):
    pcm = (np.clip(np.stack([L, R], axis=1), -1, 1) * 32767 * 0.8).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def read_wav(path):
    """Mono: (samples,). More channels: (samples, channels)."""
    with wave.open(str(path)) as w:
        ch = w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float) / 32768
    return x if ch == 1 else x.reshape(-1, ch)


class Recorder:
    """Plays a stereo test track into the combined output while recording the mic. switches =
    [(t, feed index or None)]: at t seconds after playback starts, solo that feed (None = all)."""
    def __init__(self, pw, combine, mic):
        self.pw, self.combine, self.mic = pw, combine, mic

    def _solo(self, feeds, idx):
        for j, f in enumerate(feeds):
            self.pw.set_feed_mute(f.index, idx is not None and j != idx)

    def play_record(self, L, R, switches, feeds, target=None, channels=1):
        d = paths.cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        play, rec = d / "test.wav", d / "test_rec.wav"
        write_stereo(play, L, R)
        self._solo(feeds, switches[0][1])
        r = subprocess.Popen(["pw-record", "--target", self.mic, "--rate", str(RATE), "--channels", str(channels),
                              "--format", "s16", str(rec)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(REC_LEAD)
            t0 = time.monotonic()
            p = subprocess.Popen(["pw-play", "--target", target or self.combine, str(play)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for t, idx in switches[1:]:
                time.sleep(max(0, t0 + t - time.monotonic()))
                self._solo(feeds, idx)
            p.wait()
            time.sleep(0.8)
        finally:
            r.terminate(); r.wait()
            self._solo(feeds, None)
        try:
            return read_wav(rec)
        finally:
            for f in (play, rec):
                f.unlink(missing_ok=True)


def _recorder(ctx):
    if ctx.recorder is None:
        ctx.recorder = Recorder(ctx.pw, ctx.setup.combine_node, ctx.setup.mic or ctx.pw.default_source())
    return ctx.recorder


def measure_arrivals(ctx, speakers, repeats, feeds, out_index) -> dict:
    """Chirps from one speaker at a time. Returns {speaker id: (median arrival ms, MAD ms)}.
    out_index maps an output id to its feed's index in `feeds`."""
    oi = {s.id: out_index[ctx.setup.output_of(s.id).id] for s in speakers}
    pattern = [s for _ in range(repeats) for s in speakers]
    c = chirp()
    n = int((CHIRP_LEAD + CHIRP_SPACING * len(pattern) + 1.0) * RATE)
    L, R, starts = np.zeros(n), np.zeros(n), []
    for k, s in enumerate(pattern):
        s0 = int((CHIRP_LEAD + k * CHIRP_SPACING) * RATE)
        lg, rg = _gains(s)
        L[s0:s0 + len(c)] += c * lg; R[s0:s0 + len(c)] += c * rg
        starts.append(s0)
    switches = [(0, oi[pattern[0].id])] + [(CHIRP_LEAD + (k - 1) * CHIRP_SPACING + 0.7, oi[pattern[k].id])
                                           for k in range(1, len(pattern))]
    x = _recorder(ctx).play_record(L, R, switches, feeds)
    found = detect_arrivals(x, starts, c)
    arr = {s.id: [] for s in speakers}
    for s, a in zip(pattern, found, strict=True):
        if a is not None:
            arr[s.id].append(a)
    if any(len(a) < max(2, repeats // 2) for a in arr.values()):
        raise SounarError("chirps not heard clearly - is the volume up and the room quiet?")
    return {k: (float(np.median(a)), float(np.median(np.abs(np.array(a) - np.median(a))))) for k, a in arr.items()}


def measure_levels(ctx, speakers, feeds, out_index, repeats=2):
    """Pink noise from one speaker at a time; dB over 200 Hz-5 kHz at the mic, and the spread."""
    oi = {s.id: out_index[ctx.setup.output_of(s.id).id] for s in speakers}
    pattern = [s for _ in range(repeats) for s in speakers]
    lead = 0.5
    noise = pink_noise(lead + BAL_SEG * len(pattern) + 1.0)
    L, R = np.zeros_like(noise), np.zeros_like(noise)
    for k, s in enumerate(pattern):
        a, b = int((lead + k * BAL_SEG) * RATE), int((lead + (k + 1) * BAL_SEG) * RATE)
        lg, rg = _gains(s)
        L[a:b] = noise[a:b] * lg; R[a:b] = noise[a:b] * rg
    switches = [(0, oi[pattern[0].id])] + [(lead + k * BAL_SEG, oi[pattern[k].id]) for k in range(1, len(pattern))]
    x = _recorder(ctx).play_record(L, R, switches, feeds)
    lv = {s.id: [] for s in speakers}
    for k, s in enumerate(pattern):
        s0 = REC_LEAD + lead + k * BAL_SEG
        a, b = int((s0 + BAL_WIN[0]) * RATE), int((s0 + BAL_WIN[1]) * RATE)
        lv[s.id].append(band_db(x[a:b], *BAL_BAND))
    return {k: float(np.mean(v)) for k, v in lv.items()}, {k: float(np.ptp(v)) for k, v in lv.items()}


# ---------------------------------------------------------------- quiet while measuring

def _muted_file():
    return paths.state_dir() / "muted.json"


class PausedPlayers:
    """Pause every playing MPRIS media player for the measurement, resume afterwards."""
    def __enter__(self):
        self.paused = []
        out = subprocess.run(["busctl", "--user", "list", "--no-legend"], capture_output=True, text=True,
                             check=False).stdout
        for line in out.splitlines():
            name = line.split()[0] if line.split() else ""
            if not name.startswith("org.mpris.MediaPlayer2."):
                continue
            st = subprocess.run(["busctl", "--user", "get-property", name, "/org/mpris/MediaPlayer2",
                                 "org.mpris.MediaPlayer2.Player", "PlaybackStatus"], capture_output=True,
                                text=True, check=False).stdout
            if "Playing" in st:
                self._call(name, "Pause")
                self.paused.append(name)
        if self.paused:
            time.sleep(1)
        return self

    def __exit__(self, *exc):
        for name in self.paused:
            self._call(name, "Play")

    @staticmethod
    def _call(name, method):
        subprocess.run(["busctl", "--user", "call", name, "/org/mpris/MediaPlayer2",
                        "org.mpris.MediaPlayer2.Player", method], capture_output=True, check=False)


def mute_other_streams(pw, combine) -> list:
    """Mute every app still playing (a browser tab or AirPlay source that ignores Pause would
    play over the test signals). Noted in muted.json so the guard can unmute after a crash."""
    muted = []
    for s in pw.streams(combine):
        if s.mute or s.corked or s.media in KEEPALIVE:
            continue
        pw.set_stream_mute(s.index, True)
        muted.append({"index": s.index, "app": s.app})
    if muted:
        paths.state_dir().mkdir(parents=True, exist_ok=True)
        _muted_file().write_text(json.dumps(muted))
    return muted


def unmute_streams(pw, entries, combine="sounar", settle=1.5) -> None:
    """Unmute what mute_other_streams muted, plus any stream of the same app that came back
    muted (a paused player makes a new stream on Play and the app's stored mute comes with it)."""
    if entries:
        apps = {e["app"] for e in entries if e.get("app")}
        for e in entries:
            pw.set_stream_mute(e["index"], False)
        for attempt in range(2):
            if attempt and settle:
                time.sleep(settle)
            for s in pw.streams(combine):
                if s.mute and s.app in apps:
                    pw.set_stream_mute(s.index, False)
    _muted_file().unlink(missing_ok=True)


def left_muted() -> list:
    try:
        return json.loads(_muted_file().read_text())
    except (OSError, ValueError):
        return []


class Session:
    """Everything a measurement needs and puts back, whatever happens: other changes wait,
    players pause, other apps are muted, the outputs play the plain stereo layout (with the
    current sync); afterwards the active preset, the mutes and the players come back."""
    def __init__(self, ctx, kind):
        self.ctx, self.kind = ctx, kind
        self.marker = self.players = None
        self.muted = []

    def __enter__(self):
        ctx = self.ctx
        if ctx.pw.dry_run:
            raise SounarError("measuring plays test sounds, so it can't be a dry run")
        with Lock():
            self.marker = Measuring(self.kind).__enter__()
        try:
            self.players = (ctx.extra.get("players") or PausedPlayers()).__enter__()
            self.muted = mute_other_streams(ctx.pw, ctx.setup.combine_node)
            self.apply_stereo()
        except BaseException:
            self.__exit__()
            raise
        return self

    def apply_stereo(self):
        from sounar.core import apply_plan, current_plan
        st = load_state(self.ctx.presets)
        if self.kind == "room" and st.get("room"):
            # the room is measured without its own bass cuts: with them on, a second measurement
            # heard only what was left and replaced the cuts that worked
            st["room"] = {**st["room"], "correction": False}
        apply_plan(self.ctx, current_plan(self.ctx, st, force_layout="stereo"))
        time.sleep(self.ctx.settle and 0.6)

    def __exit__(self, *exc):
        from sounar.core import apply_plan, current_plan
        ctx = self.ctx
        try:
            apply_plan(ctx, current_plan(ctx, load_state(ctx.presets)))
            for f in ctx.pw.feeds(ctx.setup.combine_node):
                if f.mute:
                    ctx.pw.set_feed_mute(f.index, False)
        finally:
            try:
                unmute_streams(ctx.pw, self.muted, ctx.setup.combine_node, settle=ctx.settle and 1.5)
            finally:
                if self.players:
                    self.players.__exit__(*exc)
                if self.marker:
                    self.marker.__exit__()


def recordings_left() -> bool:
    d = paths.cache_dir()
    return any(os.path.exists(d / n) for n in ("test.wav", "test_rec.wav"))
