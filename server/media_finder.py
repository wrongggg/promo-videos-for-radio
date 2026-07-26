import os
import re
import subprocess

import numpy as np
import requests
import static_ffmpeg
import yt_dlp

from track import Track

static_ffmpeg.add_paths()

CLIP_DURATION = 10
MAX_SOURCE_DURATION = 900  # skip full sets/mixes
MIN_SOURCE_DURATION = 45

# YouTube increasingly demands sign-in/bot-check on individual videos.
# cookie_file (a Netscape-format cookies.txt, exported by the requesting user
# from their own browser) is threaded through every yt-dlp call below to
# authenticate as *their* session -- never a shared/operator identity, since
# this app is used by multiple people.
def _cookie_opts(cookie_file: str | None) -> dict:
    if cookie_file and os.path.exists(cookie_file):
        return {"cookiefile": cookie_file}
    return {}

VERSION_KEYWORDS = [
    "remix", "rmx", "mix", "edit", "re-edit", "reedit", "dub", "version",
    "vip", "flip", "rework", "bootleg", "mashup", "extended", "instrumental",
    "acapella", "acoustic", "unplugged", "demo", "session", "live",
]


ENERGY_SAMPLE_RATE = 8000


def _pick_clip_window(duration: float, length: float) -> tuple[float, float]:
    """Fallback heuristic (used when energy analysis isn't available): skip a
    likely intro rather than guessing the most energetic moment."""
    start = min(45.0, duration * 0.3)
    if start + length > duration:
        start = max(0.0, duration - length)
    return start, start + length


def _download_full_audio(url: str, out_stem: str, cookie_file: str | None = None) -> str | None:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": out_stem + ".%(ext)s",
        "noplaylist": True,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
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


def _decode_pcm(audio_path: str, sample_rate: int = ENERGY_SAMPLE_RATE) -> np.ndarray | None:
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", audio_path, "-f", "s16le", "-acodec", "pcm_s16le",
             "-ar", str(sample_rate), "-ac", "1", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
        )
        if not result.stdout:
            return None
        return np.frombuffer(result.stdout, dtype=np.int16)
    except Exception:
        return None


def _energetic_start(pcm: np.ndarray, sample_rate: int, window_length: float) -> float | None:
    """Find the `window_length`-second window with the highest average energy —
    a proxy for the drop/hook/chorus, rather than an arbitrary fixed offset.
    Skips the first/last 5% of the track (cold intro, outro fade/silence)."""
    hop = sample_rate  # 1-second resolution
    n_hops = len(pcm) // hop
    if n_hops < 3:
        return None

    samples = pcm[: n_hops * hop].astype(np.float64).reshape(n_hops, hop)
    rms = np.sqrt(np.mean(samples ** 2, axis=1) + 1e-9)

    window = max(1, round(window_length))
    if n_hops <= window:
        return None

    cumsum = np.cumsum(np.insert(rms, 0, 0.0))
    window_sums = cumsum[window:] - cumsum[:-window]  # sum of energy per possible start second

    margin = max(1, round(n_hops * 0.05))
    lo, hi = margin, len(window_sums) - margin
    if hi <= lo:
        lo, hi = 0, len(window_sums)

    best_start = lo + int(np.argmax(window_sums[lo:hi]))
    return float(best_start)


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())


def _extract_keywords(s: str) -> set:
    norm = _normalize(s)
    return {kw for kw in VERSION_KEYWORDS if kw in norm}


def _title_matches(track: Track, candidate_title: str) -> bool:
    """Reject any candidate that isn't clearly the exact same track/version.
    Never accept the original mix for a remix request, or vice versa."""
    cand_norm = _normalize(candidate_title)
    title_norm = _normalize(track.title)

    title_words = [w for w in title_norm.split() if len(w) > 2]
    if title_words and not all(w in cand_norm for w in title_words):
        return False

    qualifier = track.album or ""
    required = _extract_keywords(qualifier)
    if required:
        qualifier_norm = _normalize(qualifier)
        if qualifier_norm not in cand_norm and not required.issubset(_extract_keywords(candidate_title)):
            return False
    else:
        # No version specified on the tracklist -- reject anything that is
        # clearly a remix/edit/etc of the track rather than the plain version.
        if _extract_keywords(candidate_title):
            return False
    return True


def _search_query(track: Track) -> str:
    if track.album:
        return f"{track.artist} {track.title} {track.album}"
    return f"{track.artist} {track.title}"


def _search_youtube(query: str, n: int = 8, cookie_file: str | None = None) -> list[dict]:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "default_search": f"ytsearch{n}",
        "extract_flat": False,
        # one restricted/sign-in-gated result shouldn't sink the whole search --
        # skip it and keep whichever other results resolved fine.
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


def _download_range(url: str, start: float, end: float, out_stem: str, audio_only: bool, cookie_file: str | None = None) -> str | None:
    fmt = "bestaudio/best" if audio_only else "bestvideo[height<=960]+bestaudio/best[height<=960]"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": fmt,
        "outtmpl": out_stem + ".%(ext)s",
        "download_ranges": lambda info, ydl: [{"start_time": start, "end_time": end}],
        "force_keyframes_at_cuts": True,
        "noplaylist": True,
        **_cookie_opts(cookie_file),
    }
    if audio_only:
        ydl_opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}
        ]
    else:
        ydl_opts["merge_output_format"] = "mp4"
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


def _find_matching_candidate(track: Track, entries: list[dict]) -> dict | None:
    for entry in entries:
        if not entry:
            continue
        duration = entry.get("duration") or 0
        if not (MIN_SOURCE_DURATION <= duration <= MAX_SOURCE_DURATION):
            continue
        title = entry.get("title") or ""
        if _title_matches(track, title):
            return entry
    return None


def _best_window_start(url: str, out_dir: str, index: int, duration: float, window: float, cookie_file: str | None = None) -> float:
    """Downloads the full audio once to analyze energy, then deletes it. Falls
    back to the fixed heuristic if analysis fails for any reason (age-gated
    track, decode failure, silence-only, etc)."""
    full_stem = os.path.join(out_dir, f"track{index}_energyscan")
    full_path = _download_full_audio(url, full_stem, cookie_file=cookie_file)
    if not full_path:
        return _pick_clip_window(duration, window)[0]
    try:
        pcm = _decode_pcm(full_path)
        if pcm is None or len(pcm) == 0:
            return _pick_clip_window(duration, window)[0]
        start = _energetic_start(pcm, ENERGY_SAMPLE_RATE, window)
        if start is None:
            return _pick_clip_window(duration, window)[0]
        return min(start, max(0.0, duration - window))
    finally:
        if os.path.exists(full_path):
            os.remove(full_path)


def find_youtube_video_clip(
    track: Track, out_dir: str, index: int,
    clip_duration: int = CLIP_DURATION, audio_duration: int | None = None,
    cookie_file: str | None = None,
) -> dict | None:
    audio_duration = audio_duration or clip_duration
    window = max(clip_duration, audio_duration)
    entries = _search_youtube(_search_query(track) + " official", cookie_file=cookie_file)
    candidate = _find_matching_candidate(track, entries)
    if candidate is None:
        return None

    duration = candidate.get("duration") or (MIN_SOURCE_DURATION + window)
    url = candidate.get("webpage_url") or candidate.get("url")
    start = _best_window_start(url, out_dir, index, duration, window, cookie_file=cookie_file)
    end = start + window
    raw_stem = os.path.join(out_dir, f"track{index}_raw")
    raw_path = _download_range(url, start, end, raw_stem, audio_only=False, cookie_file=cookie_file)
    if not raw_path or not os.path.exists(raw_path):
        return None

    cropped_path = os.path.join(out_dir, f"track{index}_video.mp4")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", raw_path,
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
            "-an", "-t", str(clip_duration),
            cropped_path,
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    audio_path = os.path.join(out_dir, f"track{index}_audio.m4a")
    subprocess.run(
        ["ffmpeg", "-y", "-i", raw_path, "-vn", "-acodec", "aac", "-t", str(audio_duration), audio_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    os.remove(raw_path)

    return {
        "video": cropped_path if os.path.exists(cropped_path) else None,
        "audio": audio_path if os.path.exists(audio_path) else None,
    }


def find_youtube_audio_only(track: Track, out_dir: str, index: int, audio_duration: int = CLIP_DURATION, cookie_file: str | None = None) -> str | None:
    entries = _search_youtube(_search_query(track) + " audio", n=6, cookie_file=cookie_file)
    candidate = _find_matching_candidate(track, entries)
    if candidate is None:
        return None

    duration = candidate.get("duration") or (MIN_SOURCE_DURATION + audio_duration)
    url = candidate.get("webpage_url") or candidate.get("url")

    # One full-track download serves both the energy analysis and the final clip
    # (trimmed locally), instead of analyzing once and downloading a range again.
    full_stem = os.path.join(out_dir, f"track{index}_fullaudio_tmp")
    full_path = _download_full_audio(url, full_stem, cookie_file=cookie_file)
    if not full_path:
        return None

    pcm = _decode_pcm(full_path)
    start = _pick_clip_window(duration, audio_duration)[0]
    if pcm is not None and len(pcm) > 0:
        energetic = _energetic_start(pcm, ENERGY_SAMPLE_RATE, audio_duration)
        if energetic is not None:
            start = min(energetic, max(0.0, duration - audio_duration))

    audio_path = os.path.join(out_dir, f"track{index}_audio.m4a")
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start), "-i", full_path, "-t", str(audio_duration), "-acodec", "aac", audio_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    os.remove(full_path)
    return audio_path if os.path.exists(audio_path) else None


def find_artist_image(track: Track, out_dir: str, index: int) -> str | None:
    try:
        resp = requests.get(
            "https://itunes.apple.com/search",
            params={"term": f"{track.artist} {track.title}", "media": "music", "limit": 1},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
        if not results:
            return None
        art_url = results[0].get("artworkUrl100")
        if not art_url:
            return None
        art_url = art_url.replace("100x100bb", "600x600bb")
        img_resp = requests.get(art_url, timeout=10)
        img_resp.raise_for_status()
        img_path = os.path.join(out_dir, f"track{index}_art.jpg")
        with open(img_path, "wb") as f:
            f.write(img_resp.content)
        return img_path
    except Exception:
        return None


def process_track(
    track: Track, out_dir: str, index: int,
    clip_duration: int = CLIP_DURATION, audio_duration: int | None = None,
    cookie_file: str | None = None,
) -> dict:
    """audio_duration lets the caller request a longer audio bed than the visual
    clip (e.g. the last featured track, whose audio also needs to cover the
    outro card so the video is never silent). cookie_file, if given, should be
    the requesting user's own exported YouTube cookies.txt -- never shared
    across users."""
    audio_duration = audio_duration or clip_duration
    result = {"video": None, "audio": None, "image": None, "needs_manual_audio": False}

    video_result = find_youtube_video_clip(
        track, out_dir, index, clip_duration=clip_duration, audio_duration=audio_duration, cookie_file=cookie_file,
    )
    if video_result:
        result["video"] = video_result["video"]
        result["audio"] = video_result["audio"]

    if not result["video"]:
        result["image"] = find_artist_image(track, out_dir, index)

    if not result["audio"]:
        result["audio"] = find_youtube_audio_only(track, out_dir, index, audio_duration=audio_duration, cookie_file=cookie_file)

    if not result["audio"]:
        result["needs_manual_audio"] = True

    return result


if __name__ == "__main__":
    import tempfile

    t = Track("The Cure", "Pictures of You", album="Extended Dub Mix")
    with tempfile.TemporaryDirectory() as d:
        print(process_track(t, d, 0))
        print(os.listdir(d))
