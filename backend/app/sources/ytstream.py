"""YouTube Music, YouTube and TikTok ingestion via yt-dlp.

Supports:
- Searching YouTube / YouTube Music (`ytsearchN:query`) for tracks (amapiano, afro house, r&b, etc.)
  with intelligent DJ curation (ranking biggest artist hits, filtering out interviews/mixes/reactions).
- Resolving and downloading direct URLs from YouTube, YouTube Music (`music.youtube.com`), and TikTok (`tiktok.com`, `vm.tiktok.com`).
- Extracting audio directly into `stream_dir` (preferring native m4a/webm without slow re-encode) so tracks can be analysed and served in 1-2s.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger("jevdj.ytstream")

MIN_SECONDS = 30   # allow shorter TikTok sounds/edits as well as full tracks
MAX_SECONDS = 720  # skip DJ sets / hour-long mixes; Jev mixes individual tracks

_JUNK_RE = re.compile(
    r"\b(?:interview|podcast|reaction|reacts\s+to|tutorial|how\s+to|karaoke|behind\s+the\s+scenes|"
    r"documentary|explained|review|unboxing|teaser\s+trailer|full\s+album|1\s*hour|2\s*hours|"
    r"non\s*stop\s*mix|megamix|dj\s+mix|live\s+set|boiler\s+room)\b",
    re.IGNORECASE,
)


def is_url(text: str) -> bool:
    text = text.strip()
    return text.startswith(("http://", "https://"))


def detect_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "tiktok.com" in host:
        return "tiktok"
    if "music.youtube.com" in host:
        return "ytmusic"
    return "youtube"


def canonical_id(entry: dict[str, Any], fallback_url: str = "") -> str:
    raw_id = str(entry.get("id") or "").strip()
    extractor = str(entry.get("extractor_key") or entry.get("extractor") or "").lower()
    url = str(entry.get("webpage_url") or entry.get("url") or fallback_url)
    plat = "tiktok" if ("tiktok" in extractor or "tiktok.com" in url.lower()) else "yt"
    if raw_id and re.fullmatch(r"[A-Za-z0-9_-]{3,40}", raw_id):
        return f"{plat}_{raw_id}"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return f"{plat}_{digest}"


def _clean_artist_title(entry: dict[str, Any]) -> tuple[str, str]:
    track_name = entry.get("track")
    artist_name = entry.get("artist") or entry.get("creator") or entry.get("uploader") or entry.get("channel") or ""
    raw_title = (entry.get("title") or "").strip()
    if track_name and artist_name:
        return str(artist_name).strip(), str(track_name).strip()
    if " - " in raw_title:
        parts = raw_title.split(" - ", 1)
        artist_guess, title_guess = parts[0].strip(), parts[1].strip()
        # Strip common noise suffixes like (Official Audio), [Official Video], etc.
        title_guess = re.sub(
            r"\s*[\(\[](?:official\s*(?:music\s*)?(?:video|audio|lyric\s*video|visualizer|visualiser)|lyrics?|hd|4k|prod\.[^\)\]]+)[\)\]]",
            "",
            title_guess,
            flags=re.IGNORECASE,
        ).strip()
        return artist_guess or str(artist_name).strip(), title_guess or raw_title
    clean_title = re.sub(
        r"\s*[\(\[](?:official\s*(?:music\s*)?(?:video|audio|lyric\s*video|visualizer|visualiser)|lyrics?|hd|4k)[\)\]]",
        "",
        raw_title,
        flags=re.IGNORECASE,
    ).strip()
    artist_clean = re.sub(r"\s*-\s*Topic$", "", str(artist_name).strip(), flags=re.IGNORECASE)
    return artist_clean or "Unknown", clean_title or raw_title or "Untitled"


def _summary(entry: dict[str, Any], fallback_url: str = "") -> dict[str, Any]:
    cid = canonical_id(entry, fallback_url)
    url = entry.get("webpage_url") or entry.get("url") or fallback_url
    if url and not str(url).startswith("http") and cid.startswith("yt_"):
        vid = cid.removeprefix("yt_")
        url = f"https://music.youtube.com/watch?v={vid}"
    plat = "tiktok" if cid.startswith("tiktok_") else "youtube"
    artist, title = _clean_artist_title(entry)
    thumbs = entry.get("thumbnails") or []
    thumb = entry.get("thumbnail") or (thumbs[-1].get("url") if thumbs else None)
    duration = int(round(float(entry.get("duration") or 0)))
    return {
        "id": cid,
        "title": title,
        "artist": artist,
        "genre": (entry.get("genre") or ("tiktok" if plat == "tiktok" else "")).lower(),
        "duration": duration,
        "bpm": None,
        "key": None,
        "camelot": None,
        "plays": int(entry.get("view_count") or entry.get("like_count") or 0),
        "artwork": thumb,
        "permalink": str(url) if url else None,
        "platform": plat,
        "streamable": True,
    }


def _norm_song_key(artist: str, title: str) -> str:
    """Normalise artist+title so we don't queue both the Music Video and Lyric Video of the same song."""
    base = re.sub(r"[\(\[].*?[\)\]]", "", f"{artist} {title}".lower())
    base = re.sub(r"\b(?:ft|feat|featuring|x|vs|and)\b.*", "", base)
    return re.sub(r"[^a-z0-9]", "", base)[:36]


def _curation_score(summary: dict[str, Any], raw_entry: dict[str, Any], query: str) -> float:
    """Rank search results like a world-class DJ digging for an artist's biggest club-ready hits."""
    q_low = query.lower().strip()
    q_tokens = [w for w in re.split(r"\W+", q_low) if len(w) >= 2]
    artist_low = (summary.get("artist") or "").lower()
    title_low = (summary.get("title") or "").lower()
    raw_title = (raw_entry.get("title") or "").lower()
    uploader = (raw_entry.get("uploader") or raw_entry.get("channel") or "").lower()

    score = 0.0
    # 1. Popularity (log10 of plays/views so 5M views beats 500 views without drowning relevance)
    plays = max(int(summary.get("plays") or 0), 1)
    score += math.log10(plays) * 1.4

    # 2. Artist / Title match with query
    if q_low in artist_low or q_low in title_low:
        score += 5.0
    elif q_tokens:
        hits = sum(1 for w in q_tokens if w in artist_low or w in title_low or w in raw_title)
        score += 4.0 * (hits / len(q_tokens))

    # 3. Official Audio / Topic channel boost (cleanest mastering, no video skits)
    if "- topic" in uploader or "official audio" in raw_title:
        score += 3.5
    elif "official" in raw_title or "vevo" in uploader:
        score += 1.8

    # 4. Sweet-spot track duration (2.5 min to 8.5 min is ideal for club/amapiano/house tracks)
    dur = summary.get("duration") or 0
    if 150 <= dur <= 510:
        score += 2.0
    elif dur > 540:
        score -= 2.5

    return score


def search(query: str, limit: int = 20, min_seconds: int = MIN_SECONDS) -> list[dict[str, Any]]:
    """Search YouTube / YouTube Music for tracks matching `query`, or resolve a pasted URL directly."""
    query = query.strip()
    if not query:
        return []
    if is_url(query):
        info = resolve_url(query)
        return [info] if info else []

    import yt_dlp

    low = query.lower()
    wants_mix = any(w in low for w in ("mix", "set", "live", "podcast", "interview"))
    search_q = query if any(w in low for w in ("audio", "song", "lyrics", "official", "topic", "mix")) else f"{query} official audio"

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "noplaylist": True,
    }
    fetch_count = min(max(limit * 2, 30), 50)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        res = ydl.extract_info(f"ytsearch{fetch_count}:{search_q}", download=False)
    entries = (res or {}).get("entries") or []

    scored_items: list[tuple[float, dict[str, Any]]] = []
    seen_ids: set[str] = set()
    seen_songs: set[str] = set()

    for e in entries:
        if not e:
            continue
        raw_title = str(e.get("title") or "")
        if not wants_mix and _JUNK_RE.search(raw_title):
            continue
        s = _summary(e)
        if s["id"] in seen_ids:
            continue
        if s["duration"] and not (min_seconds <= s["duration"] <= MAX_SECONDS):
            continue
        if not wants_mix and _JUNK_RE.search(f"{s['artist']} {s['title']}"):
            continue
        song_key = _norm_song_key(s["artist"], s["title"])
        if song_key and song_key in seen_songs:
            continue
        seen_ids.add(s["id"])
        if song_key:
            seen_songs.add(song_key)
        scored_items.append((_curation_score(s, e, query), s))

    scored_items.sort(key=lambda item: item[0], reverse=True)
    return [s for _sc, s in scored_items[:limit]]


def resolve_url(url: str) -> dict[str, Any]:
    """Resolve metadata for a single YouTube, YouTube Music, or TikTok URL."""
    url = url.strip()
    if not is_url(url):
        raise ValueError("expected an http(s) URL")
    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError("could not resolve URL")
    if "entries" in info and info["entries"]:
        info = info["entries"][0]
    return _summary(info, fallback_url=url)


def safe_id(cid: str) -> str:
    if not re.fullmatch(r"(?:yt|tiktok)_[A-Za-z0-9_-]{3,40}", cid):
        raise ValueError("bad stream track id")
    return cid


def url_for_id(cid: str, permalink: str | None = None) -> str:
    if permalink and is_url(permalink):
        return permalink
    cid = safe_id(cid)
    if cid.startswith("yt_"):
        vid = cid.removeprefix("yt_")
        return f"https://www.youtube.com/watch?v={vid}"
    raise ValueError("TikTok tracks require a full URL to download")


def fetch(cid: str, cache_dir: Path, url: str | None = None, meta_hint: dict[str, Any] | None = None) -> tuple[Path, dict[str, Any]]:
    """Download audio for `cid` (or `url`) into `cache_dir` and return (audio_path, metadata_summary).
    Downloads native m4a/webm audio directly (skipping slow FFmpeg MP3 re-encoding) for 3-5x faster station prep."""
    import yt_dlp

    cid = safe_id(cid)
    target_url = url_for_id(cid, url)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Check if already downloaded on disk
    for ext in (".m4a", ".mp3", ".webm", ".opus", ".ogg", ".wav", ".flac"):
        existing = cache_dir / f"{cid}{ext}"
        if existing.exists() and existing.stat().st_size > 20_000:
            if meta_hint and meta_hint.get("title"):
                return existing, meta_hint
            try:
                meta = resolve_url(target_url)
            except Exception:
                meta = {"id": cid, "title": cid, "artist": "Unknown", "genre": "", "permalink": target_url, "artwork": None}
            return existing, meta

    outtmpl = str(cache_dir / f"{cid}.%(ext)s")
    ydl_opts = {
        # Prefer m4a (AAC) or webm/opus ~128-160kbps: fast download, native decode via PyAV, no slow re-encode
        "format": "bestaudio[ext=m4a][abr<=192]/bestaudio[ext=webm][abr<=192]/bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "concurrent_fragment_downloads": 4,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(target_url, download=True)
    if info and "entries" in info and info["entries"]:
        info = info["entries"][0]
    meta = _summary(info or {}, fallback_url=target_url)
    meta["id"] = cid

    for ext in (".m4a", ".webm", ".opus", ".mp3", ".ogg", ".wav", ".flac", ".mp4"):
        candidate = cache_dir / f"{cid}{ext}"
        if candidate.exists() and candidate.stat().st_size > 10_000:
            return candidate, meta
    # Fallback: check any file starting with cid
    for candidate in cache_dir.glob(f"{cid}.*"):
        if candidate.is_file() and candidate.stat().st_size > 10_000 and not candidate.name.endswith(".part"):
            return candidate, meta
    raise RuntimeError("audio download finished but output file was not found")


def cache_size(cache_dir: Path) -> int:
    if not cache_dir.exists():
        return 0
    return sum(p.stat().st_size for p in cache_dir.iterdir() if p.is_file())
