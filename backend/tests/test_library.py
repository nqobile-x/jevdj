"""Upload, auto-detect and grid correction."""

import time

import numpy as np
import soundfile as sf

from app.analysis.watcher import LibraryWatcher, snapshot
from tests.test_api import FakeJev, make_client


def _noise_wav(path, seconds=2.0):
    y = (np.random.default_rng(1).standard_normal(int(44100 * seconds)) * 0.1).astype(np.float32)
    sf.write(path, y, 44100)


def test_upload_saves_audio_and_rejects_junk(tmp_path):
    c, _ = make_client(tmp_path, FakeJev())
    src = tmp_path / "src.wav"
    _noise_wav(src)
    with c:
        r = c.post("/library/upload", files=[
            ("files", ("My Song.wav", src.read_bytes(), "audio/wav")),
            ("files", ("broken.mp4", b"<html><title>410 Gone</title></html>", "video/mp4")),
            ("files", ("notes.txt", b"hello", "text/plain")),
        ]).json()
    assert r["saved"] == ["My Song.wav"]
    reasons = {x["file"]: x["reason"] for x in r["rejected"]}
    assert "web page" in reasons["broken.mp4"] and "not an audio" in reasons["notes.txt"]
    assert (tmp_path / "music" / "My Song.wav").exists()
    assert not list((tmp_path / "music").glob("*.part"))


def test_upload_never_overwrites(tmp_path):
    c, _ = make_client(tmp_path, FakeJev())
    src = tmp_path / "src.wav"
    _noise_wav(src)
    with c:
        for _ in range(2):
            c.post("/library/upload", files=[("files", ("../../evil/Song.wav", src.read_bytes(), "audio/wav"))])
    names = sorted(p.name for p in (tmp_path / "music").iterdir())
    assert names == ["Song (1).wav", "Song.wav"]


def test_grid_shift(tmp_path):
    c, _ = make_client(tmp_path, FakeJev())
    with c:
        before = c.get("/library/1").json()
        after = c.post("/library/1/grid", json={"shift_s": 0.01}).json()
        assert abs(after["first_downbeat"] - before["first_downbeat"] - 0.01) < 1e-9
        assert abs(after["downbeats"][1] - before["downbeats"][1] - 0.01) < 1e-3
        assert c.post("/library/1/grid", json={"shift_s": 5}).status_code == 422


def test_watcher_waits_for_stable_folder(tmp_path):
    root = tmp_path / "m"
    root.mkdir()
    calls = []
    w = LibraryWatcher(root, lambda old, new: calls.append(len(new)) or True, interval=0.05)
    w.start()
    try:
        _noise_wav(root / "a.wav")
        deadline = time.time() + 3
        while not calls and time.time() < deadline:
            time.sleep(0.02)
        assert calls == [1]
        assert snapshot(root) == w.handled
    finally:
        w.stop()
