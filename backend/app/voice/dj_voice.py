"""Optional DJ voice: Groq writes one short line, Kokoro speaks it.

Both are optional. Without Groq a template line is used; without Kokoro the frontend
falls back to the browser's speechSynthesis.
"""

from __future__ import annotations

import asyncio
import os
import time
import logging
import random
import re
import uuid
from pathlib import Path
from typing import Any

from app.voice import local_tts

log = logging.getLogger("jevdj.voice")

SYSTEM = (
    "You are the hype DJ on a Johannesburg radio mix: amapiano, afro house, deep house, hip hop. "
    "Write ONE spoken line under 20 words introducing the next track. Warm, confident, a touch of "
    "Mzansi slang is fine. No hashtags, no emojis, no quotes, no em dashes."
)

TEMPLATES = [
    "Next up: {title}. We're {phase_line}.",
    "Keep it locked. {title} coming in, {phase_line}.",
    "Here's {title}. Johannesburg, we're {phase_line}.",
]
INTRO_TEMPLATES = [
    "Hey Nqobile, it's Jev. Let's ease in with {title}.",
    "Sawubona, Jev on the decks. Starting you off with {title}.",
]
SWITCH_TEMPLATES = [
    "Switching it up. Let's go somewhere new with {title}.",
    "New direction, same groove. Here's {title}.",
]
MODE_PROMPTS = {
    "intro": "This is the very first track of the set: greet the listener, Nqobile, by name.",
    "switch": "The listener just asked to switch it up: acknowledge the change of direction.",
    "next": "",
}
PHASE_LINES = {
    "warm-up": "easing into the groove",
    "build": "building into the peak",
    "peak": "right at the peak",
    "cool-down": "bringing it down smooth",
}


class DJVoice:
    def __init__(self, groq_key: str, groq_model: str, out_dir: Path,
                 tts_model: str = "canopylabs/orpheus-v1-english", tts_voice: str = "troy") -> None:
        self.groq_key = groq_key
        self.groq_model = groq_model
        self.tts_model = tts_model
        self.tts_voice = tts_voice
        self.out_dir = out_dir
        self._groq = None
        self._kokoro = None
        self._kokoro_failed = False
        self._groq_tts_failed = False
        self._groq_tts_until = 0.0  # rate-limited: don't ask Groq to speak again before this time
        self.local_voice = os.getenv("LOCAL_TTS_VOICE", "David")

    @property
    def status(self) -> dict[str, bool]:
        return {"groq": bool(self.groq_key), "groq_tts": bool(self.groq_key and self.tts_model) and not self._groq_tts_failed
                and time.time() >= self._groq_tts_until, "kokoro": self._kokoro_available(), "local": local_tts.available()}

    def _client(self):
        if self._groq is None:
            from groq import Groq

            self._groq = Groq(api_key=self.groq_key, timeout=10.0)
        return self._groq

    def _kokoro_available(self) -> bool:
        if self._kokoro_failed:
            return False
        try:
            import importlib.util

            return importlib.util.find_spec("kokoro") is not None
        except Exception:
            return False

    # ------------------------------------------------------------ text

    def _template(self, ctx: dict[str, Any]) -> str:
        pool = {"intro": INTRO_TEMPLATES, "switch": SWITCH_TEMPLATES}.get(ctx.get("mode", "next"), TEMPLATES)
        return random.choice(pool).format(
            title=ctx.get("next_title", "this one"),
            phase_line=PHASE_LINES.get(ctx.get("phase", "build"), "keeping it moving"),
        )

    def _groq_line(self, ctx: dict[str, Any]) -> str:
        prompt = (
            f"Next track: {ctx.get('next_title')} by {ctx.get('next_artist') or 'unknown artist'} "
            f"({ctx.get('next_genre') or 'dance'}, {ctx.get('next_bpm', 0):.0f} BPM). "
            f"Previous track: {ctx.get('current_title') or 'none'}. Set phase: {ctx.get('phase')}. "
            f"{MODE_PROMPTS.get(ctx.get('mode', 'next'), '')}"
        )
        extra: dict[str, Any] = {}
        if "gpt-oss" in self.groq_model or "qwen3" in self.groq_model:
            extra["reasoning_effort"] = "low"  # reasoning models: keep it fast
        resp = self._client().chat.completions.create(
            model=self.groq_model,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            max_completion_tokens=400,
            temperature=0.9,
            **extra,
        )
        return resp.choices[0].message.content or ""

    @staticmethod
    def clean(text: str) -> str:
        text = text.strip().replace("—", " - ").replace("–", "-")
        text = text.translate(str.maketrans({"“": "", "”": "", "‘": "'", "’": "'", '"': ""}))
        text = re.sub(r"\s+", " ", text)
        words = text.split(" ")
        if len(words) > 20:
            text = " ".join(words[:20]).rstrip(",;:") + "."
        return text

    # ------------------------------------------------------------ audio

    def _speak(self, text: str) -> str | None:
        """Best voice available: Groq (natural) -> Kokoro (local neural, if installed) -> Windows voice (always)."""
        return self._speak_groq(text) or self._speak_kokoro(text) or self._speak_local(text)

    def _speak_local(self, text: str) -> str | None:
        name = f"{uuid.uuid4().hex}.wav"
        return name if local_tts.speak_to_file(text, self.out_dir / name, self.local_voice) else None

    def _speak_groq(self, text: str) -> str | None:
        """Groq-hosted Orpheus TTS: no local model needed."""
        if not self.groq_key or not self.tts_model or self._groq_tts_failed or time.time() < self._groq_tts_until:
            return None
        try:
            audio = self._client().audio.speech.create(
                model=self.tts_model, voice=self.tts_voice, input=text, response_format="wav",
            )
            self.out_dir.mkdir(parents=True, exist_ok=True)
            name = f"{uuid.uuid4().hex}.wav"
            (self.out_dir / name).write_bytes(audio.read())
            return name
        except Exception as exc:
            log.warning("Groq TTS failed, falling back: %s", str(exc)[:160])
            msg = str(exc)
            if "429" in msg or "rate limit" in msg.lower():
                # Free tier used up: wait until Groq says to try again (default 30 min).
                m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", msg)
                wait = 1800.0
                if m and any(m.groups()):
                    h, mi, se = (float(g) if g else 0.0 for g in m.groups())
                    wait = h * 3600 + mi * 60 + se + 5
                self._groq_tts_until = time.time() + wait
            elif "model" in msg.lower() or "401" in msg:
                self._groq_tts_failed = True
            return None

    def _speak_kokoro(self, text: str) -> str | None:
        if not self._kokoro_available():
            return None
        try:
            import numpy as np
            import soundfile as sf

            if self._kokoro is None:
                from kokoro import KPipeline

                self._kokoro = KPipeline(lang_code="b")  # British English is closest to SA English
            chunks = [audio for _gs, _ps, audio in self._kokoro(text, voice="bm_george")]
            if not chunks:
                return None
            self.out_dir.mkdir(parents=True, exist_ok=True)
            name = f"{uuid.uuid4().hex}.wav"
            sf.write(self.out_dir / name, np.concatenate([np.asarray(c) for c in chunks]), 24000)
            return name
        except Exception as exc:
            log.warning("Kokoro failed, disabling: %s", exc)
            self._kokoro_failed = True
            return None

    async def line(self, ctx: dict[str, Any]) -> dict[str, Any]:
        source = "template"
        text = ""
        if self.groq_key:
            try:
                text = self.clean(await asyncio.to_thread(self._groq_line, ctx))
                source = "groq"
            except Exception as exc:
                log.warning("Groq failed: %s", exc)
        if not text:
            text = self._template(ctx)
        audio = await asyncio.to_thread(self._speak, text)
        return {"text": text, "source": source, "audio_url": f"/voice/audio/{audio}" if audio else None}
