"""SMART ORDER: arrange a pool of tracks into a set that flows.

Two things decide the order:
- the energy arc: each slot in the set has a target energy (warm-up -> peak -> cool-down for
  JOURNEY). Targets are percentiles of the pool's own energy, so the hottest tracks of *this*
  library cluster at the peak whatever the library sounds like;
- mixability: neighbours should share a key (Camelot) and tempo, so every transition is smooth.

A greedy pass builds the chain, then a local search (segment reversals and single moves)
lowers the total cost. Pure function of its inputs: same pool in, same order out.
"""

from __future__ import annotations

import math
from typing import Any

from app.brain import rules

Track = dict[str, Any]

SHAPES = ("journey", "build", "peak")
W_EDGE = 0.7    # how much a rough transition costs
W_ENERGY = 0.3  # how much being off the energy arc costs
LOCAL_SEARCH_MAX = 90  # above this many tracks, greedy only (fast enough for any library)


def arc(shape: str, x: float) -> float:
    """Target energy percentile (0..1) at position x (0..1) of the set."""
    x = min(max(x, 0.0), 1.0)
    if shape == "build":
        return 0.1 + 0.85 * x
    if shape == "peak":  # peak time: the hottest block first, all together, easing off late
        return 0.97 - 0.55 * x ** 2
    # journey: warm up, peak around 70%, bring it home
    if x < 0.7:
        return 0.12 + 0.86 * (x / 0.7) ** 1.3
    return 0.98 - 0.5 * ((x - 0.7) / 0.3)


def phase_at(shape: str, x: float) -> str:
    """The set phase that matches a point on the arc (drives transition choice)."""
    if shape == "peak":
        return "peak" if x < 0.75 else "cool-down"
    if shape == "build":
        return "warm-up" if x < 0.25 else "build" if x < 0.75 else "peak"
    return "warm-up" if x < 0.2 else "build" if x < 0.55 else "peak" if x < 0.82 else "cool-down"


def pair_score(a: Track, b: Track) -> float:
    """0..1: how well b mixes after a (key + tempo, a touch of genre)."""
    kd = rules.key_distance(a, b)
    key = {0: 1.0, 1: 0.85, 2: 0.45}.get(kd, 0.1)
    gap = abs(rules.bpm_ratio(a["bpm"], b["bpm"]) - 1)
    bpm = max(0.0, 1 - gap / rules.BRIDGE_LIMIT) - (0.15 if gap > rules.SYNC_LIMIT else 0.0)
    genre = 0.05 if rules.same_genre(a, b) else 0.0
    return min(1.0, 0.55 * key + 0.45 * max(bpm, 0.0) + genre)


def _energy(t: Track) -> float:
    return float(t.get("energy") or 0.5)


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.5
    i = p * (len(sorted_vals) - 1)
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def smart_order(
    pool: list[Track],
    shape: str = "journey",
    start: Track | None = None,
    offset: int = 0,
    total: int | None = None,
    taste: dict | None = None,
) -> list[Track]:
    """Order `pool` (not including `start`) into the best set.

    start: the track playing now; the order continues from it.
    offset/total: where the first returned track sits in the whole set (for the arc), e.g.
    after 5 played tracks of a 20-track set, offset=6 (5 played + the current one), total=20.
    """
    shape = shape if shape in SHAPES else "journey"
    tracks = [t for t in pool if t.get("bpm") and (t.get("duration") or 0) <= rules.MAX_TRACK_SECONDS
              and (start is None or t["id"] != start["id"])]
    if not tracks:
        return []
    n = len(tracks)
    total = max(total or 0, offset + n)
    energies = sorted([_energy(t) for t in tracks] + ([_energy(start)] if start else []))
    spread = max(energies[-1] - energies[0], 0.12)
    targets = [_percentile(energies, arc(shape, (offset + i) / max(total - 1, 1))) for i in range(n)]
    bonus = {t["id"]: rules.taste_bonus(t, taste) for t in tracks}

    # Cost tables by index: slot[k][i] = track k at position i, edge[k][m] = k then m.
    ids = [t["id"] for t in tracks]
    slot = [[W_ENERGY * min(abs(_energy(t) - targets[i]) / spread * 2, 1.5) - 0.1 * bonus[t["id"]]
             for i in range(n)] for t in tracks]
    edge = [[W_EDGE * (1 - pair_score(a, b)) if a is not b else 9.0 for b in tracks] for a in tracks]
    first = [W_EDGE * (1 - pair_score(start, t)) if start else 0.0 for t in tracks]

    # Greedy: from the current track (or the best opener), always take the cheapest next.
    left = sorted(range(n), key=lambda k: ids[k])
    seq: list[int] = []
    for i in range(n):
        prev = seq[-1] if seq else None
        k = min(left, key=lambda k: ((first[k] if prev is None else edge[prev][k]) + slot[k][i], ids[k]))
        seq.append(k)
        left.remove(k)

    if 3 <= n <= LOCAL_SEARCH_MAX:
        seq = _improve(seq, slot, edge, first)
    return [tracks[k] for k in seq]


def _cost(seq: list[int], slot, edge, first) -> float:
    c = first[seq[0]] + slot[seq[0]][0]
    for i in range(1, len(seq)):
        c += edge[seq[i - 1]][seq[i]] + slot[seq[i]][i]
    return c


def _improve(seq: list[int], slot, edge, first, passes: int = 4) -> list[int]:
    """Local search: reverse segments (2-opt) and move single tracks while the cost drops."""
    best = _cost(seq, slot, edge, first)
    n = len(seq)
    for _ in range(passes):
        improved = False
        for i in range(n - 1):
            for j in range(i + 1, n):
                cand = seq[:i] + seq[i:j + 1][::-1] + seq[j + 1:]
                c = _cost(cand, slot, edge, first)
                if c < best - 1e-9:
                    seq, best, improved = cand, c, True
        for size in (1, 2, 3):  # move a run of 1-3 tracks somewhere else (or-opt)
            for i in range(n - size + 1):
                run, rest = seq[i:i + size], seq[:i] + seq[i + size:]
                for j in range(len(rest) + 1):
                    if j == i:
                        continue
                    cand = rest[:j] + run + rest[j:]
                    c = _cost(cand, slot, edge, first)
                    if c < best - 1e-9:
                        seq, best, improved = cand, c, True
                        break
        if not improved:
            break
    return seq


def describe(order: list[Track], start: Track | None = None) -> dict[str, Any]:
    """Per-step detail + summary for the UI: smoothness of every transition in the plan."""
    steps, prev = [], start
    for t in order:
        steps.append({
            "id": t["id"],
            "energy": round(_energy(t), 3),
            "smooth": rules.transition_smoothness(prev, t) if prev else None,
        })
        prev = t
    scored = [s["smooth"] for s in steps if s["smooth"] is not None]
    return {
        "steps": steps,
        "smooth_pct": round(100 * sum(1 for s in scored if s >= 2) / len(scored)) if scored else None,
    }
