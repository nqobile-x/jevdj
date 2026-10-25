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
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA temp_store=MEMORY")
        except sqlite3.OperationalError:
            self._conn.execute("PRAGMA journal_mode=MEMORY")
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

    # ------------------------------------------------------------ DJ crate training (local + YouTube/Audius)

    def train_crate_profile(self) -> dict[str, Any]:
        """Scan all analysed local, YouTube/TikTok, and Audius tracks in SQLite to build a
        comprehensive Trained DJ Crate Profile: BPM sweet spots, Camelot key centres,
        vocal vs instrumental balance, harmonic pair matrix, and synthetic style priors."""
        from app.brain import rules

        tracks = [t for t in self.get_tracks_full() if t.get("bpm") and (t.get("duration") or 0) <= rules.MAX_TRACK_SECONDS]
        if not tracks:
            return {
                "total_tracks": 0, "local_tracks": 0, "youtube_tracks": 0, "audius_tracks": 0,
                "bpm_clusters": [], "top_keys": [], "genres": [], "vocal_led_pct": 0,
                "smooth_pair_pct": 0, "top_pairs": [], "trained_styles": {}, "summary": "no analysed tracks yet",
            }

        local_n = sum(1 for t in tracks if not t.get("source"))
        yt_n = sum(1 for t in tracks if t.get("source") in ("youtube", "tiktok"))
        audius_n = sum(1 for t in tracks if t.get("source") == "audius")

        # 1. Dominant BPM clusters (rounded to 4-BPM bands)
        bpm_bands: dict[str, int] = {}
        for t in tracks:
            b = int(round(float(t["bpm"]) / 4.0) * 4)
            label = f"{b - 2}-{b + 2} BPM"
            bpm_bands[label] = bpm_bands.get(label, 0) + 1
        top_bpms = sorted(bpm_bands.items(), key=lambda kv: -kv[1])[:4]

        # 2. Dominant Camelot keys & Genres
        key_counts: dict[str, int] = {}
        genre_counts: dict[str, int] = {}
        vocal_led = 0
        for t in tracks:
            cam = str(t.get("camelot") or "")
            if cam:
                key_counts[cam] = key_counts.get(cam, 0) + 1
            g = str(t.get("genre") or "").strip().lower()
            if g:
                genre_counts[g] = genre_counts.get(g, 0) + 1
            vb = t.get("vocal_bars") or []
            if vb and (sum(vb) / len(vb)) > 0.42:
                vocal_led += 1
        top_keys = sorted(key_counts.items(), key=lambda kv: -kv[1])[:5]
        top_genres = sorted(genre_counts.items(), key=lambda kv: -kv[1])[:5]
        vocal_pct = round(100 * vocal_led / len(tracks))

        # 3. Pairwise harmonic + BPM compatibility matrix & best transition moves
        smooth_pairs = 0
        total_pairs = 0
        best_pairs: list[tuple[float, dict[str, Any]]] = []
        style_Suitability: dict[str, int] = {}

        sample = tracks[:45]  # cap O(N^2) so training finishes in <50ms
        for i, a in enumerate(sample):
            for j, b in enumerate(sample):
                if i == j:
                    continue
                total_pairs += 1
                sm = rules.transition_smoothness(a, b)
                if sm >= 2:
                    smooth_pairs += 1
                score = rules.rule_score(a, b, "build")
                opts = rules.style_options(a, b, "build", "club", "creative")
                best_style = opts[0] if opts else "long_blend"
                if sm >= 2:
                    style_Suitability[best_style] = style_Suitability.get(best_style, 0) + 1
                if len(best_pairs) < 12 or score > best_pairs[-1][0]:
                    best_pairs.append((score, {
                        "from": f"{a.get('artist') or ''} - {a.get('title') or ''}".strip(" -"),
                        "to": f"{b.get('artist') or ''} - {b.get('title') or ''}".strip(" -"),
                        "bpm": f"{a['bpm']:.0f}->{b['bpm']:.0f}",
                        "camelot": f"{a.get('camelot')}->{b.get('camelot')}",
                        "style": best_style,
                        "smoothness": sm,
                        "score": round(score, 3),
                    }))
                    best_pairs.sort(key=lambda x: -x[0])
                    best_pairs = best_pairs[:8]

        smooth_pct = round(100 * smooth_pairs / max(total_pairs, 1))

        # 4. Seed gentle positive feedback priors for styles that naturally fit the user's crate
        # if the user hasn't explicitly rated those styles yet
        existing_learned = self.learned_styles()
        set_id = self.latest_set()
        top_natural_styles = sorted(style_Suitability.items(), key=lambda kv: -kv[1])[:5]
        for st, count in top_natural_styles:
            if st not in existing_learned and count >= 2:
                self.add_feedback(set_id, "transition", 0.5, st, None, "crate training prior")

        summary = (
            f"Trained on {len(tracks)} tracks ({local_n} local, {yt_n} YouTube/TikTok, {audius_n} Audius): "
            f"BPM clusters {', '.join(f'{k} ({v})' for k, v in top_bpms[:3])}; "
            f"key centres {', '.join(f'{k} ({v})' for k, v in top_keys[:4])}; "
            f"genres {', '.join(f'{k} ({v})' for k, v in top_genres[:4])}; "
            f"{vocal_pct}% vocal-led ({100 - vocal_pct}% instrumental groove); "
            f"{smooth_pct}% of track pairs have smooth/seamless harmonic + BPM transitions."
        )
        return {
            "total_tracks": len(tracks),
            "local_tracks": local_n,
            "youtube_tracks": yt_n,
            "audius_tracks": audius_n,
            "bpm_clusters": [{"band": k, "count": v} for k, v in top_bpms],
            "top_keys": [{"camelot": k, "count": v} for k, v in top_keys],
            "genres": [{"genre": k, "count": v} for k, v in top_genres],
            "vocal_led_pct": vocal_pct,
            "smooth_pair_pct": smooth_pct,
            "top_pairs": [p for _s, p in best_pairs[:6]],
            "trained_styles": self.learned_styles(),
            "summary": summary,
        }

