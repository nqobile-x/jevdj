"""JevDJ Originals: 8 royalty-free amapiano / afro house tracks, synthesised from scratch.

A demo library that sounds like music (unlike make_demo_tracks.py, which is a test fixture) and is
safe to use in videos and social posts. The tracks span keys, tempos and energy on purpose, so
SMART ORDER has a real arc to build: mellow log-drum grooves up to a hot, busy peak-time track.

Each track is DJ-friendly: 16-bar drums-only intro, groove, an 8-bar break, the main section and a
16-bar drums-only outro, all in 8-bar phrases.

python dev/make_originals.py [out_dir]     (default: dev/originals)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from mutagen.flac import FLAC
from scipy.signal import lfilter

SR = 44100
NOTE = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}

# title, bpm, tonic, mode, energy 0..1, genre
TRACKS = [
    ("Sunday Log", 110, "A", "minor", 0.15, "amapiano"),
    ("Tembisa Keys", 112, "E", "minor", 0.3, "amapiano"),
    ("Soweto Sunrise", 113, "C", "major", 0.4, "amapiano"),
    ("Dlala Piano", 113, "D", "minor", 0.5, "amapiano"),
    ("Mzansi Nights", 114, "B", "minor", 0.62, "amapiano"),
    ("Taxi Rank Groove", 116, "F#", "minor", 0.75, "afro house"),
    ("Jozi Heat", 118, "A", "major", 0.88, "afro house"),
    ("Gqom Rush", 120, "C#", "minor", 1.0, "afro house"),
]

# Section plan in bars: (name, bars)
SECTIONS = [("intro", 16), ("groove", 16), ("break", 8), ("main", 24), ("outro", 16)]


def tt(n: int) -> np.ndarray:
    return np.arange(n) / SR


def midi(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def lowpass(x: np.ndarray, hz: float) -> np.ndarray:
    a = 1 - np.exp(-2 * np.pi * hz / SR)
    return lfilter([a], [1, a - 1], x)


def highpass(x: np.ndarray, hz: float) -> np.ndarray:
    return x - lowpass(x, hz)


class Track:
    def __init__(self, bpm: float, tonic: str, mode: str, energy: float, seed: int):
        self.bpm, self.energy = bpm, energy
        self.beat = 60.0 / bpm
        self.bar = 4 * self.beat
        self.s16 = self.beat / 4
        self.rng = np.random.default_rng(seed)
        self.bars = sum(b for _, b in SECTIONS)
        self.n = int((self.bars * self.bar + 2.0) * SR)
        self.drums = np.zeros((self.n, 2))
        self.music = np.zeros((self.n, 2))
        root = 45 + (NOTE[tonic] - 9)  # tonic around A2
        self.root = root
        # Chord progressions as (scale degree root semitones, chord tones above it).
        if mode == "minor":  # i7 - VImaj7 - IIImaj7 - VII6 (the Am7-Fmaj7-Cmaj7-G6 family)
            self.prog = [(0, [0, 3, 7, 10]), (8, [0, 4, 7, 11]), (3, [0, 4, 7, 11]), (10, [0, 4, 7, 9])]
        else:  # Imaj7 - vi7 - IVmaj7 - V6
            self.prog = [(0, [0, 4, 7, 11]), (9, [0, 3, 7, 10]), (5, [0, 4, 7, 11]), (7, [0, 4, 7, 9])]
        self._kick = self._make_kick()
        self._clap = self._make_clap()
        self._shakers = [self._make_shaker() for _ in range(6)]

    # ------------------------------------------------------------ instruments

    def _make_kick(self) -> np.ndarray:
        n = int(0.42 * SR)
        t = tt(n)
        f = 46 + (80 + 40 * self.energy) * np.exp(-t * 38)
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7.5)
        click = self.rng.standard_normal(n) * np.exp(-t * 400) * (0.15 + 0.2 * self.energy)
        return np.tanh((body + click) * (1.4 + 0.6 * self.energy)) * 0.9

    def _make_clap(self) -> np.ndarray:
        n = int(0.28 * SR)
        x = self.rng.standard_normal(n)
        env = np.zeros(n)
        for k, d in enumerate([0.0, 0.011, 0.022]):
            i = int(d * SR)
            env[i:] += np.exp(-tt(n - i) * (60 if k < 2 else 16))
        return highpass(lowpass(x * env, 3500), 900) * 0.5

    def _make_shaker(self) -> np.ndarray:
        n = int(0.07 * SR)
        return highpass(self.rng.standard_normal(n), 5000) * np.exp(-tt(n) * 55) * 0.16

    @staticmethod
    def rim() -> np.ndarray:
        n = int(0.05 * SR)
        t = tt(n)
        return (np.sin(2 * np.pi * 1750 * t) + 0.5 * np.sin(2 * np.pi * 820 * t)) * np.exp(-t * 120) * 0.22

    def open_hat(self) -> np.ndarray:
        n = int(0.22 * SR)
        return highpass(self.rng.standard_normal(n), 7000) * np.exp(-tt(n) * 14) * 0.12

    @staticmethod
    def conga(note: float) -> np.ndarray:
        n = int(0.18 * SR)
        t = tt(n)
        f = midi(note) * (1 + 0.25 * np.exp(-t * 60))
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 22) * 0.3

    @staticmethod
    def log_drum(note: float) -> np.ndarray:
        n = int(0.36 * SR)
        t = tt(n)
        f = midi(note) * (1 + 0.9 * np.exp(-t * 45))
        ph = 2 * np.pi * np.cumsum(f) / SR
        tone = np.sin(ph) + 0.35 * np.sin(2 * ph) + 0.12 * np.sin(3 * ph)
        env = np.minimum(1, t / 0.004) * np.exp(-t * 9)
        return np.tanh(tone * env * 2.4) * 0.55

    @staticmethod
    def piano(notes: list[float], dur: float) -> np.ndarray:
        n = int(dur * SR)
        t = tt(n)
        out = np.zeros(n)
        for m in notes:
            f = midi(m)
            for h, a in ((1, 1.0), (2, 0.42), (3, 0.2), (4, 0.1), (5, 0.05)):
                out += a * np.sin(2 * np.pi * f * h * t * (1 + 0.0004 * h)) * np.exp(-t * (2.2 + 1.3 * h))
        return out * np.minimum(1, t / 0.003) * 0.09

    @staticmethod
    def pad(notes: list[float], dur: float, cutoff: float = 900) -> np.ndarray:
        n = int(dur * SR)
        t = tt(n)
        out = np.zeros(n)
        for m in notes:
            for det in (-0.08, 0.08):
                out += 2 * ((midi(m + det) * t) % 1.0) - 1
        out = lowpass(out, cutoff)
        env = np.minimum(1, t / 0.6) * np.minimum(1, (dur - t) / 0.4).clip(0, 1)
        return out * env * 0.018

    @staticmethod
    def lead(note: float, dur: float) -> np.ndarray:
        n = int(dur * SR)
        t = tt(n)
        f = midi(note)
        x = np.sign(np.sin(2 * np.pi * f * t)) * 0.5 + np.sin(2 * np.pi * 2 * f * t) * 0.3
        return lowpass(x, 2400) * np.exp(-t * 6) * 0.07

    def riser(self, dur: float) -> np.ndarray:
        n = int(dur * SR)
        t = tt(n)
        noise = highpass(self.rng.standard_normal(n), 2000)
        return noise * (t / dur) ** 2 * 0.12

    # ------------------------------------------------------------ helpers

    def place(self, buf: np.ndarray, sig: np.ndarray, at: float, gain: float = 1.0, pan: float = 0.0) -> None:
        i = int(at * SR)
        if i >= len(buf) or i < 0:
            return
        k = min(len(sig), len(buf) - i)
        left, right = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
        buf[i:i + k, 0] += sig[:k] * gain * left * 1.414
        buf[i:i + k, 1] += sig[:k] * gain * right * 1.414

    def chord(self, bar: int) -> tuple[int, list[int]]:
        deg, tones = self.prog[(bar // 2) % 4]
        base = self.root + 12 + deg
        while base > 62:
            base -= 12
        return self.root + deg - (12 if deg > 6 else 0), [base + x for x in tones]

    # ------------------------------------------------------------ arrangement

    def render(self) -> np.ndarray:
        e = self.energy
        log_steps = [(3, 0), (10, 0), (14, 7)]
        if e > 0.3:
            log_steps += [(6, 7)]
        if e > 0.55:
            log_steps += [(12, 12)]
        if e > 0.8:
            log_steps += [(7, 5), (15, 12)]
        start = 0
        for name, n_bars in SECTIONS:
            for k in range(n_bars):
                bar = start + k
                b0 = bar * self.bar
                bass_root, notes = self.chord(bar)
                kick_on = name != "break"
                full = name in ("groove", "main")
                hot = name == "main"
                fade = (1 - k / n_bars) if name == "outro" else 1.0
                # drums
                for beat in range(4):
                    if kick_on:
                        self.place(self.drums, self._kick, b0 + beat * self.beat, 1.0 * max(fade, 0.5))
                    if beat in (1, 3) and (full or (name == "intro" and k >= 8) or name == "outro"):
                        self.place(self.drums, self._clap, b0 + beat * self.beat, 0.8, 0.05)
                    if e > 0.45 and name != "break":
                        self.place(self.drums, self.open_hat(), b0 + beat * self.beat + self.beat / 2, 0.5 + 0.6 * e, 0.3)
                for s in range(16):
                    acc = 1.0 if s % 4 == 2 else 0.55
                    if name != "break" or s % 2 == 0:
                        self.place(self.drums, self._shakers[(bar * 16 + s) % 6], b0 + s * self.s16,
                                   acc * (0.6 + 0.6 * e), 0.35 if s % 2 else -0.35)
                    if s in (3, 7, 11, 15):
                        self.place(self.drums, self.rim(), b0 + s * self.s16, 0.9, -0.5)
                if e > 0.6 and (full or name == "outro"):
                    for s, note in ((2, 64), (5, 60), (11, 67), (13, 64)):
                        self.place(self.drums, self.conga(note), b0 + s * self.s16, 0.5 + 0.5 * e, 0.45)
                # music
                if full or name == "break":
                    for s, semi in (log_steps if full else []):
                        self.place(self.music, self.log_drum(bass_root + semi), b0 + s * self.s16, 1.0)
                    hits = [0, 6, 10, 12] if e < 0.7 else [0, 3, 6, 10, 12, 14]
                    for s in hits:
                        self.place(self.music, self.piano(notes, 0.9), b0 + s * self.s16, 1.0, -0.25)
                        self.place(self.music, self.piano([c + 12 for c in notes[1:]], 0.5),
                                   b0 + s * self.s16 + 0.012, 0.35, 0.3)
                    if bar % 2 == 0:
                        self.place(self.music, self.pad(notes, 2 * self.bar, 700 + 1400 * e), b0, 1.2)
                    if hot and e > 0.7 and k % 2 == 1:
                        for j, s in enumerate((0, 3, 6, 8, 11)):
                            self.place(self.music, self.lead(notes[j % 4] + 12, 0.35), b0 + s * self.s16, 1.0, 0.2)
                elif name == "intro" and k >= 8 and bar % 2 == 0:
                    self.place(self.music, self.pad(notes, 2 * self.bar, 500), b0, 0.8)
                if name == "break" and k == n_bars - 2:
                    self.place(self.music, self.riser(2 * self.bar), b0, 1.0)
            start += n_bars

        # Energy lives in the mix: hot tracks are louder and more percussive.
        mix = self.drums * (0.75 + 0.35 * e) + self.music * (1.1 - 0.35 * e)
        mix = np.tanh(mix * (1.1 + 0.9 * e)) / np.tanh(1.1 + 0.9 * e)
        mix /= np.max(np.abs(mix)) / (0.45 + 0.44 * e)
        fade = int(1.5 * SR)
        mix[-fade:] *= np.linspace(1, 0, fade)[:, None]
        return mix


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "originals"
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, (title, bpm, tonic, mode, energy, genre) in enumerate(TRACKS, 1):
        audio = Track(bpm, tonic, mode, energy, seed=100 + i).render()
        path = out_dir / f"JevDJ Originals - {title}.flac"
        sf.write(path, audio.astype(np.float32), SR, subtype="PCM_16")
        tags = FLAC(path)
        tags.update({"title": title, "artist": "JevDJ Originals", "genre": genre})
        tags.save()
        print(f"{path.name}: {bpm} BPM {tonic}{'m' if mode == 'minor' else ''}, energy {energy}, {len(audio) / SR:.0f}s")


if __name__ == "__main__":
    main()
