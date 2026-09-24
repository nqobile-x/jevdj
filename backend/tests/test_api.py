"""API and Jev brain tests with a fake Jev client (no network)."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from tests.test_rules import track


class FakeJev:
    """Mimics TypeSafeClient.system_one. Picks the last choice label with a set confidence."""

    def __init__(self, confidence=0.9, fail=False, vocal=0.2):
        self.confidence = confidence
        self.fail = fail
        self.vocal = vocal
        self.calls = []

    def system_one(self, state, questions):
        self.calls.append((state, questions))
        if self.fail:
            raise ConnectionError("offline")
        choices, scores, nouls = {}, {}, {}
        for name, q in questions.items():
            if q["type"] == "choice":
                labels = list(q["criteria"])
                pick = labels[-1]
                rest = (1 - self.confidence) / max(len(labels) - 1, 1)
                choices[name] = SimpleNamespace(choice=pick, confidence=self.confidence,
                                                probabilities={l: (self.confidence if l == pick else rest) for l in labels})
            elif q["type"] == "score":
                n = len(q["criteria"])
                scores[name] = SimpleNamespace(score=1.8, confidence=self.confidence,
                                               probabilities={i: 1 / n for i in range(n)})
            else:
                nouls[name] = SimpleNamespace(noul=self.vocal)
        return SimpleNamespace(choices=choices, scores=scores, nouls=nouls)

    def close(self):
        pass


def make_client(tmp_path, fake):
    import os

    os.environ["JEVDJ_DATA_DIR"] = str(tmp_path)
    os.environ["JEVDJ_DB"] = str(tmp_path / "t.db")
    s = load_settings()
    s = s.__class__(**{**s.__dict__, "music_dir": tmp_path / "music", "groq_api_key": "", "watch_interval": 0})
    app = create_app(s, jev_client=fake)
    db = app.state.db
    for i, (bpm, cam, en) in enumerate([(113, "8A", 0.5), (114, "9A", 0.6), (113, "8B", 0.55), (112, "7A", 0.62), (124, "3B", 0.8)], 1):
        t = track(i, bpm, cam, energy=en)
        t.pop("id")
        t.update(path=str(tmp_path / f"{i}.wav"), key_name="x", key_confidence=1.0, first_beat=0.0)
        db.upsert_track(t, mtime=1.0)
    return TestClient(app), fake


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path, FakeJev())[0] as c:
        yield c


def test_library_lists_tracks(client):
    lib = client.get("/library").json()
    assert len(lib) == 5 and {"bpm", "camelot", "energy"} <= lib[0].keys()
    assert "downbeats" in client.get(f"/library/{lib[0]['id']}").json()


def test_next_uses_jev_and_scores_top3(tmp_path):
    c, fake = make_client(tmp_path, FakeJev(confidence=0.9))
    with c:
        c.post("/set/start", json={"vibe": "build"})
        r = c.post("/brain/next", json={"current_id": 1}).json()
        assert r["source"] == "jev" and r["confidence"] == 0.9
        assert r["track"]["id"] != 1
        kinds = [d["kind"] for d in c.get("/decisions").json()]
        assert "next_track" in kinds and "smoothness" in kinds


def test_low_confidence_falls_back_to_rules(tmp_path):
    c, _ = make_client(tmp_path, FakeJev(confidence=0.3))
    with c:
        r = c.post("/brain/next", json={"current_id": 1}).json()
        assert r["source"] == "rules" and "confidence 0.30" in r["fallback"]
        sources = {d["source"] for d in c.get("/decisions").json()}
        assert sources == {"jev", "rules"}  # the unsure Jev answer is still logged


def test_offline_falls_back(tmp_path):
    c, _ = make_client(tmp_path, FakeJev(fail=True))
    with c:
        r = c.post("/brain/next", json={"current_id": 1}).json()
        assert r["source"] == "rules" and "unavailable" in r["fallback"]
        t = c.post("/brain/transition", json={"from_id": 1, "to_id": 2}).json()
        from app.brain.rules import TRANSITION_BARS
        assert t["source"] == "rules" and t["style"] in TRANSITION_BARS


def test_transition_vocal_noul_shifts(tmp_path):
    c, _ = make_client(tmp_path, FakeJev(confidence=0.9, vocal=0.9))
    with c:
        t = c.post("/brain/transition", json={"from_id": 1, "to_id": 2}).json()
        assert t["source"] == "jev"
        assert t["style"] in ("long_blend", "filter_fade", "wash_out", "tease", "chop", "quick_cut")
        kinds = [d["kind"] for d in c.get("/decisions").json()]
        assert "vocal_check" in kinds


def test_history_export_and_overrides(tmp_path):
    c, _ = make_client(tmp_path, FakeJev())
    with c:
        c.post("/set/start", json={"vibe": "auto"})
        c.post("/history/played", json={"track_id": 1})
        c.post("/history/played", json={"track_id": 2, "transition": "long_blend", "source": "jev"})
        c.post("/brain/override", json={"action": "veto", "track_id": 3})
        txt = c.get("/history/export?format=txt").text
        assert "01." in txt and "via long_blend" in txt
        js = c.get("/history/export?format=json").json()
        assert len(js["tracks"]) == 2
        r = c.post("/brain/next", json={"current_id": 2, "history": [1, 2]}).json()
        assert r["track"]["id"] not in (1, 2)


def test_phase_score_every_five_tracks(tmp_path):
    c, fake = make_client(tmp_path, FakeJev(confidence=0.9))
    with c:
        c.post("/set/start", json={"vibe": "auto"})
        for tid in (1, 2, 3, 4, 5):
            c.post("/history/played", json={"track_id": tid})
        r = c.post("/brain/next", json={"current_id": 5}).json()
        assert r["phase"] == "peak"  # fake expected score 1.8 rounds to 2
        assert any(d["kind"] == "set_phase" for d in c.get("/decisions").json())


def test_switch_it_up_and_taste(tmp_path):
    c, fake = make_client(tmp_path, FakeJev(confidence=0.9))
    with c:
        c.post("/set/start", json={"vibe": "build"})
        c.post("/history/played", json={"track_id": 1})
        r = c.post("/brain/next", json={"current_id": 1, "switch": True}).json()
        assert r["track"]["id"] != 1
        state, questions = fake.calls[0]
        assert "switch it up" in questions["next"]["instructions"]
        assert "favourite genres: amapiano" in state["listener_taste"]
        v = c.post("/voice/line", json={"next_id": 2, "mode": "intro"}).json()
        assert v["text"] and v["source"] == "template"
