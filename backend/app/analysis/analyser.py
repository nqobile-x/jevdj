"""Per-track analysis: tempo grid, key, energy, vocal density and safe mix windows.

Electronic music (amapiano, house) is mostly constant tempo, so we fit a straight beat grid
to librosa's beat estimates. A straight grid lines up two decks far better than raw beat times.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from app.analysis.camelot import PITCH_CLASSES, key_to_camelot

SR = 22050
HOP = 512
VOCAL_THRESHOLD = 0.55
PHRASE_BARS = 8

# Krumhansl-Kessler key profiles.
_MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


@dataclass
class TrackAnalysis:
    path: str
    title: str
    artist: str
    genre: str
    duration: float
    bpm: float
    beat_period: float
    first_beat: float
    first_downbeat: float
    key_name: str
    camelot: str
    key_confidence: float
    energy: float
    energy_bars: list[float] = field(default_factory=list)
    vocal_bars: list[float] = field(default_factory=list)
    downbeats: list[float] = field(default_factory=list)
    intro_end: float = 0.0
    outro_start: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- metadata

def read_tags(path: str) -> dict[str, str]:
    title = artist = genre = ""
    try:
        from mutagen import File as MutagenFile

        tags = MutagenFile(path, easy=True)
        if tags is not None and tags.tags is not None:
            title = (tags.get("title") or [""])[0]
            artist = (tags.get("artist") or [""])[0]
            genre = (tags.get("genre") or [""])[0]
    except Exception:
        pass
    # YouTube-style tags: uploader as artist ("shakiraVEVO") and "Artist - Title" in the title.
    uploader = bool(re.search(r"(vevo|official|\s-\stopic)$", artist, re.IGNORECASE))
    if title and " - " in title and (not artist or uploader or _squash(artist) in _squash(title.split(" - ", 1)[0])):
        artist, title = (x.strip() for x in title.split(" - ", 1))
    artist = re.sub(r"\s*(vevo|\s-\stopic)$", "", artist, flags=re.IGNORECASE).strip()
    if not title:
        stem = Path(path).stem
        stem = re.sub(r"^\d+[\s._-]+", "", stem)
        if " - " in stem:
            a, t = stem.split(" - ", 1)
            artist = artist or a.strip()
            title = t.strip()
        else:
            title = stem
    return {"title": clean_tag(title), "artist": clean_tag(artist), "genre": genre.strip().lower()}


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


_WATERMARK = re.compile(
    r"\s*(\|\||\||-|~)?\s*[\[(]?\s*(www\.)?[a-z0-9-]+\.(com|co\.za|net|org|info|biz|xyz)\s*[\])]?\s*$",
    re.IGNORECASE,
)


_VIDEO_TAG = re.compile(
    r"\s*(-\s*)?[\[(]?\s*(official\s+)?(music\s+)?(video|audio|lyric\s+video|lyrics|visualizer|visualiser"
    r"|\d{2,3}\s*kbps)\s*[\])]?\s*$",
    re.IGNORECASE,
)


def clean_tag(value: str) -> str:
    """Strip download-site watermarks like '|| FlexyJam.com' or '(www.site.co.za)'."""
    value = value.replace("_", " ").strip()
    prev = None
    while prev != value:
        prev = value
        value = _WATERMARK.sub("", value).strip()
        value = _VIDEO_TAG.sub("", value).strip()
    return re.sub(r"\s{2,}", " ", value)


# ---------------------------------------------------------------- tempo grid

def fit_grid(beat_times: np.ndarray, tempo_hint: float) -> tuple[float, float]:
    """Fit t = a + n*period to detected beats. Returns (period, first_beat)."""
    if len(beat_times) < 8:
        period = 60.0 / max(tempo_hint, 1.0)
        first = float(beat_times[0]) if len(beat_times) else 0.0
        return period, first % period
    diffs = np.diff(beat_times)
    med = float(np.median(diffs))
    good = diffs[np.abs(diffs - med) < 0.15 * med]
    period = float(np.mean(good)) if len(good) else med
    idx = np.round((beat_times - beat_times[0]) / period)
    b, a = np.polyfit(idx, beat_times, 1)
    period = float(b)
    # Many dance tracks sit on an integer BPM; snap when very close.
    bpm = 60.0 / period
    if abs(bpm - round(bpm)) < 0.15:
        period = 60.0 / round(bpm)
    # Re-estimate phase with the final period (circular mean of residuals).
    phases = (beat_times % period) / period * 2 * np.pi
    phase = float(np.angle(np.mean(np.exp(1j * phases))))
    first = (phase / (2 * np.pi)) * period % period
    return period, first


FINE_HOP = 128  # ~5.8 ms at 22.05 kHz


def refine_grid(y: np.ndarray, sr: int, period: float, first_beat: float, duration: float) -> tuple[float, float]:
    """Sub-frame grid refinement: search tempo (+-0.15 BPM) and phase (+-40 ms) that put the grid
    right on the kick attacks. Beat trackers quantise to ~23 ms frames; a blend needs < 5 ms."""
    import librosa

    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=FINE_HOP, n_fft=2048, n_mels=24, fmax=180)
    if not len(env) or not np.any(env):
        return period, first_beat
    env = env / (np.max(env) + 1e-9)
    bpm0 = 60.0 / period
    offsets = np.arange(-0.040, 0.0401, 0.002)
    best = (-1.0, period, first_beat)
    scores_by_bpm: dict[float, tuple[float, float]] = {}
    for bpm in bpm0 + np.arange(-0.15, 0.151, 0.01):
        p = 60.0 / bpm
        beats = np.arange(first_beat, duration - 0.05, p)
        if len(beats) < 16:
            return period, first_beat
        frames = np.round((beats[None, :] + offsets[:, None]) * sr / FINE_HOP).astype(int)
        frames = np.clip(frames, 0, len(env) - 1)
        s = env[frames].mean(axis=1)
        i = int(np.argmax(s))
        scores_by_bpm[round(float(bpm), 2)] = (float(s[i]), float(offsets[i]))
        if s[i] > best[0]:
            best = (float(s[i]), p, first_beat + float(offsets[i]))
    # Dance music usually sits on an integer BPM: prefer it when it scores within 1% of the best.
    int_bpm = float(round(60.0 / best[1]))
    if int_bpm in scores_by_bpm and scores_by_bpm[int_bpm][0] >= best[0] * 0.99:
        s, off = scores_by_bpm[int_bpm]
        best = (s, 60.0 / int_bpm, first_beat + off)
    _score, p, fb = best
    return p, fb % p


def fold_period(period: float) -> float:
    bpm = 60.0 / period
    while bpm < 85:
        bpm *= 2
    while bpm > 175:
        bpm /= 2
    return 60.0 / bpm


def pick_downbeat_phase(grid: np.ndarray, low_onset: np.ndarray, chroma_nov: np.ndarray, sr: int) -> int:
    """Choose which of every 4 beats starts a bar: kicks/bass hits plus harmonic changes."""
    frames = librosa_time_to_frames(grid, sr)
    frames = frames[frames < len(low_onset)]
    if len(frames) < 8:
        return 0
    lo = np.array([low_onset[max(0, f - 2): f + 3].max() for f in frames]) / (np.max(low_onset) + 1e-9)
    ch = chroma_nov[np.minimum(frames, len(chroma_nov) - 1)] / (np.max(chroma_nov) + 1e-9)
    strength = 0.5 * lo + 0.5 * ch
    scores = [float(np.mean(strength[p::4])) for p in range(4)]
    return int(np.argmax(scores))


def grid_strength(onset: np.ndarray, grid: np.ndarray, sr: int, window: int = 2) -> float:
    """Mean onset peak within +-window frames of each grid time."""
    frames = librosa_time_to_frames(grid, sr)
    frames = frames[frames < len(onset)]
    if not len(frames):
        return 0.0
    peaks = [onset[max(0, f - window): f + window + 1].max() for f in frames]
    return float(np.mean(peaks))


def librosa_time_to_frames(times: np.ndarray, sr: int) -> np.ndarray:
    return np.round(np.asarray(times) * sr / HOP).astype(int)


# ---------------------------------------------------------------- key

def estimate_key(chroma: np.ndarray) -> tuple[str, str, float]:
    """Return (tonic, mode, confidence) using Krumhansl profile correlation."""
    profile = chroma.mean(axis=1)
    if not np.any(profile):
        return "C", "major", 0.0
    scores = []
    for i in range(12):
        scores.append((np.corrcoef(np.roll(_MAJOR_PROFILE, i), profile)[0, 1], PITCH_CLASSES[i], "major"))
        scores.append((np.corrcoef(np.roll(_MINOR_PROFILE, i), profile)[0, 1], PITCH_CLASSES[i], "minor"))
    scores.sort(key=lambda s: s[0], reverse=True)
    best, second = scores[0], scores[1]
    confidence = float(np.clip((best[0] - second[0]) * 5 + 0.5 * max(best[0], 0), 0, 1))
    return best[1], best[2], confidence


# ---------------------------------------------------------------- bars

def per_bar(values: np.ndarray, downbeats: np.ndarray, duration: float, sr: int) -> np.ndarray:
    bounds = np.append(downbeats, duration)
    frames = librosa_time_to_frames(bounds, sr)
    out = []
    for s, e in zip(frames[:-1], frames[1:]):
        seg = values[s:max(e, s + 1)]
        out.append(float(np.mean(seg)) if len(seg) else 0.0)
    return np.array(out)


def mix_windows(vocal: np.ndarray, downbeats: np.ndarray, duration: float) -> tuple[float, float]:
    """intro_end: last phrase boundary before vocals arrive. outro_start: first phrase after they stop."""
    n = len(vocal)
    if n == 0:
        return min(30.0, duration / 3), max(duration - 30.0, duration * 2 / 3)
    loud = vocal > VOCAL_THRESHOLD
    sustained = loud & np.append(loud[1:], False)

    first_vocal = int(np.argmax(sustained)) if sustained.any() else None
    if first_vocal is None:
        intro_bar = 32
    else:
        intro_bar = (first_vocal // PHRASE_BARS) * PHRASE_BARS
    intro_bar = int(np.clip(intro_bar, PHRASE_BARS, 64))
    intro_bar = min(intro_bar, max(n // 2, 1))

    last_vocal = int(n - 1 - np.argmax(sustained[::-1])) if sustained.any() else None
    if last_vocal is None:
        outro_bar = n - 32
    else:
        outro_bar = -(-(last_vocal + 2) // PHRASE_BARS) * PHRASE_BARS
    outro_bar = int(np.clip(outro_bar, max(n // 2, intro_bar + 1), max(n - PHRASE_BARS, intro_bar + 1)))
    outro_bar = min(outro_bar, n - 1)

    return float(downbeats[intro_bar]), float(downbeats[outro_bar])


# ---------------------------------------------------------------- main

def analyse_file(path: str | os.PathLike) -> TrackAnalysis:
    import librosa

    from app.analysis.audio_io import load_mono

    path = str(path)
    y, sr = load_mono(path, SR), SR
    duration = float(len(y) / sr)
    if duration < 20:
        raise ValueError("track shorter than 20s")

    stft = librosa.stft(y, hop_length=HOP)
    harm, perc = librosa.decompose.hpss(stft)
    mag_h, mag_p = np.abs(harm), np.abs(perc)
    freqs = librosa.fft_frequencies(sr=sr)

    # Tempo and beat grid from mel onsets of the full mix.
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=HOP, start_bpm=118, units="time")
    tempo = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else 120.0
    period, first_beat = fit_grid(np.asarray(beats, dtype=float), tempo)
    period = fold_period(period)
    first_beat = first_beat % period

    # Beat trackers often lock to offbeat hats/bass. Kicks carry the most sub energy on the beat.
    low_band = freqs < 150
    # Percussive part only: kicks are transients, log-drum and sub-bass notes are tonal (harmonic).
    low_onset = np.maximum(0, np.diff(np.log1p(mag_p[low_band]).sum(axis=0), prepend=0))
    on_beat = grid_strength(low_onset, np.arange(first_beat, duration, period), sr)
    off_beat = grid_strength(low_onset, np.arange(first_beat + period / 2, duration, period), sr)
    if off_beat > on_beat * 1.1:
        first_beat = (first_beat + period / 2) % period
    period, first_beat = refine_grid(y, sr, period, first_beat, duration)
    grid = np.arange(first_beat, duration, period)
    chroma = librosa.feature.chroma_stft(S=mag_h ** 2, sr=sr, hop_length=HOP)
    chroma_nov = np.append(0, np.linalg.norm(np.diff(chroma, axis=1), axis=0))
    phase = pick_downbeat_phase(grid, low_onset, chroma_nov, sr)
    first_downbeat = float(grid[phase]) if len(grid) > phase else float(first_beat)
    downbeats = np.arange(first_downbeat, duration - 4 * period * 0.5, 4 * period)

    # Key.
    tonic, mode, key_conf = estimate_key(chroma)
    camelot = key_to_camelot(tonic, mode)
    key_name = f"{tonic}{'m' if mode == 'minor' else ''}"

    # Energy.
    rms = librosa.feature.rms(S=np.abs(stft), hop_length=HOP)[0]
    rms_db = librosa.amplitude_to_db(rms + 1e-9, ref=1.0)
    bar_db = per_bar(rms_db, downbeats, duration, sr)
    lo, hi = np.percentile(bar_db, 5), np.max(bar_db)
    energy_bars = np.clip((bar_db - lo) / max(hi - lo, 1e-6), 0, 1)
    loud = float(np.clip((np.percentile(rms_db, 75) + 30) / 24, 0, 1))
    perc_ratio = float(np.sum(mag_p) / (np.sum(mag_p) + np.sum(mag_h) + 1e-9))
    perc_score = float(np.clip((perc_ratio - 0.2) / 0.4, 0, 1))
    bpm = 60.0 / period
    tempo_score = float(np.clip((bpm - 90) / 50, 0, 1))
    energy = float(np.clip(0.55 * loud + 0.3 * perc_score + 0.15 * tempo_score, 0, 1))

    # Vocal density proxy: harmonic spectral flux in 300Hz-3kHz, relative to the track.
    band = (freqs >= 300) & (freqs <= 3000)
    band_mag = np.log1p(mag_h[band])
    flux = np.maximum(0, np.diff(band_mag, axis=1, prepend=band_mag[:, :1])).sum(axis=0)
    bar_flux = per_bar(flux, downbeats, duration, sr)
    ref = np.percentile(bar_flux, 95) if len(bar_flux) else 1.0
    vocal_bars = np.clip(bar_flux / max(ref, 1e-9), 0, 1)

    intro_end, outro_start = mix_windows(vocal_bars, downbeats, duration)
    tags = read_tags(path)

    return TrackAnalysis(
        path=path,
        title=tags["title"],
        artist=tags["artist"],
        genre=tags["genre"],
        duration=round(duration, 3),
        bpm=round(bpm, 2),
        beat_period=period,
        first_beat=round(first_beat, 4),
        first_downbeat=round(first_downbeat, 4),
        key_name=key_name,
        camelot=camelot,
        key_confidence=round(key_conf, 3),
        energy=round(energy, 3),
        energy_bars=[round(float(v), 3) for v in energy_bars],
        vocal_bars=[round(float(v), 3) for v in vocal_bars],
        downbeats=[round(float(v), 4) for v in downbeats],
        intro_end=round(intro_end, 3),
        outro_start=round(outro_start, 3),
    )
