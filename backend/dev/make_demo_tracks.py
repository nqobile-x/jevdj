"""Generate a small synthetic test library with known BPM and key.

python dev/make_demo_tracks.py [out_dir] [--minutes 2.5]

Each track: kick on every beat, offbeat hats, a bassline and chord pad in a known key, and a
"vocal" lead in the 300Hz-3kHz band only in the middle section, so intro/outro detection
has something real to find. Filenames carry the ground truth: "Demo - 113bpm Am (amapiano).wav".
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100
NOTE = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}

TRACKS = [
    (113, "A", "minor", "amapiano"),
    (113, "E", "minor", "amapiano"),
    (114, "C", "major", "amapiano"),
    (112, "D", "minor", "amapiano"),
    (115, "B", "minor", "amapiano"),
    (122, "G", "major", "afro house"),
    (122, "E", "minor", "afro house"),
    (123, "F#", "minor", "afro house"),
    (124, "D", "major", "deep house"),
    (120, "A", "minor", "deep house"),
    (121, "C", "minor", "deep house"),
    (92, "G", "minor", "hip hop"),
]


def freq(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    return np.minimum(1, t / max(attack, 1e-4)) * np.exp(-t / decay)


def make_track(bpm: float, tonic: str, mode: str, minutes: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    n_bars = int(minutes * 60 / (4 * beat))
    n_bars -= n_bars % 8
    total = int(n_bars * 4 * beat * SR) + SR
    out = np.zeros(total)
    root = 36 + NOTE[tonic]  # C2 based
    scale = [0, 2, 3, 5, 7, 8, 10] if mode == "minor" else [0, 2, 4, 5, 7, 9, 11]

    kick_n = int(0.35 * SR)
    kt = np.arange(kick_n) / SR
    kick = np.sin(2 * np.pi * (50 * kt + 60 * (1 - np.exp(-kt * 30)) / 30)) * np.exp(-kt * 9)
    hat = rng.standard_normal(int(0.05 * SR)) * env(int(0.05 * SR), 0.001, 0.012)
    hat = np.diff(hat, prepend=0) * 0.35

    vocal_from, vocal_to = n_bars // 4, n_bars * 3 // 4
    progression = [0, 5, 3, 4] if mode == "minor" else [0, 4, 5, 3]  # scale degrees

    for bar in range(n_bars):
        bar_start = bar * 4 * beat
        d = progression[(bar // 2) % 4]
        chord = [scale[(d + k) % 7] + 12 * ((d + k) // 7) for k in (0, 2, 4)]
        degree = chord[0]
        for b in range(4):
            t0 = int((bar_start + b * beat) * SR)
            accent = 1.0 if b == 0 else 0.85
            out[t0:t0 + kick_n] += kick[: len(out[t0:t0 + kick_n])] * accent
            h0 = int((bar_start + (b + 0.5) * beat) * SR)
            out[h0:h0 + len(hat)] += hat[: len(out[h0:h0 + len(hat)])]
            # bassline on offbeats
            bn = int(beat * 0.45 * SR)
            bt = np.arange(bn) / SR
            bass = np.sin(2 * np.pi * freq(root + degree - 12) * bt) * env(bn, 0.005, 0.2) * 0.5
            out[h0:h0 + bn] += bass[: len(out[h0:h0 + bn])]
        # chord pad once per bar
        pn = int(4 * beat * SR)
        pt = np.arange(pn) / SR
        pad = sum(np.sin(2 * np.pi * freq(root + 12 + note) * pt) for note in chord)
        pad *= env(pn, 0.2, 3.0) * 0.08
        s = int(bar_start * SR)
        out[s:s + pn] += pad[: len(out[s:s + pn])]
        # vocal-like lead: saw with vibrato, busy notes in the mid band
        if vocal_from <= bar < vocal_to:
            for k in range(8):
                vn = int(beat / 2 * SR)
                vt = np.arange(vn) / SR
                note = root + 24 + scale[rng.integers(0, 7)]
                f = freq(note) * (1 + 0.01 * np.sin(2 * np.pi * 5.5 * vt))
                phase = np.cumsum(f) / SR
                saw = 2 * (phase % 1) - 1
                v0 = int((bar_start + k * beat / 2) * SR)
                out[v0:v0 + vn] += (saw * env(vn, 0.02, 0.25) * 0.12)[: len(out[v0:v0 + vn])]

    out /= np.max(np.abs(out)) + 1e-9
    return (out * 0.9).astype(np.float32)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default=str(Path(__file__).parent / "demo_music"))
    ap.add_argument("--minutes", type=float, default=2.5)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for i, (bpm, tonic, mode, genre) in enumerate(TRACKS):
        name = f"Demo {i + 1:02d} - {bpm}bpm {tonic}{'m' if mode == 'minor' else ''} ({genre}).wav"
        path = out / name
        if path.exists():
            continue
        sf.write(path, make_track(bpm, tonic, mode, args.minutes, seed=i), SR, subtype="PCM_16")
        print("wrote", path)


if __name__ == "__main__":
    main()
