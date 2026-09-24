"""SMART ORDER: energy arc + smooth neighbours, and the /set/order + /brain/next integration."""

from app.brain import order as smart
from app.brain import rules
from tests.test_api import FakeJev, make_client
from tests.test_rules import track


def _pool():
    # A messy library: mixed keys, tempos and energies, listed in a bad order on purpose.
    spec = [(124, "3B", 0.92), (112, "8A", 0.35), (113, "9A", 0.55), (122, "4B", 0.85), (113, "8A", 0.45),
            (114, "10A", 0.66), (123, "3B", 0.9), (112, "7A", 0.4), (115, "10A", 0.72), (120, "4B", 0.8)]
    return [track(i, bpm, cam, energy=en) for i, (bpm, cam, en) in enumerate(spec, 1)]


def _smooth_pct(seq):
    pairs = list(zip(seq, seq[1:]))
    return sum(rules.transition_smoothness(a, b) >= 2 for a, b in pairs) / len(pairs)


def test_order_uses_every_track_once_and_is_deterministic():
    pool = _pool()
    a = smart.smart_order(pool, "journey")
    b = smart.smart_order(list(reversed(pool)), "journey")
    assert sorted(t["id"] for t in a) == list(range(1, 11))
    assert [t["id"] for t in a] == [t["id"] for t in b]


def test_order_is_smoother_than_the_library_order():
    pool = _pool()
    ordered = smart.smart_order(pool, "journey")
    assert _smooth_pct(ordered) > _smooth_pct(pool)
    assert _smooth_pct(ordered) >= 0.7


def test_journey_groups_high_energy_at_the_peak():
    ordered = smart.smart_order(_pool(), "journey")
    e = [t["energy"] for t in ordered]
    third = len(e) // 3
    # warm-up starts low, the hottest tracks sit together in the back half
    assert sum(e[:third]) / third < sum(e[-2 * third:]) / (2 * third)
    top = sorted(range(len(e)), key=lambda i: -e[i])[:3]
    assert max(top) - min(top) <= 3


def test_build_rises():
    e = [t["energy"] for t in smart.smart_order(_pool(), "build")]
    assert e[0] < e[-1] and sum(e[:3]) < sum(e[-3:])


def test_continues_from_the_live_track():
    pool = _pool()
    live = pool[4]  # 113 BPM, 8A
    ordered = smart.smart_order(pool, "journey", start=live, offset=3, total=13)
    assert live["id"] not in [t["id"] for t in ordered]
    assert rules.transition_smoothness(live, ordered[0]) >= 2


def test_phase_follows_the_arc():
    assert smart.phase_at("journey", 0.05) == "warm-up"
    assert smart.phase_at("journey", 0.7) == "peak"
    assert smart.phase_at("journey", 0.95) == "cool-down"
    assert smart.phase_at("peak", 0.1) == "peak"


def test_peak_starts_with_the_hottest_block():
    e = [t["energy"] for t in smart.smart_order(_pool(), "peak")]
    assert sum(e[:3]) / 3 >= 0.8 and e[0] > e[-1]


def test_api_smart_order_drives_next(tmp_path):
    c, _fake = make_client(tmp_path, FakeJev())
    with c:
        c.post("/set/start", json={"vibe": "auto"})
        st = c.post("/set/order", json={"active": True, "shape": "journey"}).json()
        assert st["active"] and len(st["items"]) == 5 and st["shape"] == "journey"
        plan = [i["id"] for i in st["items"]]

        # Follow the order: each pick is the next unplayed track in the plan.
        cur = None
        for k in range(3):
            r = c.post("/brain/next", json={"current_id": cur}).json()
            assert r["source"] == "order" and r["reason"].startswith("smart order")
            assert r["track"]["id"] == plan[k]
            cur = r["track"]["id"]
            c.post("/history/played", json={"track_id": cur, "transition": None, "source": "order"})

        st = c.get("/set/order").json()
        assert [i["played"] for i in st["items"]][:3] == [True, True, True]

        # SWITCH IT UP still breaks out of the order.
        r = c.post("/brain/next", json={"current_id": cur, "switch": True}).json()
        assert r["source"] != "order"

        # Off: back to the normal brain.
        c.post("/set/order", json={"active": False})
        r = c.post("/brain/next", json={"current_id": cur}).json()
        assert r["source"] in ("jev", "rules")


def test_api_order_replans_after_a_manual_pick(tmp_path):
    c, _fake = make_client(tmp_path, FakeJev())
    with c:
        c.post("/set/start", json={"vibe": "auto"})
        plan = [i["id"] for i in c.post("/set/order", json={"active": True}).json()["items"]]
        manual = plan[-1]  # the listener jumps to the last track
        c.post("/history/played", json={"track_id": manual, "transition": None, "source": "user"})
        r = c.post("/brain/next", json={"current_id": manual}).json()
        assert r["source"] == "order" and r["track"]["id"] != manual
        st = c.get("/set/order").json()
        assert st["items"][0]["id"] == manual  # the plan now flows on from what is playing


def test_api_new_shape_keeps_what_was_played(tmp_path):
    c, _fake = make_client(tmp_path, FakeJev())
    with c:
        c.post("/set/start", json={"vibe": "auto"})
        plan = [i["id"] for i in c.post("/set/order", json={"active": True}).json()["items"]]
        for tid in plan[:2]:
            c.post("/history/played", json={"track_id": tid, "transition": None, "source": "order"})
        st = c.post("/set/order", json={"active": True, "shape": "peak", "current_id": plan[1]}).json()
        assert [i["id"] for i in st["items"]][:2] == plan[:2]
        assert [i["played"] for i in st["items"]][:2] == [True, True]
        r = c.post("/brain/next", json={"current_id": plan[1]}).json()
        assert r["track"]["id"] not in plan[:2] and r["reason"].startswith("smart order 3/")


def test_api_live_track_at_switch_on_is_not_replayed(tmp_path):
    c, _fake = make_client(tmp_path, FakeJev())
    with c:
        c.post("/set/start", json={"vibe": "auto"})
        c.post("/history/played", json={"track_id": 1, "transition": None, "source": "jev"})  # playing before ORDER
        plan = [i["id"] for i in c.post("/set/order", json={"active": True, "current_id": 1}).json()["items"]]
        assert plan[0] == 1
        cur = 1
        for _ in range(4):  # play the rest of the set: track 1 must never come back
            r = c.post("/brain/next", json={"current_id": cur}).json()
            assert r["track"]["id"] != 1
            cur = r["track"]["id"]
            c.post("/history/played", json={"track_id": cur, "transition": None, "source": "order"})


def test_api_rejects_unknown_shape(tmp_path):
    c, _fake = make_client(tmp_path, FakeJev())
    with c:
        assert c.post("/set/order", json={"active": True, "shape": "chaos"}).status_code == 400
