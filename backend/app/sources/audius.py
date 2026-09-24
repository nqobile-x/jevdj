"""Audius: a free, legal, open music network (artists upload their own music).

Streaming only: to add a track we stream it once into a temporary file, analyse it (beat grid,
key, energy) and delete the audio, keeping only the numbers. At play time the audio is streamed
live from Audius through the backend to the browser's mixer. Nothing is stored on disk.
Docs: https://docs.audius.co  (no key needed; app_name identifies us)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("jevdj.audius")

API = "https://api.audius.co/v1"
APP_NAME = "JevDJ"
MIN_SECONDS = 90  # skip snippets and teasers by default
MAX_SECONDS = 600  # skip full DJ mixes: Jev mixes tracks, not other people's mixes


def _client() -> httpx.Client:
    return httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": "JevDJ/0.1"})


def _summary(t: dict[str, Any]) -> dict[str, Any]:
    art = t.get("artwork") or {}
    return {
        "id": t["id"],
        "title": t.get("title") or "",
        "artist": (t.get("user") or {}).get("name") or "",
        "handle": (t.get("user") or {}).get("handle") or "",
        "genre": (t.get("genre") or "").lower(),
        "mood": t.get("mood"),
        "duration": t.get("duration") or 0,
        "bpm": t.get("bpm"),
        "key": t.get("musical_key"),
        "camelot": _camelot(t.get("musical_key")),
        "plays": t.get("play_count") or 0,
        "artwork": art.get("150x150") or art.get("480x480"),
        "permalink": f"https://audius.co{t['permalink']}" if t.get("permalink") else None,
        "streamable": bool(t.get("is_streamable")) and not t.get("is_stream_gated"),
    }


def _camelot(key: str | None) -> str | None:
    if not key:
        return None
    try:
        from app.analysis.camelot import camelot_from_name

        return camelot_from_name(key)
    except (ValueError, KeyError):
        return None


def _filter(tracks: list[dict], min_seconds: int) -> list[dict]:
    out = [_summary(t) for t in tracks if t.get("id")]
    return [t for t in out if t["streamable"] and min_seconds <= t["duration"] <= MAX_SECONDS]


def search(query: str, limit: int = 30, min_seconds: int = MIN_SECONDS) -> list[dict[str, Any]]:
    with _client() as c:
        # Ask for a full page: Audius defaults to 10, and snippets get filtered out below.
        r = c.get(f"{API}/tracks/search", params={"query": query, "app_name": APP_NAME, "limit": 50})
        r.raise_for_status()
        return _filter(r.json().get("data") or [], min_seconds)[:limit]


def trending(genre: str | None = None, time: str = "week", limit: int = 30, min_seconds: int = MIN_SECONDS) -> list[dict[str, Any]]:
    params = {"app_name": APP_NAME, "time": time}
    if genre:
        params["genre"] = genre
    with _client() as c:
        r = c.get(f"{API}/tracks/trending", params=params)
        r.raise_for_status()
        return _filter(r.json().get("data") or [], min_seconds)[:limit]


def track(track_id: str) -> dict[str, Any]:
    with _client() as c:
        r = c.get(f"{API}/tracks/{track_id}", params={"app_name": APP_NAME})
        r.raise_for_status()
        return _summary(r.json()["data"])


def safe_id(track_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9]{1,32}", track_id):
        raise ValueError("bad Audius track id")
    return track_id


PROBE_SECONDS = 3.0
MIN_RATE = 64_000  # bytes/s; below this a content node is too slow, try a mirror


def _stream_urls(c: httpx.Client, track_id: str) -> list[str]:
    """Primary stream URL plus the same path on each mirror node."""
    data = c.get(f"{API}/tracks/{track_id}", params={"app_name": APP_NAME}).json().get("data") or {}
    stream = data.get("stream") or {}
    urls = []
    if stream.get("url"):
        primary = httpx.URL(stream["url"])
        urls.append(str(primary))
        for m in stream.get("mirrors") or []:
            host = httpx.URL(m)
            urls.append(str(primary.copy_with(scheme=host.scheme, host=host.host, port=host.port)))
    urls.append(f"{API}/tracks/{track_id}/stream?app_name={APP_NAME}")  # let Audius pick
    return urls


def _download(c: httpx.Client, url: str, tmp: Path, give_up_if_slow: bool) -> bool:
    """Stream to tmp. With give_up_if_slow, abandon a node that is crawling. True = complete."""
    import time

    t0 = time.monotonic()
    got = 0
    with c.stream("GET", url) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
                got += len(chunk)
                elapsed = time.monotonic() - t0
                if give_up_if_slow and elapsed > PROBE_SECONDS and got / elapsed < MIN_RATE:
                    log.info("Audius node too slow (%.0f KB/s): %s", got / elapsed / 1000, httpx.URL(url).host)
                    return False
    return True


def best_url(track_id: str) -> str:
    """Race the primary node and mirrors with a small range request; the first good answer wins."""
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    track_id = safe_id(track_id)
    with _client() as c:
        urls = _stream_urls(c, track_id)

    def probe(url: str) -> str:
        with _client() as pc:
            r = pc.get(url, headers={"Range": "bytes=0-131071"}, timeout=8)
            if r.status_code not in (200, 206) or len(r.content) < 32_000:
                raise RuntimeError(f"bad probe {r.status_code}")
            return str(r.url)  # after redirects: the node (or storage) that actually serves it

    pool = ThreadPoolExecutor(max_workers=len(urls))
    pending = {pool.submit(probe, u) for u in urls}
    try:
        while pending:
            done, pending = wait(pending, timeout=10, return_when=FIRST_COMPLETED)
            if not done:
                break
            for f in done:
                if f.exception() is None:
                    return f.result()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    raise RuntimeError("no Audius node could serve this track")


def stream_chunks(url: str, range_header: str | None = None):
    """Yield (status, headers) then audio chunks, relayed live from an Audius node."""
    headers = {"Range": range_header} if range_header else {}
    with _client() as c, c.stream("GET", url, headers=headers) as r:
        r.raise_for_status()
        keep = {k: r.headers[k] for k in ("content-type", "content-length", "content-range", "accept-ranges") if k in r.headers}
        yield r.status_code, keep
        yield from r.iter_bytes(1 << 16)


def fetch(track_id: str, cache_dir: Path) -> Path:
    """Stream the track into a temporary file for analysis (caller deletes it afterwards)."""
    track_id = safe_id(track_id)
    cache_dir.mkdir(parents=True, exist_ok=True)
    dst = cache_dir / f"{track_id}.mp3"
    if dst.exists() and dst.stat().st_size > 50_000:
        return dst
    tmp = dst.with_suffix(".part")
    with _client() as c:
        urls = _stream_urls(c, track_id)
        for i, url in enumerate(urls):
            last = i == len(urls) - 1
            try:
                if _download(c, url, tmp, give_up_if_slow=not last):
                    tmp.replace(dst)
                    return dst
            except httpx.HTTPError as exc:
                log.info("Audius node failed (%s): %s", exc, httpx.URL(url).host)
                if last:
                    raise
    raise RuntimeError("no Audius node could serve this track")


def cache_size(cache_dir: Path) -> int:  # should stay ~0: audio is deleted after analysis
    return sum(p.stat().st_size for p in cache_dir.glob("*.mp3")) if cache_dir.exists() else 0
