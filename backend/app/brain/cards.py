"""Compact text cards: Jev only sees text, so every track is described in one line."""

from __future__ import annotations

from typing import Any

from app.brain import rules

VOCAL = 0.55


def _section(vocals: list[float], start: int, end: int) -> str:
    seg = vocals[start:end]
    if not seg:
        return "unknown"
    return "vocal-heavy" if sum(seg) / len(seg) > VOCAL else "instrumental"


def track_card(t: dict[str, Any]) -> str:
    name = f"{t.get('artist')} - {t.get('title')}" if t.get("artist") else str(t.get("title"))
    parts = [name, f"{t.get('bpm', 0):.0f} BPM", str(t.get("camelot")), f"energy {t.get('energy', 0):.2f}"]
    vocals = t.get("vocal_bars") or []
    if vocals:
        parts.append(f"{_section(vocals, 0, 16)} intro")
        parts.append(f"{_section(vocals, len(vocals) - 16, len(vocals))} outro")
        v_mean = sum(vocals) / len(vocals)
        v_style = "vocal-led (good for stem_mashup/word_play)" if v_mean > 0.48 else "instrumental-groove (good for blends/double_drop)" if v_mean < 0.25 else "balanced vocals"
        parts.append(v_style)
    energy_bars = t.get("energy_bars") or []
    if len(energy_bars) >= 16:
        drop_bar = rules.first_drop_bar(t)
        pk_start, pk_end = rules.peak_section(t)
        parts.append(f"drop bar {drop_bar}, peak bars {pk_start}-{pk_end}")
    if t.get("genre"):
        parts.append(str(t["genre"]))
    return " | ".join(parts)


def vocal_profile(t: dict[str, Any], start_bar: int, bars: int) -> str:
    v = (t.get("vocal_bars") or [])[start_bar:start_bar + bars]
    if not v:
        return "no vocal data"
    return ", ".join(f"bar {start_bar + i}: {x:.2f}" for i, x in enumerate(v))
