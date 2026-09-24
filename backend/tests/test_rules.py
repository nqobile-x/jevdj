from app.brain import rules


def track(i, bpm, camelot, energy=0.6, genre="amapiano", bars=96, vocal=None):
    period = 60 / bpm
    return {
        "id": i, "title": f"T{i}", "artist": "A", "bpm": bpm, "camelot": camelot, "energy": energy,
        "genre": genre, "beat_period": period, "first_downbeat": 0.0, "duration": bars * 4 * period + 5,
        "downbeats": [b * 4 * period for b in range(bars)],
        "vocal_bars": vocal if vocal is not None else [0.2] * bars,
        "outro_start": (bars - 32) * 4 * period, "intro_end": 16 * 4 * period,
    }


def test_bpm_ratio_half_double():
    assert rules.bpm_compatible(113, 118)
    assert not rules.bpm_compatible(113, 128)
    assert rules.bpm_compatible(90, 176)  # double time
    assert abs(rules.bpm_ratio(120, 60) - 1) < 1e-9


def test_candidates_filter_key_bpm_and_recent():
    cur = track(1, 113, "8A")
    lib = [cur, track(2, 114, "9A"), track(3, 112, "8B"), track(4, 128, "8A"), track(5, 113, "2A"), track(6, 113, "7A")]
    got = {t["id"] for t in rules.candidates(cur, lib, recent_ids=[6])}
    assert got == {2, 3}


def test_candidates_relax_when_starved():
    cur = track(1, 113, "8A")
    lib = [cur, track(2, 140, "3B")]
    assert [t["id"] for t in rules.candidates(cur, lib, [])] == [2]


def test_candidates_never_returns_current_or_excluded():
    cur = track(1, 113, "8A")
    lib = [cur, track(2, 113, "8A")]
    assert rules.candidates(cur, lib, recent_ids=[2], exclude_ids=[2]) == []


def test_pick_next_prefers_same_key_close_bpm():
    cur = track(1, 113, "8A", energy=0.6)
    best = track(2, 113, "8A", energy=0.65)
    worse = track(3, 118, "9A", energy=0.9)
    assert rules.pick_next(cur, [worse, best], "build")["id"] == 2


def test_style_variety_avoids_repeats():
    a, b = track(1, 113, "8A", energy=0.6), track(2, 113, "8A", energy=0.62)
    assert rules.pick_style(a, b, "build") == "long_blend"
    assert rules.pick_style(a, b, "build", recent=["long_blend"]) == "filter_fade"
    assert rules.pick_style(a, b, "build", recent=["long_blend", "filter_fade"]) == "wash_out"


def _energetic(i, bpm, bars=96, peak=(40, 56)):
    t = track(i, bpm, "8A", bars=bars)
    t["energy_bars"] = [0.9 if peak[0] <= b < peak[1] else 0.4 for b in range(bars)]
    return t


def test_club_mix_leaves_after_the_peak_not_at_the_outro():
    a = _energetic(1, 120)  # 2 s bars, 96 bars = 3.2 min, outro at bar 64
    plan = rules.plan_transition(a, track(2, 120, "8A"), "long_blend", shift_vocals=False, start_s=0, mix_style="club")
    assert plan.out_bar == 56 and plan.out_bar % 8 == 0
    radio = rules.plan_transition(a, track(2, 120, "8A"), "long_blend", shift_vocals=False, start_s=0, mix_style="radio")
    assert radio.out_bar == 64


def test_quick_mix_is_shorter():
    a = _energetic(1, 120, peak=(16, 32))
    plan = rules.plan_transition(a, track(2, 120, "8A"), "quick_cut", shift_vocals=False, start_s=0, mix_style="quick")
    assert 25 <= plan.out_bar <= 50


def test_drop_styles_enter_on_the_drop():
    b = _energetic(2, 120, peak=(24, 40))
    plan = rules.plan_transition(track(1, 120, "8A"), b, "quick_cut", shift_vocals=False)
    assert plan.mix_in_s == b["downbeats"][24]


def test_style_rules():
    a = track(1, 113, "8A", energy=0.6)
    assert rules.pick_style(a, track(2, 113, "8A", energy=0.62), "build") == "long_blend"
    assert rules.pick_style(a, track(3, 113, "2A"), "build") == "quick_cut"
    assert rules.pick_style(a, track(4, 113, "8A", genre="deep house"), "build") == "filter_fade"
    assert rules.pick_style(track(5, 113, "8A", energy=0.85), track(6, 113, "8A", energy=0.7), "peak") == "echo_out"


def test_plan_is_beat_aligned_and_fits():
    a, b = track(1, 113, "8A"), track(2, 115, "9A")
    plan = rules.plan_transition(a, b, "long_blend")
    assert plan.mix_out_s in a["downbeats"]
    assert plan.mix_in_s in b["downbeats"]
    real_bars = plan.loop["bars"] if plan.loop else plan.bars  # a loop stretches the outro
    assert plan.out_bar + real_bars < len(a["downbeats"])
    assert abs(plan.rate - 113 / 115) < 1e-9


def test_vocal_shift_moves_to_instrumental_phrase():
    vocal = [0.2] * 96
    for i in range(64, 72):
        vocal[i] = 0.9
    a = track(1, 113, "8A", vocal=vocal)
    a["outro_start"] = a["downbeats"][64]
    b = track(2, 113, "8A")
    plan = rules.plan_transition(a, b, "quick_cut", shift_vocals=True)
    assert plan.shifted_for_vocals and plan.out_bar == 72
    assert rules.plan_transition(a, b, "quick_cut", shift_vocals=False).out_bar == 64


def test_short_outro_loops_instead_of_shortening():
    a = track(1, 113, "8A", bars=64)
    a["outro_start"] = a["downbeats"][48]
    plan = rules.plan_transition(a, track(2, 113, "8A"), "long_blend", shift_vocals=False)
    assert plan.out_bar == 48 and plan.bars == 32
    assert plan.loop == {"start_s": round(a["downbeats"][48], 4), "end_s": round(a["downbeats"][56], 4), "bars": 8}


def test_tempo_bridge_meets_in_the_middle():
    a, b = track(1, 113, "8A"), track(2, 124, "8A")  # 9.7% apart: too far to sync, close enough to bridge
    plan = rules.plan_transition(a, b, "long_blend", shift_vocals=False)
    meet_out = 113 * plan.tempo_ramp["out_rate"]
    meet_in = 124 * plan.rate
    assert abs(meet_out - meet_in) < 0.01
    assert abs(plan.rate - 1) <= 0.08 and abs(plan.tempo_ramp["out_rate"] - 1) <= 0.08
    assert rules.transition_smoothness(a, b) == 1


def test_key_clash_never_long_blends():
    a, b = track(1, 113, "8A", energy=0.6), track(2, 117, "12A", energy=0.57)
    plan = rules.plan_transition(a, b, "long_blend", shift_vocals=False)
    assert plan.style == "quick_cut" and any("clash" in n for n in plan.notes)
    up = rules.plan_transition(a, track(3, 113, "2A", energy=0.8), "filter_fade", shift_vocals=False)
    assert up.style == "loop_roll"
    ok = rules.plan_transition(a, track(4, 113, "9A"), "long_blend", shift_vocals=False)
    assert ok.style == "long_blend"


def test_huge_tempo_gap_forces_cut():
    plan = rules.plan_transition(track(1, 90, "8A"), track(2, 113, "8A"), "long_blend", shift_vocals=False)
    assert plan.style == "quick_cut" and plan.tempo_ramp is None and plan.rate == 1.0


def test_smoothness_scale():
    a = track(1, 113, "8A")
    assert rules.transition_smoothness(a, track(2, 113, "8A")) == 3
    assert rules.transition_smoothness(a, track(3, 114, "9A")) == 2
    assert rules.transition_smoothness(a, track(4, 113, "3B")) == 0


def test_switch_candidates_prefer_new_direction():
    cur = track(1, 113, "8A", energy=0.5, genre="amapiano")
    lib = [cur, track(2, 113, "8A", energy=0.52, genre="amapiano"), track(3, 118, "9A", genre="afro house"),
           track(4, 113, "8A", energy=0.8, genre="amapiano"), track(5, 140, "8A", genre="afro house")]
    got = [t["id"] for t in rules.switch_candidates(cur, lib, [])]
    assert set(got) == {3, 4}


def test_taste_bonus_penalises_skips_and_likes_favourites():
    t = track(2, 113, "8A", genre="amapiano")
    taste = {"genres": [("amapiano", 10), ("deep house", 2)], "skipped": {3: 2}}
    assert rules.taste_bonus(t, taste) > 0
    assert rules.taste_bonus(track(3, 113, "8A", genre="hip hop"), taste) < 0


def test_clean_tag_strips_site_watermarks():
    from app.analysis.analyser import clean_tag

    assert clean_tag("Your Body ft. Wizkid || FlexyJam.com") == "Your Body ft. Wizkid"
    assert clean_tag("Track (www.fakaza.com)") == "Track"
    assert clean_tag("Mr. Brown - Track") == "Mr. Brown - Track"


def test_long_dj_mixes_are_never_picked():
    cur = track(1, 113, "8A")
    mix = track(2, 113, "8A")
    mix["duration"] = 3200
    assert rules.candidates(cur, [cur, mix, track(3, 113, "9A")], []) == [rules.candidates(cur, [cur, track(3, 113, "9A")], [])[0]]
