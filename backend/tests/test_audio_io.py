import numpy as np
import pytest
import soundfile as sf

from app.analysis.analyser import clean_tag
from app.analysis.audio_io import NotAudioError, check_audio, load_mono, sniff, to_flac


def test_sniff_ignores_extension(tmp_path):
    y = (np.random.default_rng(0).standard_normal(44100 * 2) * 0.2).astype(np.float32)  # noise: too big to trip the size check
    fake_mp3 = tmp_path / "actually_flac.mp3"
    sf.write(fake_mp3, y, 44100, format="FLAC")
    assert sniff(fake_mp3) == "flac"
    assert len(load_mono(fake_mp3, 22050)) == pytest.approx(44100, rel=0.01)


def test_html_error_page_rejected(tmp_path):
    page = tmp_path / "song.mp4"
    page.write_text("<html><head><title>410 Gone</title></head></html>")
    with pytest.raises(NotAudioError, match="web page"):
        check_audio(page)


def test_to_flac_roundtrip(tmp_path):
    y = (np.sin(np.linspace(0, 3000, 44100 * 2)) * 0.3).astype(np.float32)
    src = tmp_path / "a.wav"
    sf.write(src, np.stack([y, y], axis=1), 44100)
    dst = tmp_path / "a.flac"
    to_flac(src, dst)
    info = sf.info(dst)
    assert info.samplerate == 44100 and info.channels == 2 and abs(info.duration - 2.0) < 0.05


def test_video_suffixes_cleaned():
    assert clean_tag("Botshelo Ke Eng (Official Video)") == "Botshelo Ke Eng"
    assert clean_tag("Gumba Fire (feat. Mr JazziQ) (Video)") == "Gumba Fire (feat. Mr JazziQ)"
    assert clean_tag("Dai Dai (Official Video) - (320 Kbps)") == "Dai Dai"
    assert clean_tag("wgft (feat. Burna Boy) Official Video") == "wgft (feat. Burna Boy)"
    assert clean_tag("RATHER LIE (Official Audio)") == "RATHER LIE"
    assert clean_tag("Raindance (ft. Tems)") == "Raindance (ft. Tems)"


def test_youtube_style_tags(tmp_path, monkeypatch):
    import app.analysis.analyser as an

    class Tags(dict):
        tags = True

    def fake(title, artist):
        monkeypatch.setattr("mutagen.File", lambda *_a, **_k: Tags(title=[title], artist=[artist], genre=[""]))
        return an.read_tags(str(tmp_path / "x.mp3"))

    assert fake("Shakira, Burna Boy - Dai Dai (Official Video)", "shakiraVEVO") == \
        {"title": "Dai Dai", "artist": "Shakira, Burna Boy", "genre": ""}
    assert fake("Drake - shabang", "") ["artist"] == "Drake"
    assert fake("Asibe Happy", "Kabza De Small") == {"title": "Asibe Happy", "artist": "Kabza De Small", "genre": ""}
