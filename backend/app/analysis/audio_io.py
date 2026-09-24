"""Audio file sniffing and decoding that trusts the file's bytes, not its extension.

Downloaders often save YouTube AAC/Opus streams as ".mp3", or save an HTML error page as ".mp4".
We detect the real container from the header, decode anything ffmpeg can read via PyAV, and
reject non-audio files with a clear error.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

# Formats libsndfile (soundfile/librosa) decodes directly and browsers play natively.
NATIVE = {"mp3", "wav", "flac", "ogg"}
MIN_BYTES = 50_000


class NotAudioError(ValueError):
    pass


def sniff(path: str | Path) -> str:
    """Return the real container: mp3, wav, flac, aiff, ogg, mp4, webm, html or unknown."""
    with open(path, "rb") as f:
        head = f.read(64)
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if head[:4] == b"OggS":
        return "ogg"
    if head[4:8] == b"ftyp":
        return "mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    text = head.lstrip().lower()
    if text.startswith((b"<html", b"<!doctype", b"<?xml", b"{")):
        return "html"
    return "unknown"


def check_audio(path: str | Path) -> str:
    """Raise NotAudioError for files that cannot be music; return the sniffed kind."""
    size = Path(path).stat().st_size
    kind = sniff(path)
    if kind == "html":
        raise NotAudioError("this is a web page, not audio (the download failed) - delete it and download again")
    if size < MIN_BYTES:
        raise NotAudioError(f"file is only {size} bytes - the download is broken")
    return kind


def load_mono(path: str | Path, sr: int) -> np.ndarray:
    """Decode to mono float32 at `sr`."""
    kind = check_audio(path)
    if kind in NATIVE or kind == "aiff":
        try:
            import librosa

            y, _ = librosa.load(str(path), sr=sr, mono=True)
            return y
        except Exception:
            pass  # fall through to ffmpeg
    return _decode_av(path, sr)


def _decode_av(path: str | Path, sr: int) -> np.ndarray:
    import av

    chunks: list[np.ndarray] = []
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise NotAudioError("no audio stream in this file")
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="flt", layout="mono", rate=sr)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        raise NotAudioError("decoded no audio")
    return np.concatenate(chunks).astype(np.float32)


def to_flac(src: str | Path, dst: str | Path) -> None:
    """Decode anything ffmpeg reads and write stereo FLAC at the source rate (no further loss)."""
    import av
    import soundfile as sf

    with av.open(str(src)) as container:
        stream = container.streams.audio[0]
        rate = stream.rate or 44100
        resampler = av.AudioResampler(format="s16", layout="stereo", rate=rate)
        with sf.SoundFile(str(dst), "w", samplerate=rate, channels=2, format="FLAC", subtype="PCM_16") as out:
            for frame in container.decode(stream):
                for r in resampler.resample(frame):
                    out.write(r.to_ndarray().reshape(-1, 2))
            for r in resampler.resample(None):
                out.write(r.to_ndarray().reshape(-1, 2))
