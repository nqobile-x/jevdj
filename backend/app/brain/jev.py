"""The only module that talks to Jev (TypeSafe System One).

Every question asked, and every fallback taken, is logged to SQLite and pushed to the
/events WebSocket so the AI panel can show it. Below the confidence threshold, or when
Jev is unreachable, the rule-based logic in rules.py makes the call and is marked as such.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from app.brain import rules
from app.brain.cards import track_card, vocal_profile

log = logging.getLogger("jevdj.jev")

SMOOTHNESS_LEVELS = ["clashing", "noticeable but okay", "smooth", "seamless"]
STYLE_CRITERIA = {
    "long_blend": "32-bar EQ swap with the bass swapped at bar 16. Best for same genre and close BPM.",
    "quick_cut": "4-bar cut on a downbeat. Best for a big energy change or clashing keys.",
    "filter_fade": "High-pass sweep out over 16 bars. Best for a genre change.",
    "echo_out": "Echo/delay tail on the outgoing track, then drop the new one on its drop. Best at the end of a peak.",
    "wash_out": "Reverb wash with a high-pass on the outgoing, new track rises out of the wash. Best for bringing energy down.",
    "brake": "Vinyl brake (turntable stop) on the outgoing, then slam the new track in on its drop. Best for a genre or tempo switch, very hip hop.",
    "loop_roll": "Loop roll (1/2, 1/4, 1/8 beat stutter) with a noise riser, then drop the new track on its drop. Best for raising energy.",
    "chop": "Chop: trade bars back and forth (2, 2, 1, 1, 1, 1) between the tracks, landing on the new drop. Human, hip hop/amapiano style; works even when keys clash.",
    "tease": "Tease: short stabs of the new track's drop over the outgoing hook, building up, then the full switch on the drop. Best when energy is rising.",
    "rewind": "Rewind: quick spin-back on the outgoing, then slam the new track in on its drop. Big crowd moment, use sparingly.",
}
PHASE_CRITERIA = [
    "warm-up: relaxed, groovy, lower energy",
    "build: steadily rising energy and tension",
    "peak: highest energy, biggest tracks",
    "cool-down: bring energy down, smoother and deeper",
]
VOCAL_SHIFT = 0.6


class JevBrain:
    def __init__(
        self,
        api_key: str,
        model: str,
        threshold: float,
        log_decision: Callable[[dict[str, Any]], dict[str, Any]],
        client: Any = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.threshold = threshold
        self._log = log_decision
        self._client = client

    # ------------------------------------------------------------ plumbing

    @property
    def online(self) -> bool:
        return self._client is not None or bool(self.api_key)

    def _get_client(self) -> Any:
        if self._client is None:
            from typesafe_sdk import TypeSafeClient

            self._client = TypeSafeClient(api_key=self.api_key, model=self.model, timeout=12.0)
        return self._client

    async def _ask(self, state: Any, questions: dict[str, Any]) -> Any:
        if not self.online:
            raise RuntimeError("no TYPESAFE_API_KEY configured")
        client = self._get_client()
        return await asyncio.to_thread(client.system_one, state=state, questions=questions)

    def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            self._client.close()

    # ------------------------------------------------------------ next track

    async def choose_next(
        self,
        current: dict | None,
        cands: list[dict],
        context: dict[str, Any],
        set_id: int | None,
    ) -> dict[str, Any]:
        """Return {track, source, confidence, reason, decision_ids}."""
        phase = context.get("phase", "build")
        if not cands:
            raise ValueError("no candidate tracks")
        by_label = {f"t{t['id']}": t for t in cands}
        if context.get("switch") and current:
            question = ("The listener tapped 'switch it up'. Which track takes the set somewhere fresh "
                        "(new genre or energy) while still mixing cleanly?")
        elif current:
            question = "Which track should play next to keep the set flowing?"
        else:
            question = "Which track should open the set?"
        state = {
            "current_track": track_card(current) if current else "nothing playing yet (this is the opener)",
            "set_phase": phase,
            "user_vibe": context.get("vibe"),
            "last_tracks": context.get("last_cards", []),
            "recent_user_overrides": context.get("overrides", []),
            "dj_taste": "amapiano, afro house, deep house, hip hop; smooth harmonic mixing",
            "listener_taste": context.get("taste_text") or "no listening history yet",
        }
        ids: list[int] = []
        fallback_reason = None
        try:
            resp = await self._ask(state, {
                "next": {"type": "choice", "instructions": question,
                         "criteria": {label: track_card(t) for label, t in by_label.items()}},
            })
            ans = resp.choices["next"]
            probs = {k: round(v, 4) for k, v in sorted(ans.probabilities.items(), key=lambda kv: -kv[1])}
            d = self._log({
                "set_id": set_id, "kind": "next_track", "source": "jev", "question": question,
                "options": [track_card(t) for t in cands], "answer": _title(by_label.get(ans.choice)),
                "confidence": round(ans.confidence, 4),
                "probabilities": {_title(by_label.get(k)): v for k, v in list(probs.items())[:5]},
                "reason": rules.reason_line(current, by_label[ans.choice]) if ans.choice in by_label else None,
            })
            ids.append(d["id"])
            if ans.confidence >= self.threshold and ans.choice in by_label:
                chosen = by_label[ans.choice]
                if current is not None and len(cands) > 1:
                    top3 = [by_label[k] for k in list(probs)[:3] if k in by_label]
                    chosen, sid = await self._rank_smoothness(current, top3, probs, set_id)
                    if sid:
                        ids.append(sid)
                return {"track": chosen, "source": "jev", "confidence": round(ans.confidence, 4),
                        "reason": rules.reason_line(current, chosen), "decision_ids": ids}
            fallback_reason = f"Jev confidence {ans.confidence:.2f} < {self.threshold}"
        except Exception as exc:  # network, auth, schema: the music must not stop
            log.warning("Jev next-track failed: %s", exc)
            fallback_reason = f"Jev unavailable: {_short(exc)}"

        chosen = rules.pick_next(current, cands, phase, context.get("taste"))
        d = self._log({
            "set_id": set_id, "kind": "next_track", "source": "rules", "question": question,
            "options": [track_card(t) for t in cands[:5]], "answer": _title(chosen),
            "confidence": None, "reason": f"{rules.reason_line(current, chosen)} ({fallback_reason})",
        })
        ids.append(d["id"])
        return {"track": chosen, "source": "rules", "confidence": None,
                "reason": rules.reason_line(current, chosen), "fallback": fallback_reason, "decision_ids": ids}

    async def _rank_smoothness(
        self, current: dict, top: list[dict], probs: dict[str, float], set_id: int | None
    ) -> tuple[dict, int | None]:
        """Jev Score on the top 3: how smooth will A -> B be? Highest expected score wins."""
        question = "How smooth will the transition from A to B be?"
        questions = {
            f"t{t['id']}": {
                "type": "score",
                "instructions": {"question": question, "A (outgoing)": track_card(current), "B (incoming)": track_card(t)},
                "criteria": SMOOTHNESS_LEVELS,
            }
            for t in top
        }
        try:
            resp = await self._ask({"note": "DJ transition quality between two tracks"}, questions)
        except Exception as exc:
            log.warning("Jev smoothness failed: %s", exc)
            return top[0], None
        scored = []
        for t in top:
            a = resp.scores.get(f"t{t['id']}")
            if a is not None:
                scored.append((a.score, probs.get(f"t{t['id']}", 0), t, a.confidence))
        if not scored:
            return top[0], None
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        best = scored[0]
        d = self._log({
            "set_id": set_id, "kind": "smoothness", "source": "jev", "question": question,
            "options": SMOOTHNESS_LEVELS, "answer": _title(best[2]), "confidence": round(best[3], 4),
            "probabilities": {_title(t): round(s, 3) for s, _p, t, _c in scored},
            "reason": f"expected smoothness {best[0]:.2f}/3 ({SMOOTHNESS_LEVELS[round(best[0])]})",
        })
        return best[2], d["id"]

    # ------------------------------------------------------------ transition

    async def choose_transition(
        self,
        a: dict,
        b: dict,
        phase: str,
        set_id: int | None,
        start_s: float | None = None,
        mix_style: str = "club",
        recent_styles: list[str] | None = None,
        flair: str = "smooth",
        learned: dict[str, dict[str, float]] | None = None,
        rng: Any = None,
    ) -> dict[str, Any]:
        bonus = {s: v["bonus"] for s, v in (learned or {}).items()}
        rule_style = rules.pick_style(a, b, phase, recent_styles, mix_style, flair, bonus, rng)
        default = rules.plan_transition(a, b, rule_style, start_s=start_s, mix_style=mix_style, flair=flair, rng=rng)
        # Jev chooses only among moves that suit this pair at this flair level (guards stay in rules).
        allowed = {s: STYLE_CRITERIA[s] for s in rules.style_options(a, b, phase, mix_style, flair)}
        feedback = ", ".join(f"{s}: {int(v['up'])} liked / {int(v['down'])} disliked" for s, v in (learned or {}).items()) or "none yet"
        state = {
            "outgoing": track_card(a),
            "incoming": track_card(b),
            "set_phase": phase,
            "tempo_change_needed": f"{(default.rate - 1) * 100:+.1f}%",
            "key_compatibility": rules.key_relation(a, b),
            "planned_mix_point": f"bar {default.out_bar} of the outgoing track",
            "outgoing_vocal_density_at_mix_point (0-1)": vocal_profile(a, default.out_bar, 8),
            "last_transition_styles": recent_styles or [],
            "flair": flair,
            "listener_ratings_of_moves": feedback,
            "creative_brief": ("Mix like a creative club DJ: vary the moves, avoid repeating the last style, "
                               f"keep the energy right for the {phase} phase. Mix style: {mix_style}."),
        }
        style_q = "Which transition style fits this mix best?"
        vocal_q = "Is the outgoing track in a vocal section at the planned mix point?"
        ids: list[int] = []
        style, source, conf, shift, fallback = rule_style, "rules", None, None, None
        try:
            resp = await self._ask(state, {
                "style": {"type": "choice", "instructions": style_q, "criteria": allowed},
                "vocal": {"type": "noul", "instructions": vocal_q,
                          "criteria": {"true": "vocals are active at the mix point", "false": "instrumental at the mix point"}},
            })
            sa, va = resp.choices["style"], resp.nouls["vocal"]
            d1 = self._log({
                "set_id": set_id, "kind": "transition_style", "source": "jev", "question": style_q,
                "options": list(allowed), "answer": sa.choice, "confidence": round(sa.confidence, 4),
                "probabilities": {k: round(v, 4) for k, v in sa.probabilities.items()},
                "reason": f"{track_card(a).split(' | ')[0]} -> {track_card(b).split(' | ')[0]}",
            })
            shift = va.noul > VOCAL_SHIFT
            d2 = self._log({
                "set_id": set_id, "kind": "vocal_check", "source": "jev", "question": vocal_q,
                "options": ["yes", "no"], "answer": "yes" if shift else "no", "confidence": round(va.noul, 4),
                "probabilities": {"yes": round(va.noul, 4), "no": round(1 - va.noul, 4)},
                "reason": "shift mix point to next instrumental phrase" if shift else "keep mix point",
            })
            ids += [d1["id"], d2["id"]]
            if sa.confidence >= self.threshold and sa.choice in allowed:
                style, source, conf = sa.choice, "jev", round(sa.confidence, 4)
            else:
                fallback = f"Jev confidence {sa.confidence:.2f} < {self.threshold}"
        except Exception as exc:
            log.warning("Jev transition failed: %s", exc)
            fallback = f"Jev unavailable: {_short(exc)}"

        plan = rules.plan_transition(a, b, style, shift_vocals=shift, start_s=start_s, mix_style=mix_style,
                                     flair=flair, rng=rng)
        if source == "rules":
            d = self._log({
                "set_id": set_id, "kind": "transition_style", "source": "rules", "question": style_q,
                "options": list(allowed), "answer": style, "confidence": None,
                "reason": f"rule pick ({fallback})",
            })
            ids.append(d["id"])
        return {**plan.to_dict(), "source": source, "confidence": conf, "fallback": fallback, "decision_ids": ids}

    # ------------------------------------------------------------ set phase

    async def choose_phase(self, current_phase: str, played: int, last_cards: list[str], set_id: int | None) -> dict[str, Any]:
        question = "Which phase should the set move to next?"
        try:
            resp = await self._ask(
                {"current_phase": current_phase, "tracks_played": played, "last_tracks": last_cards},
                {"phase": {"type": "score", "instructions": question, "criteria": PHASE_CRITERIA}},
            )
            ans = resp.scores["phase"]
            phase = rules.PHASES[int(round(min(max(ans.score, 0), 3)))]
            src = "jev" if ans.confidence >= self.threshold else "rules"
            if src == "rules":
                phase = rules.next_phase(current_phase, played)
            d = self._log({
                "set_id": set_id, "kind": "set_phase", "source": src, "question": question,
                "options": rules.PHASES, "answer": phase, "confidence": round(ans.confidence, 4),
                "probabilities": {rules.PHASES[k]: round(v, 4) for k, v in ans.probabilities.items() if k < 4},
                "reason": f"expected {ans.score:.2f} on warm-up(0)..cool-down(3)",
            })
            return {"phase": phase, "source": src, "decision_ids": [d["id"]]}
        except Exception as exc:
            phase = rules.next_phase(current_phase, played)
            d = self._log({
                "set_id": set_id, "kind": "set_phase", "source": "rules", "question": question,
                "options": rules.PHASES, "answer": phase, "reason": f"set arc rule (Jev unavailable: {_short(exc)})",
            })
            return {"phase": phase, "source": "rules", "decision_ids": [d["id"]]}


def _title(t: dict | None) -> str | None:
    if not t:
        return None
    return f"{t.get('artist')} - {t.get('title')}" if t.get("artist") else t.get("title")


def _short(exc: Exception) -> str:
    msg = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return msg[:120]
