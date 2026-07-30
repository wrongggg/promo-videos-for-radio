"""Operator-only YouTube fallback.

Downloading from YouTube violates its terms of service. This module therefore
exists solely for the operator's own single-user sessions and must never be
reachable from a subscriber's job -- `media_finder.resolve_track` only imports
it when `allow_youtube` is set, and only the admin session sets that (app.py).

It is deliberately the *last* resort: the catalog preview chain in `providers`
resolves the overwhelming majority of tracks legitimately, and this only picks
up the residue that no licensed catalog carries in this territory.

Version matching reuses `providers.candidate_matches`, so the same rule holds
here as everywhere else -- the exact version in the tracklist, or nothing.
"""
import os
import subprocess
from typing import Optional

import yt_dlp

import providers
from providers import Candidate
from track import Track

MAX_SOURCE_DURATION = 900  # skip full sets/mixes
MIN_SOURCE_DURATION = 45


def _cookie_opts(cookie_file: Optional[str]) -> dict:
    if cookie_file and os.path.exists(cookie_file):
        return {"cookiefile": cookie_file}
    return {}


def _search(query: str, n: int = 8, cookie_file: Optional[str] = None) -> list[dict]:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "default_search": f"ytsearch{n}",
        "extract_flat": False,
        # one restricted/sign-in-gated result shouldn't sink the whole search
        "ignoreerrors": True,
        **_cookie_opts(cookie_file),
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
    except Exception:
        return []
    if not info:
        return []
    return [e for e in (info.get("entries") or []) if e]


def _pick(track: Track, entries: list[dict]) -> Optional[dict]:
    """Same strict version rule as the licensed providers."""
    for entry in entries:
        if not entry:
            continue
        duration = entry.get("duration") or 0
        if not (MIN_SOURCE_DURATION <= duration <= MAX_SOURCE_DURATION):
            continue
        title = entry.get("title") or ""
        # YouTube titles are unstructured, so hand the whole title in as both
        # title and version and let the keyword logic sort it out.
        cand = Candidate(
            source="youtube",
            artist=entry.get("uploader") or track.artist,
            title=title,
            version="",
        )
        # Uploader names are unreliable (topic channels, reuploads), so match on
        # the title alone -- but keep the impostor and version checks.
        cand.artist = track.artist
        if providers.candidate_matches(track, cand):
            return entry
    return None


def _download_range(url: str, start: float, end: float, out_stem: str,
                    cookie_file: Optional[str] = None) -> Optional[str]:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestvideo[height<=960]+bestaudio/best[height<=960]",
        "outtmpl": out_stem + ".%(ext)s",
        "download_ranges": lambda info, ydl: [{"start_time": start, "end_time": end}],
        "force_keyframes_at_cuts": True,
        "noplaylist": True,
        "merge_output_format": "mp4",
        **_cookie_opts(cookie_file),
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception:
        return None

    stem_dir = os.path.dirname(out_stem)
    stem_name = os.path.basename(out_stem)
    for fname in os.listdir(stem_dir):
        if fname.startswith(stem_name + "."):
            return os.path.join(stem_dir, fname)
    return None


def fetch_clip(track: Track, out_dir: str, index: int, clip_duration: int = 10,
               audio_duration: int = 10, cookie_file: Optional[str] = None) -> Optional[dict]:
    entries = _search(f"{track.artist} {track.title} {track.album or ''} official",
                      cookie_file=cookie_file)
    candidate = _pick(track, entries)
    if candidate is None:
        return None

    window = max(clip_duration, audio_duration)
    duration = candidate.get("duration") or (MIN_SOURCE_DURATION + window)
    url = candidate.get("webpage_url") or candidate.get("url")

    start = min(45.0, duration * 0.3)
    if start + window > duration:
        start = max(0.0, duration - window)

    raw_stem = os.path.join(out_dir, f"track{index}_yt_raw")
    raw_path = _download_range(url, start, start + window, raw_stem, cookie_file=cookie_file)
    if not raw_path or not os.path.exists(raw_path):
        return None

    video_path = os.path.join(out_dir, f"track{index}_video.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-i", raw_path,
         "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
         "-an", "-t", str(clip_duration), video_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    audio_path = os.path.join(out_dir, f"track{index}_audio.m4a")
    subprocess.run(
        ["ffmpeg", "-y", "-i", raw_path, "-vn", "-acodec", "aac",
         "-t", str(audio_duration), audio_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        os.remove(raw_path)
    except OSError:
        pass

    return {
        "video": video_path if os.path.exists(video_path) else None,
        "audio": audio_path if os.path.exists(audio_path) else None,
        "matched": candidate.get("title") or track.label(),
        "url": url,
    }
