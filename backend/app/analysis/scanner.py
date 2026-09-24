"""Walk MUSIC_DIR, analyse new or changed files in parallel, cache results in SQLite."""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from app.config import AUDIO_EXTENSIONS
from app.db import Database


def find_audio(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS)


def _analyse(path: str) -> tuple[str, dict | None, str | None]:
    try:
        from app.analysis.analyser import analyse_file

        return path, analyse_file(path).to_dict(), None
    except Exception as exc:  # one bad file must not stop the scan
        return path, None, f"{type(exc).__name__}: {exc}"


def scan_library(
    db: Database,
    root: Path,
    progress: Callable[[dict], None] | None = None,
    force: bool = False,
    workers: int | None = None,
) -> dict:
    files = find_audio(root)
    present = {str(p) for p in files}
    removed = db.remove_missing(present)
    cached = db.cached_mtimes()
    todo = [p for p in files if force or cached.get(str(p)) != p.stat().st_mtime]
    total = len(todo)
    emit = progress or (lambda _e: None)
    emit({"type": "scan_started", "total": total, "cached": len(files) - total, "root": str(root)})

    ok = failed = 0

    def store(done: int, p: Path, result: tuple[str, dict | None, str | None]) -> None:
        nonlocal ok, failed
        path, analysis, error = result
        mtime = p.stat().st_mtime if p.exists() else 0.0
        if analysis:
            db.upsert_track(analysis, mtime)
            ok += 1
        else:
            db.mark_error(path, mtime, error or "unknown error")
            failed += 1
        emit({"type": "scan_progress", "done": done, "total": total, "current": p.name, "error": error})

    workers = workers or max(1, min(4, (os.cpu_count() or 2) - 1))
    if len(todo) <= 2 or workers == 1:
        # A couple of new files (typical for auto-detect/upload): no process pool start-up cost.
        for done, p in enumerate(todo, start=1):
            store(done, p, _analyse(str(p)))
    elif todo:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_analyse, str(p)): p for p in todo}
            for done, fut in enumerate(as_completed(futures), start=1):
                store(done, futures[fut], fut.result())

    summary = {"type": "scan_finished", "analysed": ok, "failed": failed, "removed": removed, "total_files": len(files)}
    emit(summary)
    return summary
