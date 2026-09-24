"""Live smoke test: scan dev/demo_music into data/dev and ask the real Jev each question type once.

python dev/jev_smoke.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("MUSIC_DIR", str(HERE / "demo_music"))
os.environ.setdefault("JEVDJ_DATA_DIR", str(HERE.parent / "data" / "dev"))


async def run() -> None:
    from app.analysis.scanner import scan_library
    from app.brain import rules
    from app.brain.jev import JevBrain
    from app.config import load_settings
    from app.db import Database

    s = load_settings()
    db = Database(s.db_path)
    print(scan_library(db, s.music_dir))
    lib = db.get_tracks_full()
    logs: list[dict] = []

    def log(d):
        logs.append(d)
        return {**d, "id": len(logs)}

    brain = JevBrain(s.typesafe_api_key, s.typesafe_model, s.jev_threshold, log)
    cur = lib[0]
    nxt = await brain.choose_next(cur, rules.candidates(cur, lib, [], phase="build"), {"phase": "build", "vibe": "build"}, None)
    print("NEXT ", nxt["source"], nxt["confidence"], nxt["track"]["title"], nxt.get("fallback") or "")
    print("TRANS", json.dumps(await brain.choose_transition(cur, nxt["track"], "build", None)))
    print("PHASE", await brain.choose_phase("build", 5, [], None))
    for d in logs:
        print(" -", d["kind"], d["source"], d.get("answer"), d.get("confidence"), d.get("reason"))


if __name__ == "__main__":
    asyncio.run(run())
