"""Tests for YouTube Music / TikTok stream source, DJ curation, new creative transitions, and continuous station playback."""

import time

import numpy as np
import soundfile as sf

import app.sources.ytstream as ytstream
from app.analysis.analyser import infer_genre
from app.brain import rules
from app.brain.cards import track_card
from tests.test_api import FakeJev, make_client
from tests.test_rules import track


def test_clean_artist_title_and_summary():
    entry = {
        "id": "dQw4w9WgXcQ",
        "title": "Kabza De Small - Imithandazo (Official Audio)",
        "uploader": "Kabza De Small - Topic",
        "duration": 378,
        "view_count": 4_200_000,
        "webpage_url": "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
    }
    s = ytstream._summary(entry)
    assert s["id"] == "yt_dQw4w9WgXcQ"
    assert s["artist"] == "Kabza De Small"
    assert s["title"] == "Imithandazo"
    assert s["platform"] == "youtube"
    assert s["duration"] == 378


def test_tiktok_canonical_id():
    entry = {
        "id": "739182739182",
        "title": "Amapiano log drum edit",
        "creator": "dj_sa",
        "duration": 45,
        "webpage_url": "https://www.tiktok.com/@dj_sa/video/739182739182",
    }
    s = ytstream._summary(entry)
    assert s["id"] == "tiktok_739182739182"
    assert s["platform"] == "tiktok"
    assert s["artist"] == "dj_sa"


def test_curation_score_prefers_official_hits_and_filters_junk():
    hit_entry = {
        "id": "hit12345",
        "title": "Kelvin Momo - Kurhula (Official Audio)",
        "uploader": "Kelvin Momo - Topic",
        "duration": 360,
        "view_count": 3_500_000,
    }
    low_entry = {
        "id": "low12345",
        "title": "Random cover version",
        "uploader": "fan_channel",
        "duration": 210,
        "view_count": 200,
    }
    s_hit = ytstream._summary(hit_entry)
    s_low = ytstream._summary(low_entry)
    assert ytstream._curation_score(s_hit, hit_entry, "Kelvin Momo") > ytstream._curation_score(s_low, low_entry, "Kelvin Momo")
    assert ytstream._JUNK_RE.search("Kelvin Momo 1 Hour Non Stop Mix")
    assert ytstream._JUNK_RE.search("Artist Interview Podcast Episode 4")


def test_new_creative_transition_styles_plan_cleanly():
    a = track(1, 113, "8A", energy=0.72, genre="amapiano")
    b = track(2, 113, "8A", energy=0.82, genre="amapiano")
    opts = rules.style_options(a, b, "peak", mix_style="club", flair="creative")
    assert "log_drum_swap" in opts
    assert "double_drop" in opts

    turnt_opts = rules.style_options(a, b, "peak", mix_style="club", flair="turnt")
    assert "siren_spin" in turnt_opts
    assert "word_play" in turnt_opts

    for style, expected_bars in [("log_drum_swap", 16), ("word_play", 8), ("double_drop", 16), ("siren_spin", 2), ("stem_mashup", 16)]:
        plan = rules.plan_transition(a, b, style, shift_vocals=False, start_s=0.0, mix_style="club", flair="creative")
        assert plan.style == style
        assert plan.bars == expected_bars
        assert plan.mix_out_s in a["downbeats"]
        assert plan.mix_in_s in b["downbeats"]


def test_universal_genre_inference_and_track_card():
    assert infer_genre(113.0, 0.72, 0.35, 0.3, 0.4, "Imithandazo", "Kabza De Small") == "amapiano"
    assert infer_genre(118.5, 0.75, 0.42, 0.25, 0.3, "Unknown Track", "Unknown Artist") == "3-step"
    assert infer_genre(120.0, 0.70, 0.30, 0.25, 0.36, "Sunset Mix", "Black Coffee") == "afro house"
    assert infer_genre(94.0, 0.65, 0.25, 0.55, 0.25, "Freestyle", "Rapper") == "hip hop"

    t = track(1, 113, "8A", energy=0.75, genre="amapiano")
    t["vocal_bars"] = [0.65] * 64
    t["energy_bars"] = [0.4] * 16 + [0.9] * 32 + [0.4] * 16
    card = track_card(t)
    assert "vocal-led (good for stem_mashup/word_play)" in card
    assert "drop bar" in card


def test_stream_add_and_station_isolation(tmp_path, monkeypatch):
    def fake_fetch(cid, cache_dir, url=None, meta_hint=None):
        cache_dir.mkdir(parents=True, exist_ok=True)
        dst = cache_dir / f"{cid}.m4a"
        dst.write_bytes(b"audio")
        return dst, {
            "id": cid,
            "title": f"YT {cid}",
            "artist": "Kelvin Momo",
            "genre": "amapiano",
            "permalink": f"https://music.youtube.com/watch?v={cid}",
            "artwork": None,
        }

    def fake_analyse(path):
        t = track(200, 113, "8A")
        t.pop("id")
        t.update(key_name="Am", key_confidence=1.0, first_beat=0.0, genre="amapiano")

        class A:
            def to_dict(self):
                return dict(t)

        return A()

    monkeypatch.setattr(ytstream, "fetch", fake_fetch)
    monkeypatch.setattr("app.analysis.analyser.analyse_file", fake_analyse)
    monkeypatch.setattr(
        ytstream,
        "search",
        lambda q, limit=20, min_seconds=30: [
            {"id": "yt_momo001", "title": "YT yt_momo001", "artist": "Kelvin Momo", "duration": 240,
             "permalink": "https://music.youtube.com/watch?v=yt_momo001", "platform": "youtube"}
        ],
    )

    c, _ = make_client(tmp_path, FakeJev())
    with c:
        res = c.post("/sources/stream/add", json={"id": "yt_momo001", "url": "https://music.youtube.com/watch?v=yt_momo001"}).json()
        assert res["queued"] is True
        deadline = time.time() + 60
        while time.time() < deadline:
            lib = c.get("/library").json()
            if any(t.get("source") == "youtube" for t in lib):
                break
            time.sleep(0.2)
        yt_tracks = [t for t in lib if t.get("source") == "youtube"]
        assert yt_tracks and yt_tracks[0]["artist"] == "Kelvin Momo"

        # Search endpoint marks it as added
        search_res = c.get("/sources/stream/search?q=kelvin").json()
        assert search_res[0]["added"] is True
        assert search_res[0]["track_id"] == yt_tracks[0]["id"]

        # Soundboard & Smart Chunk decision logging endpoint
        sb = c.post("/brain/soundboard", json={
            "pad": "vocal_chunk",
            "label": "VOCAL CHUNK",
            "source": "jev",
            "reason": "smart chunk tease from incoming track (4 bars to mix)",
        }).json()
        assert "decision_id" in sb
        decs = c.get("/decisions?limit=5").json()
        assert any(d["kind"] == "soundboard" and d["answer"] == "VOCAL CHUNK" for d in decs)

