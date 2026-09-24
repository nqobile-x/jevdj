"""Musical key to Camelot wheel conversion and harmonic compatibility."""

from __future__ import annotations

PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

_FLAT_TO_SHARP = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#", "Cb": "B", "Fb": "E"}

# Camelot number for each pitch class; major keys use "B", minor keys use "A".
_MAJOR = {"C": 8, "G": 9, "D": 10, "A": 11, "E": 12, "B": 1,
          "F#": 2, "C#": 3, "G#": 4, "D#": 5, "A#": 6, "F": 7}
_MINOR = {"A": 8, "E": 9, "B": 10, "F#": 11, "C#": 12, "G#": 1,
          "D#": 2, "A#": 3, "F": 4, "C": 5, "G": 6, "D": 7}


def normalise_tonic(tonic: str) -> str:
    tonic = tonic.strip()
    if not tonic:
        raise ValueError("empty tonic")
    tonic = tonic[0].upper() + tonic[1:]
    tonic = _FLAT_TO_SHARP.get(tonic, tonic)
    if tonic not in PITCH_CLASSES:
        raise ValueError(f"unknown tonic: {tonic}")
    return tonic


def key_to_camelot(tonic: str, mode: str) -> str:
    """Convert a key like ("A", "minor") or ("Bb", "major") to a Camelot code like "8A"."""
    tonic = normalise_tonic(tonic)
    mode = mode.strip().lower()
    if mode in ("minor", "min", "m"):
        return f"{_MINOR[tonic]}A"
    if mode in ("major", "maj", ""):
        return f"{_MAJOR[tonic]}B"
    raise ValueError(f"unknown mode: {mode}")


def parse_key_name(name: str) -> tuple[str, str]:
    """Parse "Am", "A minor", "F#", "Bb major", "E flat minor" into (tonic, mode)."""
    name = name.strip().replace(" flat", "b").replace(" sharp", "#").replace("♭", "b").replace("♯", "#")
    lower = name.lower()
    for suffix, mode in ((" minor", "minor"), (" major", "major"), ("min", "minor"), ("maj", "major"), ("m", "minor")):
        if lower.endswith(suffix):
            return name[: len(name) - len(suffix)].strip(), mode
    return name, "major"


def camelot_from_name(name: str) -> str:
    tonic, mode = parse_key_name(name)
    return key_to_camelot(tonic, mode)


def parse_camelot(code: str) -> tuple[int, str]:
    code = code.strip().upper()
    num, letter = int(code[:-1]), code[-1]
    if not 1 <= num <= 12 or letter not in ("A", "B"):
        raise ValueError(f"bad camelot code: {code}")
    return num, letter


def camelot_distance(a: str, b: str) -> int:
    """Steps on the wheel: 0 same key, 1 adjacent or relative major/minor, higher is riskier."""
    na, la = parse_camelot(a)
    nb, lb = parse_camelot(b)
    diff = abs(na - nb)
    diff = min(diff, 12 - diff)
    return diff + (0 if la == lb else 1)


def is_compatible(a: str, b: str) -> bool:
    return camelot_distance(a, b) <= 1
