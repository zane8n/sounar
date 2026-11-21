"""The upmix plugin (sounar/upmix/sounar_upmix.c), loaded the way a LADSPA host loads it. Builds
it with `make` when there is a C compiler; skipped otherwise."""
import ctypes as C
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

SRC = Path(__file__).resolve().parent.parent / "sounar" / "upmix"
RATE = 48000
LAT = 2048


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if not shutil.which("cc") and not shutil.which("gcc"):
        pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("upmix") / "sounar_upmix.so"
    r = subprocess.run(["make", "-s", "-C", str(SRC), "sounar_upmix.so"], capture_output=True, text=True)
    if r.returncode:
        pytest.fail(r.stderr)
    shutil.copy(SRC / "sounar_upmix.so", out)
    return C.CDLL(str(out))


class Hint(C.Structure):
    _fields_ = [("HintDescriptor", C.c_int), ("LowerBound", C.c_float), ("UpperBound", C.c_float)]


class Desc(C.Structure):
    pass


Desc._fields_ = [
    ("UniqueID", C.c_ulong), ("Label", C.c_char_p), ("Properties", C.c_int),
    ("Name", C.c_char_p), ("Maker", C.c_char_p), ("Copyright", C.c_char_p),
    ("PortCount", C.c_ulong), ("PortDescriptors", C.POINTER(C.c_int)),
    ("PortNames", C.POINTER(C.c_char_p)), ("PortRangeHints", C.POINTER(Hint)),
    ("ImplementationData", C.c_void_p),
    ("instantiate", C.CFUNCTYPE(C.c_void_p, C.POINTER(Desc), C.c_ulong)),
    ("connect_port", C.CFUNCTYPE(None, C.c_void_p, C.c_ulong, C.POINTER(C.c_float))),
    ("activate", C.CFUNCTYPE(None, C.c_void_p)),
    ("run", C.CFUNCTYPE(None, C.c_void_p, C.c_ulong)),
    ("run_adding", C.c_void_p), ("set_run_adding_gain", C.c_void_p), ("deactivate", C.c_void_p),
    ("cleanup", C.CFUNCTYPE(None, C.c_void_p)),
]

# A map like a stereo box at 11 o'clock and a twin at 2 (front) and 4 (rear) o'clock:
# chains 1 and 2 front, 3 a surround. Base gains as the room layout gives them.
MAP = {"Count": 3, "Angle 1": -30, "Angle 2": 60, "Angle 3": 120, "Zone 1": 1, "Zone 2": 1, "Zone 3": 2}
BASE = {1: {"Base M": 1.03, "Base S": 0.515, "Bass": 1.0, "Base amb": 0.0},
        2: {"Base M": 0.78, "Base S": -1.55, "Bass": 1.88, "Base amb": 0.0},
        3: {"Base M": 0.0, "Base S": 0.0, "Bass": 1.88, "Base amb": 0.58}}


class Plugin:
    def __init__(self, lib, self_idx, controls=None, rate=RATE):
        lib.ladspa_descriptor.restype = C.POINTER(Desc)
        self.D = lib.ladspa_descriptor(0).contents
        self.names = [self.D.PortNames[i].decode() for i in range(self.D.PortCount)]
        self.h = self.D.instantiate(C.pointer(self.D), rate)
        self.ctl = {n: np.zeros(1, np.float32) for n in self.names if n not in ("In L", "In R", "Out", "Amb")}
        for n, a in self.ctl.items():
            self.D.connect_port(self.h, self.names.index(n), a.ctypes.data_as(C.POINTER(C.c_float)))
        base = {"Placement": 0.6, "Wrap": 0.0, "Ambience front": 0.5, "Crossover": 150, "Vocal": 0.0, "Match": 1,
                "Self": self_idx, **MAP, **BASE[self_idx]}
        self.set({**base, **(controls or {})})
        self.D.activate(self.h)

    def set(self, c):
        for k, v in c.items():
            self.ctl[k][0] = v

    def run(self, L, R, blocks=(1024,)):
        n = len(L)
        L, R = np.ascontiguousarray(L, np.float32), np.ascontiguousarray(R, np.float32)
        out, amb = np.zeros(n, np.float32), np.zeros(n, np.float32)
        i = j = 0
        while i < n:
            b = min(blocks[j % len(blocks)], n - i); j += 1
            for name, arr in (("In L", L), ("In R", R), ("Out", out), ("Amb", amb)):
                self.D.connect_port(self.h, self.names.index(name), arr[i:].ctypes.data_as(C.POINTER(C.c_float)))
            self.D.run(self.h, b)
            i += b
        return out, amb

    def close(self):
        self.D.cleanup(self.h)


def pink(n, seed):
    X = np.fft.rfft(np.random.default_rng(seed).standard_normal(n))
    f = np.arange(len(X)); f[0] = 1
    x = np.fft.irfft(X / np.sqrt(f), n)
    return (x / np.std(x) * 0.1).astype(np.float32)


def db(x):
    return 10 * np.log10(np.mean(np.asarray(x, float) ** 2) + 1e-30)


def hp(x, fc=300):
    X = np.fft.rfft(x); X[np.fft.rfftfreq(len(x), 1 / RATE) < fc] = 0
    return np.fft.irfft(X, len(x))


def each(lib, L, R, controls=None):
    """Every chain's Out and Amb (above 300 Hz, the settled part), in dB."""
    res = {}
    for k in (1, 2, 3):
        p = Plugin(lib, k, controls)
        o, a = p.run(L, R)
        p.close()
        s = slice(RATE // 2, None)
        res[k] = (db(hp(o[s])), db(hp(a[s])))
    return res


def test_ports_and_latency(lib):
    p = Plugin(lib, 1)
    assert p.D.Label == b"sounar_upmix" and p.names[:4] == ["In L", "In R", "Out", "Amb"]
    imp = np.zeros(RATE, np.float32); imp[1000] = 1
    p.set({"Placement": 0.0, "Base M": 1.0, "Base S": 0.0, "Bass": 1.0})
    o, _ = p.run(imp, imp)
    assert p.ctl["Latency"][0] == LAT and int(np.argmax(np.abs(o))) == 1000 + LAT
    p.close()


def test_placement_zero_is_the_room_mix(lib):
    n = RATE * 2
    L, R = pink(n, 1), pink(n, 2) * 0.7 + pink(n, 1) * 0.3
    f = np.fft.rfftfreq(n, 1 / RATE)
    lp = 1 / (1 + (f / 150) ** 4)
    M, S = np.fft.rfft((L + R) / 2), np.fft.rfft((L - R) / 2)
    seg, segd = slice(RATE // 2, n - RATE // 4), slice(RATE // 2 + LAT, n - RATE // 4 + LAT)
    for k in (1, 2, 3):
        b = BASE[k]
        ref_o = np.fft.irfft(b["Bass"] * M * lp + b["Base M"] * M * (1 - lp) + b["Base S"] * S * (1 - lp), n)
        ref_a = np.fft.irfft(b["Base amb"] * S * (1 - lp), n)
        p = Plugin(lib, k, {"Placement": 0.0})
        o, a = p.run(L, R)
        p.close()
        assert db(o[segd] - ref_o[seg]) - db(ref_o[seg]) < -40
        if b["Base amb"]:
            assert db(a[segd] - ref_a[seg]) - db(ref_a[seg]) < -40


def test_sounds_go_where_they_are_panned(lib):
    src = pink(RATE * 2, 8)
    z = np.zeros_like(src)
    only = {"Placement": 1.0, "Match": 0, "Bass": 0.0}
    left = each(lib, src, z, only)
    assert left[1][0] - max(left[2][0], left[3][0], left[3][1]) > 20             # hard left: the left front
    right = each(lib, z, src, only)
    assert right[2][0] - max(right[1][0], right[3][0], right[3][1]) > 20          # hard right: the right front
    centre = each(lib, src, src, only)
    assert abs(centre[1][0] - centre[2][0]) < 4 and centre[1][0] - centre[3][1] > 25   # from both fronts


def test_reverb_goes_to_the_surround(lib):
    src = pink(RATE * 2, 8)
    X = np.fft.rfft(src)
    other = np.fft.irfft(np.abs(X) * np.exp(2j * np.pi * np.random.default_rng(9).random(len(X))), len(src))
    r = each(lib, src, other.astype(np.float32), {"Placement": 1.0, "Match": 0, "Bass": 0.0, "Ambience front": 0.0})
    assert r[3][1] - max(r[1][0], r[2][0]) > 8


def test_wrap_reaches_the_surround_only_on_its_side(lib):
    src = pink(RATE * 2, 8)
    z = np.zeros_like(src)
    c = {"Placement": 1.0, "Match": 0, "Bass": 0.0, "Wrap": 0.5}
    right = each(lib, z, src, c)
    assert right[3][0] > right[2][0] - 12                                          # leans to the rear unit
    left = each(lib, src, z, c)
    assert left[3][0] < left[1][0] - 40                                            # nothing over there


def test_every_chain_keeps_its_loudness(lib):
    n = RATE * 20
    voice, gtr, keys, vl, vr = (pink(n, s) for s in (11, 12, 13, 14, 15))
    L = voice + 0.9 * gtr + 0.3 * keys + 0.5 * vl
    R = voice + 0.2 * gtr + 0.9 * keys + 0.5 * vr
    def levels(x):
        out = {}
        for k in (1, 2, 3):
            p = Plugin(lib, k, {"Placement": x})
            o, a = p.run(L, R)
            p.close()
            s = slice(RATE * 12, None)
            out[k] = 10 * np.log10(10 ** (db(hp(o[s])) / 10) + 10 ** (db(hp(a[s])) / 10))
        return out
    base, placed = levels(0.0), levels(1.0)
    assert all(abs(placed[k] - base[k]) < 1.5 for k in (1, 2, 3)), (base, placed)


def test_survives_bad_input_and_any_block_size(lib):
    p = Plugin(lib, 2)
    bad = pink(RATE, 3); bad[5000:5100] = np.nan; bad[9000] = np.inf
    o, a = p.run(bad, bad)
    assert np.isfinite(o).all() and np.isfinite(a).all()
    p.close()
    L, R = pink(RATE, 6), pink(RATE, 7)
    p1, p2 = Plugin(lib, 3), Plugin(lib, 3)
    a1 = p1.run(L, R, (RATE,)); a2 = p2.run(L, R, (1, 7, 64, 333, 1000, 4096))
    assert all(np.array_equal(x, y) for x, y in zip(a1, a2, strict=True))
    p1.close(); p2.close()


def test_one_front_speaker_carries_the_stage(lib):
    src = pink(RATE * 2, 8)
    c = {"Count": 2, "Angle 1": 0, "Zone 1": 1, "Angle 2": 180, "Zone 2": 2, "Placement": 1.0, "Match": 0,
         "Bass": 0.0, "Ambience front": 1.0}
    p = Plugin(lib, 1, c)
    o, _ = p.run(src, np.zeros_like(src))
    p.close()
    assert db(hp(o[RATE // 2:])) > -40
