"""Offline fallback voice using the operating system's text-to-speech (Windows SAPI via pyttsx3).

Free, unlimited and works without internet; used when the Groq voice is unavailable or rate-limited.
Runs in a short-lived subprocess: SAPI is a COM component and is happiest on its own thread/process.

    python -m app.voice.local_tts "text" out.wav [voice name fragment]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("pyttsx3") is not None
    except Exception:
        return False


def speak_to_file(text: str, out: Path, voice: str = "David", timeout: float = 20) -> bool:
    """Render `text` to a WAV file. True on success."""
    if not available():
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [sys.executable, "-m", "app.voice.local_tts", text, str(out), voice],
            cwd=BACKEND_DIR, timeout=timeout, check=True, capture_output=True,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return out.exists() and out.stat().st_size > 1000


def _main(argv: list[str]) -> int:
    import pyttsx3

    text, out = argv[0], argv[1]
    want = (argv[2] if len(argv) > 2 else "David").lower()
    engine = pyttsx3.init()
    for v in engine.getProperty("voices"):
        if want in v.name.lower():
            engine.setProperty("voice", v.id)
            break
    engine.setProperty("rate", 185)  # a touch quicker than default: sounds more like a DJ
    engine.save_to_file(text, out)
    engine.runAndWait()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
