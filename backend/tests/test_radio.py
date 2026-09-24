"""Audius station: auto mix draws only from the station, filled ahead of playback."""

import time

import app.sources.audius as audius
from tests.test_api import FakeJev, make_client


def test_station_feeds_brain_next(tmp_path, monkeypatch):
    ids = ["s1", "s2", "s3", "s4"]
    monkeypatch.setattr(audius, "search", lambda q, limit=40, m=90: [{"id": i, "title": i} for i in ids])
    c, _ = make_client(tmp_path, FakeJev(confidence=0.9))
    app = c.app
    db = app.state.db

    # Fake the stream+analysis: register station tracks as analysed Audius tracks.
    from tests.test_rules import track

    def fake_track(tid):
        return {"id": tid, "title": f"Net {tid}", "artist": "Net", "genre": "amapiano", "permalink": None, "artwork": None}

    counter = {"n": 0}

    def fake_fetch(tid, cache_dir):
        cache_dir.mkdir(parents=True, exist_ok=True)
        f = cache_dir / f"{tid}.mp3"
        f.write_bytes(b"x")
        return f

    def fake_analyse(path):
        counter["n"] += 1
        t = track(100 + counter["n"], 113, "8A")
        t.pop("id")
        t.update(key_name="Am", key_confidence=1.0, first_beat=0.0, genre="")

        class A:
            def to_dict(self):
                return dict(t)

        return A()

    monkeypatch.setattr(audius, "track", fake_track)
    monkeypatch.setattr(audius, "fetch", fake_fetch)
    monkeypatch.setattr("app.analysis.analyser.analyse_file", fake_analyse)

    with c:
        r = c.post("/radio/start", json={"query": "amapiano"}).json()
        assert r["active"] and r["total"] == 4
        deadline = time.time() + 10
        while time.time() < deadline and c.get("/radio").json()["ready"] < 3:
            time.sleep(0.05)
        assert c.get("/radio").json()["ready"] >= 3
        pool = set(db.source_ids("audius").values())
        nxt = c.post("/brain/next", json={"current_id": 1}).json()
        assert nxt["track"]["id"] in pool  # picks from the station, not the local library
        c.post("/radio/stop")
        assert c.get("/radio").json()["active"] is False
