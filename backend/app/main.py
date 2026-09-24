"""JevDJ backend: library analysis, the Jev brain, DJ voice and set history.

Run: uvicorn app.main:app --reload   (from backend/)
"""

from __future__ import annotations

import asyncio
import json
import random
import logging
import os
import re
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.analysis.audio_io import NATIVE, NotAudioError, check_audio, sniff, to_flac
from app.analysis.watcher import LibraryWatcher, snapshot
from app.sources import audius
from app.analysis.scanner import find_audio, scan_library
from app.brain import rules
from app.brain.cards import track_card
from app.brain.jev import JevBrain
from app.config import AUDIO_EXTENSIONS, Settings, settings as default_settings
from app.db import Database
from app.events import bus
from app.voice.dj_voice import DJVoice

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("jevdj")

# Anything the browser cannot decode reliably (AIFF, AAC/Opus in MP4/WebM) is served as FLAC.
MEDIA_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg", ".m4a": "audio/mp4"}


class SetState:
    def __init__(self) -> None:
        self.set_id: int | None = None
        self.vibe = "build"
        self.phase = "warm-up"
        self.played = 0
        self.phase_checked_at = 0
        self.mix_style = "club"
        self.flair = "creative"


# ---------------------------------------------------------------- request bodies

class ScanBody(BaseModel):
    force: bool = False


class SetBody(BaseModel):
    vibe: str = "auto"


class NextBody(BaseModel):
    current_id: int | None = None
    history: list[int] = Field(default_factory=list)
    vibe: str | None = None
    exclude: list[int] = Field(default_factory=list)
    switch: bool = False  # "switch it up": take the set in a new direction


class TransitionBody(BaseModel):
    from_id: int
    to_id: int
    vibe: str | None = None
    start_s: float | None = None  # where the outgoing track started playing


class MixStyleBody(BaseModel):
    mix_style: str


class FlairBody(BaseModel):
    flair: str


class AlternativesBody(BaseModel):
    from_id: int
    to_id: int
    start_s: float | None = None
    exclude: list[str] = Field(default_factory=list)
    limit: int = Field(default=2, ge=1, le=4)


class PrelistenLogBody(BaseModel):
    score: float
    style: str
    issues: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)
    beat_error_ms: float | None = None
    switched_from: str | None = None
    render_ms: int | None = None


class FeedbackBody(BaseModel):
    kind: str = "transition"  # transition | track
    value: float = Field(ge=-1, le=1)  # +1 fire, -1 nah
    style: str | None = None
    track_id: int | None = None
    note: str | None = None


class OverrideBody(BaseModel):
    action: str  # veto | pick_another | mix_now | manual_load | manual_next
    track_id: int | None = None
    note: str | None = None


class PlayedBody(BaseModel):
    track_id: int
    transition: str | None = None
    source: str | None = None


class VoiceBody(BaseModel):
    current_id: int | None = None
    next_id: int
    mode: str = "next"  # intro | next | switch


class VibeBody(BaseModel):
    vibe: str


class SourceAddBody(BaseModel):
    id: str


class RadioBody(BaseModel):
    query: str | None = None  # None = trending
    genre: str | None = None


class GridBody(BaseModel):
    shift_s: float = Field(ge=-2.0, le=2.0)
    source: str = "user"  # user | beatlock


# ---------------------------------------------------------------- app factory

def create_app(settings: Settings = default_settings, jev_client: Any = None) -> FastAPI:
    db = Database(settings.db_path)
    state = SetState()

    def log_decision(d: dict[str, Any]) -> dict[str, Any]:
        saved = db.log_decision(d)
        bus.publish({"type": "decision", "decision": saved})
        return saved

    brain = JevBrain(settings.typesafe_api_key, settings.typesafe_model, settings.jev_threshold, log_decision, client=jev_client)
    voice = DJVoice(settings.groq_api_key, settings.groq_model, settings.voice_dir,
                    settings.groq_tts_model, settings.groq_tts_voice)
    scan_lock = threading.Lock()
    rng = random.Random()
    transcode_dir = settings.db_path.parent / "transcode"

    def start_scan(force: bool = False, reason: str = "manual") -> bool:
        """Run a scan in the background. False if one is already running."""
        if not scan_lock.acquire(blocking=False):
            return False

        def run() -> None:
            try:
                bus.publish({"type": "scan_reason", "reason": reason})
                scan_library(db, settings.music_dir, progress=bus.publish, force=force)
            except Exception as exc:
                log.exception("scan failed")
                bus.publish({"type": "scan_finished", "error": str(exc)})
            finally:
                scan_lock.release()

        threading.Thread(target=run, daemon=True, name="scan").start()
        return True

    def on_folder_change(old: dict, new: dict) -> bool:
        added = [p for p in new if p not in old]
        removed = [p for p in old if p not in new]
        changed = [p for p in new if p in old and new[p] != old[p]]
        if old or added:  # the first pass after startup is silent unless files are new to the database
            known = db.cached_mtimes()
            added = [p for p in added if p not in known]
            if not (added or removed or changed or any(p not in known for p in new)):
                return True
        bus.publish({"type": "library_change", "added": [Path(p).name for p in added][:20],
                     "removed": len(removed), "changed": len(changed)})
        return start_scan(False, "auto")

    watcher = LibraryWatcher(settings.music_dir, on_folder_change, settings.watch_interval) if settings.watch_interval > 0 else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        bus.bind(asyncio.get_running_loop())
        state.set_id = db.latest_set()
        if watcher:
            watcher.start()
        yield
        if watcher:
            watcher.stop()
        brain.close()
        db.close()

    app = FastAPI(title="JevDJ", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.db = db
    app.state.set = state

    def ensure_set() -> int:
        if state.set_id is None:
            state.set_id = db.new_set(state.vibe)
        return state.set_id

    def get_or_404(track_id: int) -> dict[str, Any]:
        t = db.get_track(track_id)
        if not t or t.get("error"):
            raise HTTPException(404, f"track {track_id} not found")
        return t

    # ------------------------------------------------------------ status

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "jev_online": brain.online,
            "voice": voice.status,
            "music_dir": str(settings.music_dir),
            "music_dir_exists": settings.music_dir.exists(),
            "tracks": len(db.list_tracks()),
            "audius_cache_mb": round(audius.cache_size(settings.db_path.parent / "audius") / 1e6, 1),
            "set_id": state.set_id,
            "phase": state.phase,
            "vibe": state.vibe,
            "mix_style": state.mix_style,
            "flair": state.flair,
            "threshold": settings.jev_threshold,
        }

    # ------------------------------------------------------------ library

    @app.post("/library/scan")
    async def library_scan(body: ScanBody | None = None) -> dict[str, Any]:
        if not start_scan(bool(body and body.force), "manual"):
            raise HTTPException(409, "scan already running")
        if watcher:
            watcher.mark_handled(snapshot(settings.music_dir))
        return {"started": True, "music_dir": str(settings.music_dir), "files": len(find_audio(settings.music_dir))}

    @app.post("/library/upload")
    async def library_upload(files: list[UploadFile] = File(...)) -> dict[str, Any]:
        """Save dropped/picked files into MUSIC_DIR, then analyse them."""
        settings.music_dir.mkdir(parents=True, exist_ok=True)
        saved, rejected = [], []
        for f in files:
            name = _safe_name(f.filename or "track")
            if Path(name).suffix.lower() not in AUDIO_EXTENSIONS:
                rejected.append({"file": f.filename, "reason": "not an audio file type"})
                continue
            dst = _unique(settings.music_dir / name)
            tmp = dst.with_name(dst.name + ".part")
            with tmp.open("wb") as out:
                while chunk := await f.read(1 << 20):
                    out.write(chunk)
            try:
                check_audio(tmp)
            except NotAudioError as exc:
                tmp.unlink(missing_ok=True)
                rejected.append({"file": f.filename, "reason": str(exc)})
                continue
            tmp.replace(dst)
            saved.append(dst.name)
        if saved:
            start_scan(False, "upload")
            if watcher:
                watcher.mark_handled(snapshot(settings.music_dir))
        return {"saved": saved, "rejected": rejected, "music_dir": str(settings.music_dir)}

    @app.post("/library/open-folder")
    def library_open_folder() -> dict[str, Any]:
        """Open MUSIC_DIR in Explorer/Finder (the app runs locally)."""
        settings.music_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(settings.music_dir)  # noqa: S606 - local desktop app
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(settings.music_dir)])
        return {"opened": str(settings.music_dir)}

    # ------------------------------------------------------------ online source: Audius

    audius_dir = settings.db_path.parent / "audius"
    audius_busy: set[str] = set()

    def _mark_added(items: list[dict]) -> list[dict]:
        """Flag tracks already in the library and use Jev's own analysis (energy, BPM, key) for them."""
        have = db.source_ids("audius")
        out = []
        for t in items:
            row = {**t, "added": t["id"] in have, "track_id": have.get(t["id"]), "adding": t["id"] in audius_busy,
                   "energy": None}
            if row["track_id"]:
                mine = db.get_track(row["track_id"], full=False) or {}
                row.update(energy=mine.get("energy"), bpm=mine.get("bpm") or t.get("bpm"),
                           camelot=mine.get("camelot") or t.get("camelot"))
            out.append(row)
        return out

    @app.get("/sources/audius/search")
    async def audius_search(q: str = Query(min_length=1, max_length=100), limit: int = 30, min_seconds: int = 90):
        try:
            return _mark_added(await asyncio.to_thread(audius.search, q, limit, min_seconds))
        except Exception as exc:
            raise HTTPException(502, f"Audius search failed: {exc}") from exc

    @app.get("/sources/audius/trending")
    async def audius_trending(genre: str | None = None, time: str = "week", limit: int = 30):
        try:
            return _mark_added(await asyncio.to_thread(audius.trending, genre, time, limit))
        except Exception as exc:
            raise HTTPException(502, f"Audius trending failed: {exc}") from exc

    @app.post("/sources/audius/add")
    def audius_add(body: SourceAddBody) -> dict[str, Any]:
        """Fetch an Audius track into the app cache, analyse it, add it to the library (background)."""
        try:
            tid = audius.safe_id(body.id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if tid in db.source_ids("audius"):
            return {"queued": False, "already": True}
        if tid in audius_busy:
            return {"queued": False, "busy": True}
        audius_busy.add(tid)
        threading.Thread(target=add_audius, args=(tid,), daemon=True, name=f"audius-{tid}").start()
        return {"queued": True}

    def add_audius(tid: str) -> int | None:
        """Stream, analyse and register one Audius track (blocking). Returns the library id."""
        audius_busy.add(tid)
        emit = lambda stage, **kw: bus.publish({"type": "source_progress", "source": "audius", "id": tid, "stage": stage, **kw})
        try:
            meta = audius.track(tid)
            emit("downloading", title=meta["title"])
            path = audius.fetch(tid, audius_dir)
            emit("analysing", title=meta["title"])
            from app.analysis.analyser import analyse_file

            try:
                a = analyse_file(path).to_dict()
            finally:
                path.unlink(missing_ok=True)  # streaming only: keep the analysis, not the audio
            # Audius metadata is cleaner than file tags: credit the uploader.
            a.update(title=meta["title"], artist=meta["artist"], genre=meta["genre"] or a["genre"])
            a.update(source="audius", source_id=tid, permalink=meta["permalink"], artwork=meta["artwork"])
            a["path"] = f"audius:{tid}"  # no file on disk
            track_id = db.upsert_track(a, 0.0)
            emit("done", title=meta["title"], track_id=track_id)
            bus.publish({"type": "scan_finished", "analysed": 1, "failed": 0, "quiet": True})
            return track_id
        except Exception as exc:
            log.warning("Audius add %s failed: %s", tid, exc)
            emit("error", error=str(exc)[:200])
            return None
        finally:
            audius_busy.discard(tid)

    # ------------------------------------------------------------ Audius station (auto mix from Audius)

    radio: dict[str, Any] = {"active": False, "query": None, "genre": None, "ids": [], "failed": set()}
    radio_lock = threading.Lock()
    RADIO_AHEAD = 3  # keep this many analysed, unplayed station tracks ready

    def radio_pool() -> set[int]:
        have = db.source_ids("audius")
        return {have[sid] for sid in radio["ids"] if sid in have}

    def radio_status() -> dict[str, Any]:
        recent = set(db.recent_track_ids(state.set_id, settings.recent_block))
        pool = radio_pool()
        return {"type": "radio", "active": radio["active"], "query": radio["query"], "genre": radio["genre"],
                "ready": len(pool - recent), "total": len(radio["ids"]), "adding": len(audius_busy)}

    def radio_fill() -> None:
        """Keep RADIO_AHEAD station tracks analysed ahead of playback (runs in a thread)."""
        if not radio_lock.acquire(blocking=False):
            return
        try:
            while radio["active"]:
                status = radio_status()
                if status["ready"] >= RADIO_AHEAD:
                    break
                have = db.source_ids("audius")
                todo = [s for s in radio["ids"] if s not in have and s not in radio["failed"] and s not in audius_busy]
                if not todo:
                    break
                ids_before = list(radio["ids"])
                if add_audius(todo[0]) is None:
                    radio["failed"].add(todo[0])
                if radio["ids"] != ids_before:  # station changed meanwhile
                    continue
                bus.publish(radio_status())
        finally:
            radio_lock.release()
            bus.publish(radio_status())

    @app.post("/radio/start")
    async def radio_start(body: RadioBody) -> dict[str, Any]:
        """Auto-mix from Audius: a station built from a search (or trending)."""
        try:
            if body.query:
                results = await asyncio.to_thread(audius.search, body.query, 40)
            else:
                results = await asyncio.to_thread(audius.trending, body.genre, "week", 40)
        except Exception as exc:
            raise HTTPException(502, f"Audius failed: {exc}") from exc
        radio.update(active=True, query=body.query, genre=body.genre, ids=[t["id"] for t in results], failed=set())
        log_decision({"set_id": state.set_id, "kind": "source", "source": "user", "question": "auto-mix source",
                      "answer": f"Audius: {body.query or 'trending ' + (body.genre or '')}".strip(),
                      "reason": f"{len(results)} full-length tracks in the station"})
        threading.Thread(target=radio_fill, daemon=True, name="radio-fill").start()
        status = radio_status()
        bus.publish(status)
        return status

    @app.post("/radio/stop")
    def radio_stop() -> dict[str, Any]:
        radio.update(active=False)
        status = radio_status()
        bus.publish(status)
        return status

    @app.get("/radio")
    def radio_get() -> dict[str, Any]:
        return radio_status()

    @app.post("/library/{track_id}/grid")
    def library_grid(track_id: int, body: GridBody) -> dict[str, Any]:
        t = db.shift_grid(track_id, body.shift_s)
        if not t:
            raise HTTPException(404, "track not found")
        log_decision({"set_id": state.set_id, "kind": "grid_fix", "source": "user" if body.source == "user" else "rules",
                      "question": "beat grid correction", "answer": f"{t['artist']} - {t['title']}",
                      "reason": f"grid moved {body.shift_s * 1000:+.0f} ms ({body.source})"})
        return t

    @app.get("/library")
    def library(errors: bool = False) -> list[dict[str, Any]]:
        return db.list_tracks(include_errors=errors)

    @app.get("/library/{track_id}")
    def library_track(track_id: int) -> dict[str, Any]:
        return get_or_404(track_id)

    @app.get("/audio/{track_id}")
    def audio(track_id: int, request: Request):
        t = db.get_track(track_id, full=False)
        if not t:
            raise HTTPException(404, "track not found")
        if t.get("source") == "audius":
            # Streamed live from Audius, never stored.
            try:
                chunks = audius.stream_chunks(audius.best_url(t["source_id"]), request.headers.get("range"))
                status, headers = next(chunks)
            except Exception as exc:
                raise HTTPException(502, f"Audius stream failed: {exc}") from exc
            headers.setdefault("content-type", "audio/mpeg")
            return StreamingResponse(chunks, status_code=status, headers=headers, media_type=headers["content-type"])
        path = Path(t["path"])
        if not path.exists():
            raise HTTPException(404, "file missing on disk")
        kind = sniff(path)
        if kind == "aiff":
            return FileResponse(_flac_copy(path, transcode_dir, track_id), media_type="audio/flac")
        if kind not in NATIVE:
            # AAC / Opus / video containers (often mislabelled downloads): decode once to FLAC.
            dst = transcode_dir / f"{track_id}-{int(path.stat().st_mtime)}.flac"
            if not dst.exists():
                transcode_dir.mkdir(parents=True, exist_ok=True)
                to_flac(path, dst)
            return FileResponse(dst, media_type="audio/flac")
        return FileResponse(path, media_type=MEDIA_TYPES[{"mp3": ".mp3", "wav": ".wav", "flac": ".flac", "ogg": ".ogg"}[kind]])

    # ------------------------------------------------------------ set

    @app.post("/set/start")
    def set_start(body: SetBody) -> dict[str, Any]:
        state.vibe = body.vibe if body.vibe in rules.VIBES else "auto"
        state.phase = "warm-up" if state.vibe == "auto" else state.vibe
        state.played = 0
        state.phase_checked_at = 0
        state.set_id = db.new_set(state.vibe)
        bus.publish({"type": "set_started", "set_id": state.set_id, "vibe": state.vibe, "phase": state.phase})
        return {"set_id": state.set_id, "vibe": state.vibe, "phase": state.phase}

    @app.post("/set/vibe")
    def set_vibe(body: VibeBody) -> dict[str, Any]:
        if body.vibe not in rules.VIBES:
            raise HTTPException(400, f"vibe must be one of {rules.VIBES}")
        state.vibe = body.vibe
        if body.vibe != "auto":
            state.phase = body.vibe
        bus.publish({"type": "phase", "phase": state.phase, "vibe": state.vibe, "source": "user"})
        return {"vibe": state.vibe, "phase": state.phase}

    # ------------------------------------------------------------ brain

    @app.post("/brain/next")
    async def brain_next(body: NextBody) -> dict[str, Any]:
        set_id = ensure_set()
        if body.vibe and body.vibe in rules.VIBES and body.vibe != state.vibe:
            state.vibe = body.vibe
            if body.vibe != "auto":
                state.phase = body.vibe
        library = db.get_tracks_full()
        if not library:
            raise HTTPException(409, "library is empty - scan your music folder first")
        current = db.get_track(body.current_id) if body.current_id else None
        if radio["active"]:
            pool = radio_pool()
            recent_ids = set(body.history[-settings.recent_block:]) | set(db.recent_track_ids(set_id, settings.recent_block))
            fresh = pool - recent_ids - set(body.exclude) - ({current["id"]} if current else set())
            threading.Thread(target=radio_fill, daemon=True, name="radio-fill").start()
            if not fresh:
                if len(radio["ids"]) > len(pool) + len(radio["failed"]):
                    raise HTTPException(409, "Audius station warming up - preparing the next track")
                # Station exhausted: allow repeats rather than stop the music.
            library = [t for t in library if t["id"] in pool] or library

        # Every 5 tracks in auto vibe, Jev scores where the set should go next.
        if state.vibe == "auto" and state.played >= 5 and state.played - state.phase_checked_at >= 5:
            state.phase_checked_at = state.played
            last = [track_card(t) for t in (db.get_track(i) for i in db.recent_track_ids(set_id, 5)) if t]
            res = await brain.choose_phase(state.phase, state.played, last, set_id)
            state.phase = res["phase"]
            bus.publish({"type": "phase", "phase": state.phase, "vibe": state.vibe, "source": res["source"]})

        recent = list(dict.fromkeys(body.history[-settings.recent_block:] + db.recent_track_ids(set_id, settings.recent_block)))
        if body.switch and current:
            cands = rules.switch_candidates(current, library, recent, body.exclude)
            db.add_override(set_id, "switch_it_up", current["id"], "listener asked for a new direction")
        else:
            cands = rules.candidates(current, library, recent, body.exclude, limit=20, phase=state.phase)
        taste = db.taste()
        taste_text = "; ".join(filter(None, [
            ("favourite genres: " + ", ".join(f"{g} ({n} plays)" for g, n in taste["genres"])) if taste["genres"] else "",
            ("most played artists: " + ", ".join(f"{a} ({n})" for a, n in taste["artists"])) if taste["artists"] else "",
            ("often skips: " + ", ".join(str((db.get_track(i, full=False) or {}).get("title")) for i in list(taste["skipped"])[:5]))
            if taste["skipped"] else "",
        ]))
        if not cands:
            raise HTTPException(409, "no candidate tracks left")
        last_cards = [track_card(t) for t in (db.get_track(i) for i in recent[:3]) if t]
        overrides = [
            f"{o['action']}: {o.get('artist') or ''} {o.get('title') or ''} {o.get('note') or ''}".strip()
            for o in db.recent_overrides(set_id)
        ]
        result = await brain.choose_next(
            current, cands,
            {"phase": state.phase, "vibe": state.vibe, "last_cards": last_cards, "overrides": overrides,
             "taste": taste, "taste_text": taste_text, "switch": body.switch},
            set_id,
        )
        t = result["track"]
        return {
            "track": {k: v for k, v in t.items() if k not in ("energy_bars", "vocal_bars", "downbeats")},
            "source": result["source"],
            "confidence": result["confidence"],
            "reason": result["reason"],
            "fallback": result.get("fallback"),
            "decision_ids": result["decision_ids"],
            "phase": state.phase,
            "candidates": len(cands),
        }

    @app.post("/brain/transition")
    async def brain_transition(body: TransitionBody) -> dict[str, Any]:
        set_id = ensure_set()
        a, b = get_or_404(body.from_id), get_or_404(body.to_id)
        recent = [h["transition"] for h in db.history(set_id) if h.get("transition")][-3:]
        return await brain.choose_transition(a, b, state.phase, set_id, body.start_s, state.mix_style, recent,
                                             state.flair, db.learned_styles(), rng)

    @app.post("/brain/alternatives")
    def brain_alternatives(body: AlternativesBody) -> list[dict[str, Any]]:
        """Other ways to do this mix (used when the pre-listen doesn't like the plan)."""
        a, b = get_or_404(body.from_id), get_or_404(body.to_id)
        options = [s for s in rules.style_options(a, b, state.phase, state.mix_style, state.flair) if s not in body.exclude]
        plans = []
        for style in options[: body.limit]:
            p = rules.plan_transition(a, b, style, start_s=body.start_s, mix_style=state.mix_style, flair=state.flair)
            plans.append({**p.to_dict(), "source": "rules", "confidence": None, "fallback": "pre-listen alternative"})
        return plans

    @app.post("/brain/prelisten")
    def brain_prelisten(body: PrelistenLogBody) -> dict[str, Any]:
        """Log what Jev heard when it pre-listened to the next mix."""
        parts = []
        if body.switched_from:
            parts.append(f"switched from {body.switched_from}")
        parts += body.fixes
        parts += [f"heard: {i}" for i in body.issues]
        saved = log_decision({
            "set_id": state.set_id, "kind": "prelisten", "source": "rules", "question": "pre-listen the next mix",
            "answer": f"{body.style} scored {body.score:.1f}/10", "confidence": round(body.score / 10, 3),
            "reason": "; ".join(parts) or "clean",
            "extra": {"beat_error_ms": body.beat_error_ms, "render_ms": body.render_ms},
        })
        return {"decision_id": saved["id"]}

    @app.post("/set/flair")
    def set_flair(body: FlairBody) -> dict[str, Any]:
        if body.flair not in rules.FLAIRS:
            raise HTTPException(400, f"flair must be one of {list(rules.FLAIRS)}")
        state.flair = body.flair
        bus.publish({"type": "flair", "flair": state.flair})
        return {"flair": state.flair}

    @app.post("/feedback")
    def feedback(body: FeedbackBody) -> dict[str, Any]:
        """Listener ratings. Jev learns bounded preferences from these (never its own code or rules)."""
        if body.kind == "transition" and body.style not in rules.TRANSITION_BARS:
            raise HTTPException(400, "unknown transition style")
        fid = db.add_feedback(state.set_id, body.kind, body.value, body.style, body.track_id, body.note)
        log_decision({"set_id": state.set_id, "kind": "feedback", "source": "user", "question": body.kind,
                      "answer": ("fire" if body.value > 0 else "nah") + (f" on {body.style}" if body.style else ""),
                      "reason": body.note})
        return {"id": fid, "learned": db.learned_styles()}

    @app.get("/learning")
    def learning() -> dict[str, Any]:
        return {"styles": db.learned_styles(), "taste": db.taste()}

    @app.post("/learning/reset")
    def learning_reset() -> dict[str, Any]:
        db.reset_learning()
        log_decision({"set_id": state.set_id, "kind": "feedback", "source": "user", "question": "reset",
                      "answer": "learning reset", "reason": "listener cleared Jev's learned move preferences"})
        return {"styles": {}}

    @app.post("/set/mix-style")
    def set_mix_style(body: MixStyleBody) -> dict[str, Any]:
        if body.mix_style not in rules.MIX_STYLES:
            raise HTTPException(400, f"mix_style must be one of {list(rules.MIX_STYLES)}")
        state.mix_style = body.mix_style
        bus.publish({"type": "mix_style", "mix_style": state.mix_style})
        return {"mix_style": state.mix_style}

    @app.post("/brain/override")
    def brain_override(body: OverrideBody) -> dict[str, Any]:
        set_id = ensure_set()
        oid = db.add_override(set_id, body.action, body.track_id, body.note)
        t = db.get_track(body.track_id, full=False) if body.track_id else None
        saved = log_decision({
            "set_id": set_id, "kind": "override", "source": "user", "question": body.action,
            "answer": f"{t['artist']} - {t['title']}" if t else None, "reason": body.note,
        })
        return {"id": oid, "decision_id": saved["id"]}

    @app.get("/taste")
    def taste() -> dict[str, Any]:
        t = db.taste()
        return {"genres": t["genres"], "artists": t["artists"], "skipped": len(t["skipped"])}

    @app.get("/decisions")
    def decisions(limit: int = 100, set_id: int | None = None) -> list[dict[str, Any]]:
        return db.list_decisions(limit, set_id)

    # ------------------------------------------------------------ history

    @app.post("/history/played")
    def history_played(body: PlayedBody) -> dict[str, Any]:
        set_id = ensure_set()
        hid = db.add_history(set_id, body.track_id, body.transition, body.source, state.phase)
        state.played += 1
        bus.publish({"type": "played", "track_id": body.track_id, "played": state.played})
        if radio["active"]:
            threading.Thread(target=radio_fill, daemon=True, name="radio-fill").start()
        return {"id": hid, "played": state.played}

    @app.get("/history")
    def history(set_id: int | None = None) -> list[dict[str, Any]]:
        sid = set_id or state.set_id
        return db.history(sid) if sid else []

    @app.get("/history/export")
    def history_export(format: str = Query("json", pattern="^(json|txt)$"), set_id: int | None = None):
        sid = set_id or state.set_id
        rows = db.history(sid) if sid else []
        if not rows:
            raise HTTPException(404, "no set history yet")
        start = rows[0]["started_at"]
        if format == "json":
            payload = {
                "set_id": sid,
                "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "tracks": [
                    {**{k: r[k] for k in ("track_id", "title", "artist", "bpm", "camelot", "energy", "genre",
                                          "transition", "source", "phase")},
                     "offset_s": round(r["started_at"] - start, 1)}
                    for r in rows
                ],
                "decisions": db.list_decisions(1000, sid),
            }
            return PlainTextResponse(json.dumps(payload, indent=2), media_type="application/json",
                                     headers={"Content-Disposition": f'attachment; filename="jevdj-set-{sid}.json"'})
        lines = [f"JevDJ set #{sid} - {time.strftime('%Y-%m-%d %H:%M', time.localtime(start))}", ""]
        for i, r in enumerate(rows, 1):
            off = int(r["started_at"] - start)
            name = f"{r['artist']} - {r['title']}" if r["artist"] else r["title"]
            lines.append(f"{i:02d}. [{off // 3600:d}:{off % 3600 // 60:02d}:{off % 60:02d}] {name}  "
                         f"({r['bpm']:.0f} BPM, {r['camelot']})  via {r['transition'] or 'start'}")
        return PlainTextResponse("\n".join(lines) + "\n",
                                 headers={"Content-Disposition": f'attachment; filename="jevdj-set-{sid}.txt"'})

    # ------------------------------------------------------------ voice

    @app.post("/voice/line")
    async def voice_line(body: VoiceBody) -> dict[str, Any]:
        nxt = get_or_404(body.next_id)
        cur = db.get_track(body.current_id, full=False) if body.current_id else None
        res = await voice.line({
            "next_title": nxt["title"], "next_artist": nxt["artist"], "next_genre": nxt["genre"],
            "next_bpm": nxt["bpm"] or 0, "current_title": cur["title"] if cur else None, "phase": state.phase,
            "mode": body.mode,
        })
        log_decision({"set_id": state.set_id, "kind": "voice", "source": res["source"],
                      "question": "DJ voice line", "answer": res["text"]})
        return res

    @app.get("/voice/audio/{name}")
    def voice_audio(name: str) -> FileResponse:
        if not name.endswith(".wav") or "/" in name or "\\" in name or ".." in name:
            raise HTTPException(400, "bad name")
        path = settings.voice_dir / name
        if not path.exists():
            raise HTTPException(404, "not found")
        return FileResponse(path, media_type="audio/wav")

    # ------------------------------------------------------------ events

    @app.websocket("/events")
    async def events(ws: WebSocket) -> None:
        await ws.accept()
        q = bus.subscribe()
        try:
            await ws.send_json({"type": "hello", "jev_online": brain.online, "phase": state.phase, "vibe": state.vibe})
            while True:
                event = await q.get()
                await ws.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            bus.unsubscribe(q)

    return app


def _safe_name(name: str) -> str:
    name = Path(name).name  # drop any client-side directories
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "track"


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(1, 1000):
        cand = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not cand.exists():
            return cand
    raise HTTPException(409, "too many files with the same name")


def _flac_copy(src: Path, out_dir: Path, track_id: int) -> Path:
    """Lossless AIFF -> FLAC conversion so Chromium can decode it. Cached per track and mtime."""
    import soundfile as sf

    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{track_id}-{int(src.stat().st_mtime)}.flac"
    if not dst.exists():
        info = sf.info(str(src))
        subtype = "PCM_24" if "24" in info.subtype or "32" in info.subtype or "FLOAT" in info.subtype else "PCM_16"
        data, sr = sf.read(str(src), dtype="float32" if subtype == "PCM_24" else "int16", always_2d=True)
        sf.write(str(dst), data, sr, format="FLAC", subtype=subtype)
    return dst


app = create_app()
