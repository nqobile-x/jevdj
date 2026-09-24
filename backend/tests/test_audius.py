"""Audius source with the network faked out."""

import time

import numpy as np
import soundfile as sf

import app.sources.audius as audius
from app.db import Database
from tests.test_api import FakeJev, make_client


def test_summary_filters_snippets_and_gated():
    raw = [
        {"id": "a1", "title": "Full", "user": {"name": "DJ"}, "duration": 240, "is_streamable": True, "permalink": "/dj/full"},
        {"id": "a2", "title": "Teaser", "user": {"name": "DJ"}, "duration": 40, "is_streamable": True},
        {"id": "a3", "title": "Gated", "user": {"name": "DJ"}, "duration": 240, "is_streamable": True, "is_stream_gated": True},
    ]
    out = audius._filter(raw, 90)
    assert [t["id"] for t in out] == ["a1"] and out[0]["permalink"] == "https://audius.co/dj/full"


def test_safe_id_rejects_paths():
    import pytest

    with pytest.raises(ValueError):
        audius.safe_id("../../etc")


def test_add_downloads_analyses_and_survives_folder_scan(tmp_path, monkeypatch):
    src = tmp_path / "net.wav"
    sr = 22050
    t = np.arange(sr * 40) / sr
    y = (np.sin(2 * np.pi * 55 * t) * (np.sin(2 * np.pi * 2 * t) > 0.95)).astype(np.float32)  # 120 BPM thumps
    sf.write(src, y, sr)

    monkeypatch.setattr(audius, "track", lambda tid: {"id": tid, "title": "Net Song", "artist": "Net Artist",
                                                      "genre": "electronic", "permalink": "https://audius.co/x", "artwork": None})

    fetched = []

    def fake_fetch(tid, cache_dir):
        cache_dir.mkdir(parents=True, exist_ok=True)
        dst = cache_dir / f"{tid}.wav"
        dst.write_bytes(src.read_bytes())
        fetched.append(dst)
        return dst

    monkeypatch.setattr(audius, "fetch", fake_fetch)
    monkeypatch.setattr(audius, "search", lambda q, limit, m: [{"id": "abc123", "title": "Net Song"}])
    c, _ = make_client(tmp_path, FakeJev())
    with c:
        assert c.post("/sources/audius/add", json={"id": "abc123"}).json() == {"queued": True}
        deadline = time.time() + 60
        while time.time() < deadline:
            lib = c.get("/library").json()
            if any(t.get("source") == "audius" for t in lib):
                break
            time.sleep(0.2)
        net = [t for t in lib if t.get("source") == "audius"]
        assert net and net[0]["title"] == "Net Song" and net[0]["artist"] == "Net Artist"
        assert net[0]["path"] == "audius:abc123" and not fetched[0].exists()  # audio not kept
        assert c.get("/sources/audius/search?q=x").json()[0]["added"] is True
        # A folder scan must not delete online tracks.
        c.post("/library/scan", json={})
        time.sleep(0.5)
        assert any(t.get("source") == "audius" for t in c.get("/library").json())


def test_migration_adds_columns(tmp_path):
    import sqlite3

    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, mtime REAL NOT NULL, title TEXT, error TEXT)")
    con.commit()
    con.close()
    Database(p)
    cols = {r[1] for r in sqlite3.connect(p).execute("PRAGMA table_info(tracks)")}
    assert {"source", "source_id", "permalink", "artwork"} <= cols


def test_filter_skips_dj_mixes():
    raw = [{"id": "m1", "title": "Amapiano Mix 2026", "user": {"name": "DJ"}, "duration": 3264, "is_streamable": True}]
    assert audius._filter(raw, 90) == []
