"""DJ routines, flair and bounded learning."""

import random

from app.brain import rules
from tests.test_api import FakeJev, make_client
from tests.test_rules import track


def energetic(i, bpm, cam="8A", bars=128, peaks=((32, 56), (96, 120)), genre="amapiano"):
    t = track(i, bpm, cam, bars=bars, genre=genre)
    t["energy_bars"] = [0.9 if any(s <= b < e for s, e in peaks) else 0.35 for b in range(bars)]
    t["energy"] = 0.7
    return t


def test_smooth_has_no_routines():
    a, b = track(1, 113, "8A", energy=0.5), track(2, 113, "8A", energy=0.7)
    assert not {"chop", "tease", "rewind"} & set(rules.style_options(a, b, "build", "club", "smooth"))


def test_creative_teases_rising_energy_and_chops_clashes():
    a = track(1, 113, "8A", energy=0.5)
    assert rules.style_options(a, track(2, 113, "9A", energy=0.7), "build", "club", "creative")[0] == "tease"
    assert rules.style_options(a, track(3, 113, "3B", energy=0.5), "build", "club", "creative")[0] == "chop"


def test_turnt_leads_with_routines_and_never_breaks_guards():
    a = track(1, 90, "8A")
    opts = rules.style_options(a, track(2, 125, "8A"), "peak", "club", "turnt")  # 39% tempo gap
    assert "chop" not in opts and "tease" not in opts and "rewind" in opts


def test_learning_nudges_but_does_not_override():
    a, b = track(1, 113, "8A", energy=0.6), track(2, 113, "8A", energy=0.62)
    assert rules.pick_style(a, b, "build", flair="smooth") == "long_blend"
    assert rules.pick_style(a, b, "build", flair="smooth", learned={"long_blend": -0.5}) == "filter_fade"
    # a huge learned bonus for a move that doesn't suit the pair can't sneak it in
    assert rules.pick_style(a, b, "build", flair="smooth", learned={"brake": 0.5}) != "brake"


def test_chop_lands_on_the_drop():
    a, b = energetic(1, 113), energetic(2, 113, "3B", peaks=((24, 40),))
    plan = rules.plan_transition(a, b, "chop", shift_vocals=False)
    assert plan.style == "chop" and plan.bars == 8
    assert plan.mix_in_s == b["downbeats"][16]  # drop at 24 minus 8 bars of chopping


def test_tease_with_clash_becomes_chop():
    plan = rules.plan_transition(energetic(1, 113), energetic(2, 113, "3B"), "tease", shift_vocals=False)
    assert plan.style == "chop"


def test_run_it_back_and_skip_edit():
    a, b = energetic(1, 113), energetic(2, 113)
    plan = rules.plan_transition(a, b, "quick_cut", shift_vocals=False, mix_style="club", flair="turnt",
                                 start_s=0, rng=random.Random(1))
    assert plan.pre and plan.pre["type"] == "run_it_back" and plan.pre["bar"] == 32
    assert plan.edit and plan.edit["from_bar"] == 56 and plan.edit["to_bar"] == 96
    calm = rules.plan_transition(a, b, "quick_cut", shift_vocals=False, mix_style="club", flair="smooth", start_s=0)
    assert calm.pre is None and calm.edit is None


def test_feedback_endpoint_and_bounds(tmp_path):
    c, _ = make_client(tmp_path, FakeJev())
    with c:
        for _ in range(20):
            r = c.post("/feedback", json={"kind": "transition", "style": "chop", "value": 1}).json()
        assert 0 < r["learned"]["chop"]["bonus"] <= 0.5
        assert c.post("/feedback", json={"kind": "transition", "style": "rm -rf", "value": 1}).status_code == 400
        assert c.post("/feedback", json={"kind": "transition", "style": "chop", "value": 5}).status_code == 422
        assert c.post("/learning/reset").json() == {"styles": {}}
        assert c.post("/set/flair", json={"flair": "turnt"}).json() == {"flair": "turnt"}


def test_alternatives_and_prelisten_log(tmp_path):
    c, _ = make_client(tmp_path, FakeJev())
    with c:
        alts = c.post("/brain/alternatives", json={"from_id": 1, "to_id": 2, "exclude": ["long_blend"]}).json()
        assert alts and all(p["style"] != "long_blend" for p in alts) and len(alts) <= 2
        r = c.post("/brain/prelisten", json={"score": 8.4, "style": "chop", "fixes": ["beats lined up (+12 ms)"],
                                             "issues": [], "beat_error_ms": 2}).json()
        d = c.get("/decisions?limit=1").json()[0]
        assert d["id"] == r["decision_id"] and d["kind"] == "prelisten" and "beats lined up" in d["reason"]
