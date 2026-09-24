"""Rule-based DJ logic: candidate filter, fallback next-track pick, transition planning.

Jev decides on top of this. When Jev is unsure (confidence < threshold) or offline,
these rules make the call on their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analysis.camelot import camelot_distance

PHASES = ["warm-up", "build", "peak", "cool-down"]
VIBES = PHASES + ["auto"]

TRANSITION_BARS = {
    "long_blend": 32, "quick_cut": 4, "filter_fade": 16, "echo_out": 8,
    "wash_out": 8, "brake": 2, "loop_roll": 4,
    "chop": 8, "tease": 8, "rewind": 2,
}
# Styles that drop the new track in on its drop (the energy lands on the one).
DROP_STYLES = ("quick_cut", "loop_roll", "brake", "echo_out", "tease", "rewind")
FLAIRS = ("smooth", "creative", "turnt")
# How long each track plays before Jev moves on (seconds). None = play to the outro.
MIX_STYLES: dict[str, tuple[int, int] | None] = {"radio": None, "club": (110, 200), "quick": (50, 100)}
TRANSITION_STYLES = list(TRANSITION_BARS)

SYNC_LIMIT = 0.08    # pitch range for a straight tempo sync
BRIDGE_LIMIT = 0.16  # gaps up to this are bridged: outgoing ramps up/down, incoming starts halfway
LOOPABLE = ("long_blend", "filter_fade")
SYNCED = LOOPABLE + ("chop", "tease")  # both tracks sound together (or trade bars): tempos must match
RAMP_BARS = 16

PHASE_ENERGY = {"warm-up": 0.4, "build": 0.6, "peak": 0.82, "cool-down": 0.45}
VOCAL_CLASH = 0.55

Track = dict[str, Any]

MAX_TRACK_SECONDS = 900  # longer files are DJ mixes or albums: never auto-picked


# ---------------------------------------------------------------- helpers

def bpm_ratio(a: float, b: float) -> float:
    """Pitch change needed to bring b to a's tempo, allowing half/double time. 1.0 = no change."""
    best = float("inf")
    for mult in (0.5, 1.0, 2.0):
        r = a / (b * mult)
        if abs(r - 1) < abs(best - 1):
            best = r
    return best


def bpm_compatible(a: float, b: float, tolerance: float = 0.08) -> bool:
    return abs(bpm_ratio(a, b) - 1) <= tolerance


def key_distance(a: Track, b: Track) -> int:
    try:
        return camelot_distance(a["camelot"], b["camelot"])
    except (KeyError, ValueError, TypeError):
        return 6


def same_genre(a: Track, b: Track) -> bool:
    ga, gb = (a.get("genre") or "").lower(), (b.get("genre") or "").lower()
    return bool(ga) and ga == gb


def target_energy(current: Track | None, phase: str) -> float:
    base = PHASE_ENERGY.get(phase, 0.6)
    if current is None:
        return base
    cur = current.get("energy") or 0.5
    step = {"warm-up": 0.02, "build": 0.07, "peak": 0.03, "cool-down": -0.07}.get(phase, 0.0)
    return 0.5 * base + 0.5 * (cur + step)


def key_relation(a: Track, b: Track) -> str:
    kd = key_distance(a, b)
    return {0: "same key: perfect", 1: "adjacent on the Camelot wheel: harmonic",
            2: "two steps: a little tense"}.get(kd, f"clash ({kd} steps on the Camelot wheel): avoid long overlaps")


def reason_line(current: Track | None, nxt: Track) -> str:
    if current is None:
        return f"opener: {nxt['camelot']}, {nxt['bpm']:.0f} BPM, energy {nxt['energy']:.2f}"
    de = (nxt.get("energy") or 0) - (current.get("energy") or 0)
    return (
        f"key {current['camelot']}->{nxt['camelot']}, "
        f"BPM {current['bpm']:.0f}->{nxt['bpm']:.0f}, energy {de:+.2f}"
    )


# ---------------------------------------------------------------- candidates

def candidates(
    current: Track | None,
    library: list[Track],
    recent_ids: list[int],
    exclude_ids: list[int] | None = None,
    limit: int = 20,
    phase: str = "build",
) -> list[Track]:
    """Pre-filter to <= limit tracks. Relaxes constraints step by step so auto mode never starves."""
    blocked = set(recent_ids) | set(exclude_ids or [])
    if current is not None:
        blocked.add(current["id"])
    library = [t for t in library if (t.get("duration") or 0) <= MAX_TRACK_SECONDS]
    pool = [t for t in library if t["id"] not in blocked and t.get("bpm")]
    if not pool:  # tiny library: allow repeats, but never the current or excluded track
        hard = set(exclude_ids or []) | ({current["id"]} if current else set())
        pool = [t for t in library if t["id"] not in hard and t.get("bpm")]
    if current is None:
        return sorted(pool, key=lambda t: rule_score(None, t, phase), reverse=True)[:limit]

    steps = [
        lambda t: bpm_compatible(current["bpm"], t["bpm"], 0.08) and key_distance(current, t) <= 1,
        lambda t: bpm_compatible(current["bpm"], t["bpm"], 0.08) and key_distance(current, t) <= 2,
        lambda t: bpm_compatible(current["bpm"], t["bpm"], 0.08),
        lambda t: bpm_compatible(current["bpm"], t["bpm"], 0.15),
        lambda t: True,
    ]
    chosen: list[Track] = []
    for rule in steps:
        chosen = [t for t in pool if rule(t)]
        if len(chosen) >= min(2, len(pool)):
            break
    chosen.sort(key=lambda t: rule_score(current, t, phase), reverse=True)
    return chosen[:limit]


def rule_score(current: Track | None, t: Track, phase: str) -> float:
    """Higher is better: best Camelot match, closest BPM, energy step toward the phase target."""
    energy_gap = abs((t.get("energy") or 0.5) - target_energy(current, phase))
    if current is None:
        return 1.0 - energy_gap
    kd = key_distance(current, t)
    key_score = {0: 1.0, 1: 0.85, 2: 0.45}.get(kd, 0.1)
    bpm_gap = abs(bpm_ratio(current["bpm"], t["bpm"]) - 1)
    bpm_score = max(0.0, 1 - bpm_gap / BRIDGE_LIMIT) - (0.15 if bpm_gap > SYNC_LIMIT else 0.0)
    genre = 0.1 if same_genre(current, t) else 0.0
    return 0.4 * key_score + 0.3 * bpm_score + 0.3 * (1 - min(energy_gap / 0.4, 1)) + genre


def taste_bonus(t: Track, taste: dict | None) -> float:
    """Small nudge from listening memory: favourite genres up, skipped tracks down."""
    if not taste:
        return 0.0
    bonus = -0.25 * min(taste.get("skipped", {}).get(t["id"], 0), 2)
    fav = [g for g, _n in taste.get("genres", [])]
    if t.get("genre") and t["genre"] in fav:
        bonus += 0.08 * (len(fav) - fav.index(t["genre"])) / len(fav)
    return bonus


def pick_next(current: Track | None, cands: list[Track], phase: str, taste: dict | None = None) -> Track | None:
    if not cands:
        return None
    return max(cands, key=lambda t: rule_score(current, t, phase) + taste_bonus(t, taste))


def switch_candidates(current: Track, library: list[Track], recent_ids: list[int], exclude_ids: list[int] | None = None,
                      limit: int = 20) -> list[Track]:
    """'Switch it up': still mixable (BPM within 15%), but a different genre or a clear energy change."""
    blocked = set(recent_ids) | set(exclude_ids or []) | {current["id"]}
    pool = [t for t in library if t["id"] not in blocked and t.get("bpm") and bpm_compatible(current["bpm"], t["bpm"], 0.15)
            and (t.get("duration") or 0) <= MAX_TRACK_SECONDS]
    fresh = [t for t in pool if not same_genre(current, t)
             or abs((t.get("energy") or 0.5) - (current.get("energy") or 0.5)) > 0.15]
    chosen = fresh or pool
    chosen.sort(key=lambda t: (key_distance(current, t), abs(bpm_ratio(current["bpm"], t["bpm"]) - 1)))
    return chosen[:limit]


def transition_smoothness(a: Track, b: Track) -> int:
    """0 clashing, 1 noticeable but okay, 2 smooth, 3 seamless (same scale as the Jev Score)."""
    kd = key_distance(a, b)
    bpm_gap = abs(bpm_ratio(a["bpm"], b["bpm"]) - 1)
    if kd > 2 or bpm_gap > BRIDGE_LIMIT:
        return 0
    if kd == 2 or bpm_gap > 0.04:
        return 1
    if kd == 0 and bpm_gap < 0.015:
        return 3
    return 2


# ---------------------------------------------------------------- transitions

def style_options(a: Track, b: Track, phase: str, mix_style: str = "club", flair: str = "smooth") -> list[str]:
    """Transition styles that suit this pair, best first. Flair adds the DJ routines."""
    base = _base_options(a, b, phase, mix_style)
    if flair == "smooth":
        return base
    delta = (b.get("energy") or 0.5) - (a.get("energy") or 0.5)
    kd = key_distance(a, b)
    synced_ok = abs(bpm_ratio(a["bpm"], b["bpm"]) - 1) <= BRIDGE_LIMIT
    routines: list[str] = []
    if synced_ok and kd <= 2 and delta > 0.05:
        routines.append("tease")  # tease the new drop over the outgoing hook, then switch
    if synced_ok and (kd > 2 or mix_style == "quick" or "hip" in (b.get("genre") or "")):
        routines.append("chop")  # trading bars never overlaps, so clashing keys are fine
    if flair == "turnt":
        if synced_ok and "chop" not in routines:
            routines.append("chop")
        if synced_ok and kd <= 2 and "tease" not in routines:
            routines.append("tease")
        routines.append("rewind")
        return routines + [s for s in base if s not in routines]
    # creative: routines lead when they clearly suit, otherwise they sit behind the classic moves
    extra = [s for s in ("tease", "chop") if s not in routines and synced_ok and (s != "tease" or kd <= 2)]
    return routines + base + extra


def _base_options(a: Track, b: Track, phase: str, mix_style: str) -> list[str]:
    delta = (b.get("energy") or 0.5) - (a.get("energy") or 0.5)
    bpm_gap = abs(bpm_ratio(a["bpm"], b["bpm"]) - 1)
    if key_distance(a, b) > 2 or bpm_gap > BRIDGE_LIMIT:
        if delta > 0.1:
            return ["loop_roll", "quick_cut", "brake"]
        if delta < -0.1:
            return ["quick_cut", "brake", "wash_out", "echo_out"]
        return ["quick_cut", "loop_roll", "brake", "echo_out"]
    if delta > 0.25:
        return ["loop_roll", "quick_cut", "long_blend"]
    if delta < -0.25:
        return ["wash_out", "echo_out", "quick_cut"]
    if phase == "peak" and delta < -0.1:
        return ["echo_out", "wash_out", "long_blend"]
    if a.get("genre") and b.get("genre") and not same_genre(a, b):
        return ["filter_fade", "echo_out", "long_blend"]
    if mix_style == "quick":
        return ["quick_cut", "loop_roll", "echo_out", "long_blend"]
    return ["long_blend", "filter_fade", "wash_out"]


def pick_style(
    a: Track,
    b: Track,
    phase: str,
    recent: list[str] | None = None,
    mix_style: str = "club",
    flair: str = "smooth",
    learned: dict[str, float] | None = None,
    rng: Any = None,
) -> str:
    """Best-suited style: musical fit first, then what the listener has rated up or down,
    skipping the last two moves so the set doesn't sound samey. Now and then (not in SMOOTH)
    it tries the runner-up so it keeps learning."""
    options = style_options(a, b, phase, mix_style, flair)
    scored = []
    for i, s in enumerate(options):
        score = 1.0 - 0.15 * i + (learned or {}).get(s, 0.0)
        if s in (recent or [])[-2:]:
            score -= 0.6
        scored.append((score, s))
    scored.sort(reverse=True)
    if rng is not None and flair != "smooth" and len(scored) > 1 and rng.random() < 0.12:
        return scored[rng.randrange(1, min(3, len(scored)))][1]
    return scored[0][1]


@dataclass
class TransitionPlan:
    style: str
    bars: int
    mix_out_s: float
    mix_in_s: float
    rate: float
    out_bar: int
    shifted_for_vocals: bool = False
    notes: list[str] = field(default_factory=list)
    tempo_ramp: dict[str, float] | None = None  # {"out_rate": r, "bars": n}: ramp outgoing before the mix
    loop: dict[str, float] | None = None  # {"start_s", "end_s", "bars"}: loop the outgoing to stretch its outro
    pre: dict[str, Any] | None = None  # routine on the outgoing before the transition (e.g. run it back)
    edit: dict[str, float] | None = None  # skip-the-dull-part jump inside the incoming track once it is live

    def to_dict(self) -> dict[str, Any]:
        return {
            "style": self.style,
            "bars": self.bars,
            "mix_out_s": round(self.mix_out_s, 4),
            "mix_in_s": round(self.mix_in_s, 4),
            "rate": round(self.rate, 5),
            "out_bar": self.out_bar,
            "shifted_for_vocals": self.shifted_for_vocals,
            "notes": self.notes,
            "tempo_ramp": self.tempo_ramp,
            "loop": self.loop,
            "pre": self.pre,
            "edit": self.edit,
        }


def _downbeats(t: Track) -> list[float]:
    dbs = t.get("downbeats") or []
    if dbs:
        return list(dbs)
    period = t.get("beat_period") or 60.0 / (t.get("bpm") or 120)
    first = t.get("first_downbeat") or 0.0
    n = int(((t.get("duration") or 0) - first) / (4 * period))
    return [first + i * 4 * period for i in range(max(n, 1))]


def _bar_at(downbeats: list[float], s: float) -> int:
    for i, d in enumerate(downbeats):
        if d >= s - 0.01:
            return i
    return max(len(downbeats) - 1, 0)


def vocal_window(t: Track, start_bar: int, bars: int) -> list[float]:
    v = t.get("vocal_bars") or []
    return v[start_bar:start_bar + bars]


def vocal_heavy_at(t: Track, start_bar: int, bars: int) -> bool:
    w = vocal_window(t, start_bar, min(bars, 8))
    return bool(w) and sum(w) / len(w) > VOCAL_CLASH


def shift_for_vocals(t: Track, out_bar: int, bars: int) -> int:
    """Move the mix-out point to the next 8-bar instrumental phrase, or the nearest earlier one."""
    dbs = _downbeats(t)
    last_ok = len(dbs) - bars
    for b in range(out_bar + 8 - out_bar % 8, last_ok + 1, 8):
        if not vocal_heavy_at(t, b, bars):
            return b
    for b in range(out_bar - out_bar % 8 - 8, 0, -8):
        if not vocal_heavy_at(t, b, bars) and b >= len(dbs) // 3:
            return b
    return out_bar


def peak_section(t: Track, width: int = 16) -> tuple[int, int]:
    """Bars [start, end) of the highest-energy stretch: usually the drop or the big chorus."""
    e = t.get("energy_bars") or []
    if len(e) <= width:
        return 0, len(e)
    sums = [sum(e[i:i + width]) for i in range(len(e) - width + 1)]
    start = max(range(len(sums)), key=sums.__getitem__)
    return start, start + width


def first_drop_bar(t: Track) -> int:
    """First phrase where the track really kicks in (energy near its peak). 0 if unknown."""
    e = t.get("energy_bars") or []
    if len(e) < 16:
        return 0
    top = max(e)
    for i in range(4, len(e) - 4):
        if min(e[i:i + 4]) >= 0.8 * top:  # four solid bars, not one spike
            return i - i % 4
    return 0


def choose_out_bar(a: Track, start_s: float | None, mix_style: str, need: int) -> tuple[int, str | None]:
    """Where to leave the outgoing track. Club/quick styles leave after its best part instead of
    waiting for the outro: the first phrase after the peak, inside the play-time window, off the vocals."""
    dbs = _downbeats(a)
    n = len(dbs)
    last_ok = max(n - need - 1, 0)
    default = min(_bar_at(dbs, a.get("outro_start") or dbs[last_ok]), last_ok)
    window = MIX_STYLES.get(mix_style)
    if not window or n < 24:
        return default, None
    bar_s = 4 * (a.get("beat_period") or 60.0 / (a.get("bpm") or 120))
    start_bar = _bar_at(dbs, start_s or 0.0)
    lo = start_bar + int(window[0] / bar_s + 0.999)
    hi = min(start_bar + int(window[1] / bar_s), default)
    if lo > hi:
        return default, None
    _ps, peak_end = peak_section(a)
    best, best_score = None, float("-inf")
    for bar in range(lo - lo % 8 + (8 if lo % 8 else 0), hi + 1, 8):
        score = (2.0 if bar >= peak_end else 0.0) + (1.0 if not vocal_heavy_at(a, bar, 8) else 0.0)
        score -= abs(bar - max(peak_end, lo)) / 32
        if score > best_score:
            best, best_score = bar, score
    if best is None:
        return default, None
    played = (dbs[best] - (start_s or 0.0)) / 60
    return best, f"{mix_style} mix: leaving after {played:.1f} min at bar {best} (peak ends bar {peak_end})"


def plan_transition(
    a: Track,
    b: Track,
    style: str,
    shift_vocals: bool | None = None,
    start_s: float | None = None,
    mix_style: str = "radio",
    flair: str = "smooth",
    rng: Any = None,
) -> TransitionPlan:
    """Choose beat-aligned mix points. shift_vocals=None means decide from vocal density here.
    start_s is where the outgoing track started playing (for the club/quick play-time window)."""
    dbs_a = _downbeats(a)
    dbs_b = _downbeats(b)
    notes: list[str] = []

    # Tempo: sync straight within +-8%; bridge gaps up to 16% by meeting in the middle.
    ratio = bpm_ratio(a["bpm"], b["bpm"])
    gap = abs(ratio - 1)
    tempo_ramp = None
    if gap > BRIDGE_LIMIT and style in SYNCED:
        notes.append(f"tempo gap {gap:.0%} is too big to blend, cutting instead")
        style = "quick_cut"
    kd = key_distance(a, b)
    if kd > 2 and style == "tease":
        notes.append(f"keys {a.get('camelot')} and {b.get('camelot')} clash: chop instead of a tease")
        style = "chop"
    if kd > 2 and style in LOOPABLE:
        # Long overlaps between clashing keys sound out of tune: switch to a short, energetic change.
        delta = (b.get("energy") or 0.5) - (a.get("energy") or 0.5)
        style = "loop_roll" if delta > 0.05 else "quick_cut"
        notes.append(f"keys {a.get('camelot')} and {b.get('camelot')} clash ({kd} steps): {style} instead of a blend")
    if gap <= SYNC_LIMIT:
        rate = ratio
    elif gap <= BRIDGE_LIMIT and style in SYNCED:
        rate = ratio ** 0.5
        tempo_ramp = {"out_rate": round(1 / rate, 5), "bars": RAMP_BARS}
        meet = a["bpm"] / rate
        notes.append(f"tempo bridge: outgoing ramps {a['bpm']:.0f}->{meet:.1f} BPM over {RAMP_BARS} bars, "
                     f"incoming starts at {meet:.1f} and eases home")
    else:
        rate = 1.0
        if gap > SYNC_LIMIT:
            notes.append("tempo too far apart to sync, incoming plays at native speed")
    bars = TRANSITION_BARS[style]

    # Mix out at the outro phrase. Blends can loop the outro, so they only need 8 real bars left.
    need = 8 if style in LOOPABLE else bars
    out_bar, why = choose_out_bar(a, start_s, mix_style, need)
    if why:
        notes.append(why)

    shifted = False
    should_shift = vocal_heavy_at(a, out_bar, need) if shift_vocals is None else shift_vocals
    if should_shift:
        new_bar = shift_for_vocals(a, out_bar, need)
        if new_bar != out_bar:
            notes.append(f"mix point moved from bar {out_bar} to {new_bar} to avoid vocals")
            out_bar, shifted = new_bar, True

    loop = None
    room = len(dbs_a) - 1 - out_bar
    if room < bars and style in LOOPABLE:
        loop_bars = 8 if room >= 8 else 4
        if room >= loop_bars:
            loop = {"start_s": round(dbs_a[out_bar], 4), "end_s": round(dbs_a[out_bar + loop_bars], 4), "bars": loop_bars}
            notes.append(f"outro only {room} bars: looping bars {out_bar}-{out_bar + loop_bars} to fit a {bars}-bar blend")
        else:
            bars = max(4, room - room % 4)
            notes.append(f"outro only {room} bars, blend shortened to {bars}")

    # Where the incoming track enters. Always on a downbeat so the grids line up bar for bar.
    in_bar = 0
    if style == "chop":
        # Trade bars, landing on the new track's drop when the chop ends.
        drop = first_drop_bar(b) or _bar_at(dbs_b, b.get("intro_end") or 0)
        in_bar = max(drop - bars, 0)
        in_bar -= in_bar % 4
        notes.append(f"chop: trading bars, landing on its bar {in_bar + bars}")
    elif style in DROP_STYLES:
        # Drop the new track straight in on its drop, so the energy lands on the one.
        in_bar = first_drop_bar(b) or max(_bar_at(dbs_b, b.get("intro_end") or 0) - 4, 0)
        in_bar -= in_bar % 4
        if in_bar:
            notes.append(f"incoming drops in at its bar {in_bar}")
    else:
        # Blends ride the intro, but skip very long intros.
        intro_bar = _bar_at(dbs_b, b.get("intro_end") or 0)
        if intro_bar > 32:
            in_bar = intro_bar - min(bars, 16)
            in_bar -= in_bar % 8
            notes.append(f"long intro: incoming starts at bar {in_bar}")
    in_bar = min(in_bar, max(len(dbs_b) - 1, 0))

    pre = run_it_back(a, out_bar, style, flair, rng)
    if pre:
        notes.append(f"run it back: rewind to the hook at bar {pre['bar']} for {pre['bars']} bars first")
    edit = skip_edit(b, in_bar, mix_style, flair)
    if edit:
        notes.append(f"edit: skip bars {edit['from_bar']}-{edit['to_bar']} of the incoming (the quiet stretch)")

    return TransitionPlan(
        style=style,
        bars=bars,
        mix_out_s=dbs_a[out_bar] if dbs_a else 0.0,
        mix_in_s=dbs_b[in_bar] if dbs_b else 0.0,
        rate=rate,
        out_bar=out_bar,
        shifted_for_vocals=shifted,
        notes=notes,
        tempo_ramp=tempo_ramp,
        loop=loop,
        pre=pre,
        edit=edit,
    )


def run_it_back(a: Track, out_bar: int, style: str, flair: str, rng: Any = None) -> dict[str, Any] | None:
    """Rewind the outgoing to its hook and play it again before leaving. Crowd-pleaser: used on
    energetic tracks, always in TURNT, about a third of the time in CREATIVE, never in SMOOTH."""
    if flair == "smooth" or style not in DROP_STYLES + ("chop",) or (a.get("energy") or 0) < 0.5:
        return None
    if flair == "creative" and (rng is None or rng.random() > 0.35):
        return None
    start, _end = peak_section(a, 8)
    dbs = _downbeats(a)
    if not dbs or start >= out_bar - 8:
        return None
    start -= start % 4
    return {"type": "run_it_back", "bar": start, "jump_to_s": round(dbs[start], 4), "bars": 8}


def skip_edit(b: Track, in_bar: int, mix_style: str, flair: str) -> dict[str, Any] | None:
    """A DJ edit: jump from the end of the first big section over a long quiet stretch (verse,
    breakdown) straight to the next big section. Only in CLUB/QUICK with some flair."""
    e = b.get("energy_bars") or []
    dbs = _downbeats(b)
    if flair == "smooth" or mix_style == "radio" or len(e) < 48 or not dbs:
        return None
    top = max(e)
    high = [x >= 0.75 * top for x in e]
    low = [x < 0.55 * top for x in e]
    i = in_bar + 8
    while i < len(e) and not high[i]:
        i += 1  # into the first big section
    while i < len(e) and high[i]:
        i += 1  # to its end
    start = i + (-i % 8)
    j = start
    while j < len(e) and low[j]:
        j += 1
    end = j - j % 8
    if end - start >= 16 and end < len(dbs) - 16:
        return {"at_s": round(dbs[start], 4), "to_s": round(dbs[end], 4), "from_bar": start, "to_bar": end}
    return None


def next_phase(current: str, tracks_played: int) -> str:
    """Rule fallback for the set arc: warm-up -> build -> peak -> peak -> cool-down -> build..."""
    arc = ["warm-up", "build", "peak", "peak", "cool-down", "build", "peak"]
    return arc[min(tracks_played // 5, len(arc) - 1)] if tracks_played < 35 else arc[(tracks_played // 5) % 3 + 1]
