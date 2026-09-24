import pytest

from app.analysis.camelot import camelot_distance, camelot_from_name, is_compatible, key_to_camelot


@pytest.mark.parametrize(
    "tonic,mode,code",
    [
        ("C", "major", "8B"), ("A", "minor", "8A"), ("G", "major", "9B"), ("E", "minor", "9A"),
        ("D", "major", "10B"), ("B", "minor", "10A"), ("A", "major", "11B"), ("F#", "minor", "11A"),
        ("E", "major", "12B"), ("C#", "minor", "12A"), ("B", "major", "1B"), ("G#", "minor", "1A"),
        ("F#", "major", "2B"), ("D#", "minor", "2A"), ("C#", "major", "3B"), ("A#", "minor", "3A"),
        ("G#", "major", "4B"), ("F", "minor", "4A"), ("D#", "major", "5B"), ("C", "minor", "5A"),
        ("A#", "major", "6B"), ("G", "minor", "6A"), ("F", "major", "7B"), ("D", "minor", "7A"),
    ],
)
def test_all_24_keys(tonic, mode, code):
    assert key_to_camelot(tonic, mode) == code


@pytest.mark.parametrize("name,code", [("Am", "8A"), ("Bb", "6B"), ("Ebm", "2A"), ("Db major", "3B"), ("F# minor", "11A"), ("gm", "6A")])
def test_names_and_flats(name, code):
    assert camelot_from_name(name) == code


def test_every_code_used_once():
    from app.analysis.camelot import PITCH_CLASSES

    codes = {key_to_camelot(t, m) for t in PITCH_CLASSES for m in ("major", "minor")}
    assert len(codes) == 24


def test_distance_and_compatibility():
    assert camelot_distance("8A", "8A") == 0
    assert camelot_distance("8A", "9A") == 1
    assert camelot_distance("12A", "1A") == 1  # wraps around the wheel
    assert camelot_distance("8A", "8B") == 1  # relative major/minor
    assert camelot_distance("8A", "10A") == 2
    assert camelot_distance("8A", "2A") == 6
    assert is_compatible("5B", "4B") and is_compatible("5B", "5A")
    assert not is_compatible("5B", "4A")


def test_bad_input():
    with pytest.raises(ValueError):
        key_to_camelot("H", "major")
    with pytest.raises(ValueError):
        camelot_distance("13A", "1A")


def test_audius_style_key_names():
    assert camelot_from_name("E flat minor") == "2A"
    assert camelot_from_name("G flat minor") == "11A"
    assert camelot_from_name("F sharp major") == "2B"
    assert camelot_from_name("F minor") == "4A"
