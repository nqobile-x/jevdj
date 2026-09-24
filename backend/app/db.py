"""SQLite storage: track analysis cache, AI decision log, set history and overrides."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_JSON_COLS = {"energy_bars", "vocal_bars", "downbeats"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    mtime REAL NOT NULL,
    title TEXT, artist TEXT, genre TEXT,
    duration REAL, bpm REAL, beat_period REAL, first_beat REAL, first_downbeat REAL,
    key_name TEXT, camelot TEXT, key_confidence REAL, energy REAL,
    energy_bars TEXT, vocal_bars TEXT, downbeats TEXT,
    intro_end REAL, outro_start REAL,
    error TEXT,
    analysed_at REAL,
    source TEXT,
    source_id TEXT,
    permalink TEXT,
    artwork TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    set_id INTEGER,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    question TEXT,
    options TEXT,
    answer TEXT,
    confidence REAL,
    probabilities TEXT,
    reason TEXT,
    extra TEXT
);
CREATE TABLE IF NOT EXISTS sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    vibe TEXT
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id INTEGER NOT NULL,
    track_id INTEGER NOT NULL,
    started_at REAL NOT NULL,
    transition TEXT,
    source TEXT,
    phase TEXT
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    set_id INTEGER,
    kind TEXT NOT NULL,
    style TEXT,
    track_id INTEGER,
    value REAL NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    set_id INTEGER,
    action TEXT NOT NULL,
    track_id INTEGER,
    note TEXT
);
"""

TRACK_SUMMARY_COLS = (
    "id, path, title, artist, genre, duration, bpm, beat_period, first_beat, first_downbeat, "
    "key_name, camelot, key_confidence, energy, intro_end, outro_start, error, source, source_id, permalink, artwork"
)

# Columns added after the first release: added to existing databases on open.
_MIGRATIONS = {"source": "TEXT", "source_id": "TEXT", "permalink": "TEXT", "artwork": "TEXT"}


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(tracks)")}
        for col, typ in _MIGRATIONS.items():
            if col not in have:
                self._conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} {typ}")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _exec(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # ------------------------------------------------------------ tracks

    @staticmethod
    def _track_row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for col in _JSON_COLS & d.keys():
            d[col] = json.loads(d[col]) if d[col] else []
        return d

    def cached_mtimes(self) -> dict[str, float]:
        """Local-folder tracks only (online sources are not part of the folder scan)."""
        return {r["path"]: r["mtime"] for r in self._query("SELECT path, mtime FROM tracks WHERE source IS NULL")}

    def source_ids(self, source: str) -> dict[str, int]:
        rows = self._query("SELECT id, source_id FROM tracks WHERE source = ? AND error IS NULL", (source,))
        return {r["source_id"]: r["id"] for r in rows}

    def upsert_track(self, analysis: dict[str, Any], mtime: float) -> int:
        row = {k: (json.dumps(v) if k in _JSON_COLS else v) for k, v in analysis.items()}
        row.update(mtime=mtime, analysed_at=time.time(), error=None)
        cols = list(row.keys())
        sql = (
            f"INSERT INTO tracks ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)}) "
            f"ON CONFLICT(path) DO UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in cols if c != 'path')}"
        )
        self._exec(sql, row)
        return self._query("SELECT id FROM tracks WHERE path = ?", (row["path"],))[0]["id"]

    def mark_error(self, path: str, mtime: float, error: str) -> None:
        self._exec(
            "INSERT INTO tracks (path, mtime, title, error, analysed_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, error=excluded.error, analysed_at=excluded.analysed_at",
            (path, mtime, Path(path).stem, error, time.time()),
        )

    def remove_missing(self, present: set[str]) -> int:
        gone = [p for p in self.cached_mtimes() if p not in present]
        for p in gone:
            self._exec("DELETE FROM tracks WHERE path = ?", (p,))
        return len(gone)

    def list_tracks(self, include_errors: bool = False) -> list[dict[str, Any]]:
        where = "" if include_errors else "WHERE error IS NULL"
        return [dict(r) for r in self._query(f"SELECT {TRACK_SUMMARY_COLS} FROM tracks {where} ORDER BY artist, title")]

    def get_track(self, track_id: int, full: bool = True) -> dict[str, Any] | None:
        cols = "*" if full else TRACK_SUMMARY_COLS
        rows = self._query(f"SELECT {cols} FROM tracks WHERE id = ?", (track_id,))
        return self._track_row(rows[0]) if rows else None

    def shift_grid(self, track_id: int, shift_s: float) -> dict[str, Any] | None:
        """Move a track's beat grid (manual fix or learned from live beat lock)."""
        t = self.get_track(track_id)
        if not t or t.get("error"):
            return None
        period = t["beat_period"] or 60.0 / t["bpm"]
        first_beat = (t["first_beat"] + shift_s) % period
        first_downbeat = t["first_downbeat"] + shift_s
        while first_downbeat < 0:
            first_downbeat += 4 * period
        downbeats = [d + shift_s for d in t["downbeats"] if 0 <= d + shift_s < t["duration"]]
        self._exec(
            "UPDATE tracks SET first_beat = ?, first_downbeat = ?, downbeats = ?, intro_end = ?, outro_start = ? WHERE id = ?",
            (first_beat, first_downbeat, json.dumps([round(d, 4) for d in downbeats]),
             t["intro_end"] + shift_s, t["outro_start"] + shift_s, track_id),
        )
        return self.get_track(track_id)

    def get_tracks_full(self) -> list[dict[str, Any]]:
        return [self._track_row(r) for r in self._query("SELECT * FROM tracks WHERE error IS NULL")]

    # ------------------------------------------------------------ decisions

    def log_decision(self, d: dict[str, Any]) -> dict[str, Any]:
        row = {
            "ts": d.get("ts", time.time()),
            "set_id": d.get("set_id"),
            "kind": d["kind"],
            "source": d["source"],
            "question": d.get("question"),
            "options": json.dumps(d.get("options")),
            "answer": json.dumps(d.get("answer")),
            "confidence": d.get("confidence"),
            "probabilities": json.dumps(d.get("probabilities")),
            "reason": d.get("reason"),
            "extra": json.dumps(d.get("extra")),
        }
        cols = list(row.keys())
        cur = self._exec(f"INSERT INTO decisions ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})", row)
        out = dict(d)
        out.update(id=cur.lastrowid, ts=row["ts"])
        return out

    def list_decisions(self, limit: int = 100, set_id: int | None = None) -> list[dict[str, Any]]:
        where, params = ("WHERE set_id = ?", (set_id, limit)) if set_id else ("", (limit,))
        rows = self._query(f"SELECT * FROM decisions {where} ORDER BY id DESC LIMIT ?", params)
        out = []
        for r in rows:
            d = dict(r)
            for c in ("options", "answer", "probabilities", "extra"):
                d[c] = json.loads(d[c]) if d[c] else None
            out.append(d)
        return out

    # ------------------------------------------------------------ sets / history / overrides

    def new_set(self, vibe: str) -> int:
        return self._exec("INSERT INTO sets (started_at, vibe) VALUES (?, ?)", (time.time(), vibe)).lastrowid

    def latest_set(self) -> int | None:
        rows = self._query("SELECT id FROM sets ORDER BY id DESC LIMIT 1")
        return rows[0]["id"] if rows else None

    def add_history(self, set_id: int, track_id: int, transition: str | None, source: str | None, phase: str | None) -> int:
        return self._exec(
            "INSERT INTO history (set_id, track_id, started_at, transition, source, phase) VALUES (?, ?, ?, ?, ?, ?)",
            (set_id, track_id, time.time(), transition, source, phase),
        ).lastrowid

    def history(self, set_id: int) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT h.*, t.title, t.artist, t.bpm, t.camelot, t.energy, t.genre FROM history h "
            "LEFT JOIN tracks t ON t.id = h.track_id WHERE h.set_id = ? ORDER BY h.id",
            (set_id,),
        )
        return [dict(r) for r in rows]

    def recent_track_ids(self, set_id: int | None, n: int) -> list[int]:
        if set_id is None:
            return []
        rows = self._query("SELECT track_id FROM history WHERE set_id = ? ORDER BY id DESC LIMIT ?", (set_id, n))
        return [r["track_id"] for r in rows]

    def add_override(self, set_id: int | None, action: str, track_id: int | None, note: str | None) -> int:
        return self._exec(
            "INSERT INTO overrides (ts, set_id, action, track_id, note) VALUES (?, ?, ?, ?, ?)",
            (time.time(), set_id, action, track_id, note),
        ).lastrowid

    def recent_overrides(self, set_id: int | None, n: int = 5) -> list[dict[str, Any]]:
        if set_id is None:
            return []
        rows = self._query(
            "SELECT o.action, o.track_id, o.note, t.title, t.artist FROM overrides o "
            "LEFT JOIN tracks t ON t.id = o.track_id WHERE o.set_id = ? ORDER BY o.id DESC LIMIT ?",
            (set_id, n),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ taste memory (all sets)

    def taste(self, top: int = 4) -> dict[str, Any]:
        """What the listener lets play vs. skips, across every set."""
        genres = self._query(
            "SELECT t.genre AS name, COUNT(*) AS n FROM history h JOIN tracks t ON t.id = h.track_id "
            "WHERE t.genre IS NOT NULL AND t.genre != '' GROUP BY t.genre ORDER BY n DESC LIMIT ?", (top,))
        artists = self._query(
            "SELECT t.artist AS name, COUNT(*) AS n FROM history h JOIN tracks t ON t.id = h.track_id "
            "WHERE t.artist IS NOT NULL AND t.artist != '' GROUP BY t.artist ORDER BY n DESC LIMIT ?", (top,))
        skipped = self._query(
            "SELECT track_id, COUNT(*) AS n FROM overrides WHERE action IN ('veto', 'pick_another') "
            "AND track_id IS NOT NULL GROUP BY track_id")
        return {
            "genres": [(r["name"], r["n"]) for r in genres],
            "artists": [(r["name"], r["n"]) for r in artists],
            "skipped": {r["track_id"]: r["n"] for r in skipped},
        }

    # ------------------------------------------------------------ learning from feedback

    def add_feedback(self, set_id: int | None, kind: str, value: float, style: str | None = None,
                     track_id: int | None = None, note: str | None = None) -> int:
        return self._exec(
            "INSERT INTO feedback (ts, set_id, kind, style, track_id, value, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), set_id, kind, style, track_id, value, note),
        ).lastrowid

    def learned_styles(self) -> dict[str, dict[str, float]]:
        """Per move: likes, dislikes and a bounded bonus in [-0.5, 0.5] (shrunk toward 0 with few ratings)."""
        rows = self._query(
            "SELECT style, SUM(CASE WHEN value > 0 THEN value ELSE 0 END) AS up, "
            "SUM(CASE WHEN value < 0 THEN -value ELSE 0 END) AS down, COUNT(*) AS n "
            "FROM feedback WHERE kind = 'transition' AND style IS NOT NULL GROUP BY style"
        )
        out = {}
        for r in rows:
            bonus = (r["up"] - r["down"]) / (r["n"] + 3)
            out[r["style"]] = {"up": r["up"], "down": r["down"], "n": r["n"], "bonus": max(-0.5, min(0.5, bonus))}
        return out

    def reset_learning(self) -> None:
        self._exec("DELETE FROM feedback")
