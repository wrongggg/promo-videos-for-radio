"""Resolves each tracklist entry into the media a promo scene needs: an audio
clip, and something to look at.

Audio comes from the catalog preview chain in `providers` (iTunes, then
Deezer) -- 30-second clips published by the rights holders' own APIs, fetched
with a single HTTP GET, no API key and nothing for the user to upload.

The version is never negotiable. A tracklist records what actually aired, so a
live take or a remix is a different recording, not a substitute. When no
provider carries the exact version the track resolves to artwork only and the
scene falls back to generative visuals.

yt-dlp remains available but is gated behind `allow_youtube`, which only the
operator's own session sets. Downloading from YouTube violates its terms of
service, so it must never run on behalf of a subscriber -- see app.py.
"""
import os
import subprocess
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import static_ffmpeg

import providers
from providers import Candidate
from track import Track

static_ffmpeg.add_paths()

CLIP_DURATION = 10
MAX_SOURCE_DURATION = 900  # skip full sets/mixes
MIN_SOURCE_DURATION = 45

ENERGY_SAMPLE_RATE = 8000


@dataclass
class ResolvedMedia:
    """Everything one scene needs, plus where it came from.

    `sources` feeds the per-job manifest: a paid product needs to be able to
    say exactly which service each asset came from and under what terms."""
    audio: Optional[str] = None
    video: Optional[str] = None
    artwork: Optional[str] = None
    artist_image: Optional[str] = None
    needs_manual_audio: bool = False
    matched_label: str = ""
    sources: list[dict] = field(default_factory=list)

    def credit(self, cand: Candidate, kind: str) -> None:
        self.sources.append({
            "kind": kind,
            "source": cand.source,
            "matched": cand.label(),
            "url": cand.attribution_url,
            "license": cand.license_note,
        })

    def to_dict(self) -> dict:
        # Kept key-compatible with what compose.py/app.py already consume.
        return {
            "audio": self.audio,
            "video": self.video,
            "image": self.artwork or self.artist_image,
            "artwork": self.artwork,
            "artist_image": self.artist_image,
            "needs_manual_audio": self.needs_manual_audio,
            "matched_label": self.matched_label,
            "sources": self.sources,
        }


# --------------------------------------------------------------------------
# audio analysis -- pick the most energetic window inside a clip
# --------------------------------------------------------------------------

def _decode_pcm(audio_path: str, sample_rate: int = ENERGY_SAMPLE_RATE) -> Optional[np.ndarray]:
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


def _energetic_start(pcm: np.ndarray, sample_rate: int, window_length: float) -> Optional[float]:
    """Find the `window_length`-second window with the highest average energy --
    a proxy for the drop/hook/chorus, rather than an arbitrary fixed offset.
    Skips the first/last 5% (cold intro, outro fade/silence)."""
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
    window_sums = cumsum[window:] - cumsum[:-window]

    margin = max(1, round(n_hops * 0.05))
    lo, hi = margin, len(window_sums) - margin
    if hi <= lo:
        lo, hi = 0, len(window_sums)

    return float(lo + int(np.argmax(window_sums[lo:hi])))


def _trim_to_best_window(src: str, dest: str, want_seconds: float) -> Optional[str]:
    """Cut the most energetic `want_seconds` out of an already-downloaded clip.

    Catalog previews are label-chosen and usually already sit on the hook, but
    they run 30s and a scene needs ~10s, so it still pays to pick the best
    window inside them."""
    pcm = _decode_pcm(src)
    start = 0.0
    if pcm is not None and len(pcm) > 0:
        available = len(pcm) / ENERGY_SAMPLE_RATE
        if available > want_seconds:
            found = _energetic_start(pcm, ENERGY_SAMPLE_RATE, want_seconds)
            if found is not None:
                start = min(found, max(0.0, available - want_seconds))

    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start), "-i", src, "-t", str(want_seconds),
         "-acodec", "aac", "-b:a", "192k", dest],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return dest if os.path.exists(dest) and os.path.getsize(dest) > 0 else None


# --------------------------------------------------------------------------
# catalog preview path (the default for everyone)
# --------------------------------------------------------------------------

def _resolve_preview_audio(track: Track, out_dir: str, index: int, want_seconds: float,
                           result: ResolvedMedia) -> bool:
    cand = providers.find_audio_candidate(track)
    if not cand or not cand.audio_url:
        return False

    raw = os.path.join(out_dir, f"track{index}_preview_raw")
    downloaded = providers.download(cand.audio_url, raw)
    if not downloaded:
        return False

    dest = os.path.join(out_dir, f"track{index}_audio.m4a")
    trimmed = _trim_to_best_window(downloaded, dest, want_seconds)
    try:
        os.remove(downloaded)
    except OSError:
        pass

    if not trimmed:
        return False

    result.audio = trimmed
    result.matched_label = cand.label()
    result.credit(cand, "audio")

    # The audio provider usually carries usable artwork too -- take it now and
    # save a second round of lookups.
    if cand.artwork_url and not result.artwork:
        art = providers.download(cand.artwork_url, os.path.join(out_dir, f"track{index}_art.jpg"))
        if art:
            result.artwork = art
            result.credit(cand, "artwork")
    return True


def _resolve_images(track: Track, out_dir: str, index: int, result: ResolvedMedia) -> None:
    """Album art and an artist photo, from whichever providers have them.
    These are what the generative/audio-reactive scenes are built around, so
    it's worth checking every provider rather than stopping at the first."""
    if result.artwork and result.artist_image:
        return

    for cand in providers.find_image_candidates(track):
        if cand.artwork_url and not result.artwork:
            art = providers.download(cand.artwork_url, os.path.join(out_dir, f"track{index}_art.jpg"))
            if art:
                result.artwork = art
                result.credit(cand, "artwork")
        if cand.artist_image_url and not result.artist_image:
            pic = providers.download(
                cand.artist_image_url, os.path.join(out_dir, f"track{index}_artist.jpg")
            )
            if pic:
                result.artist_image = pic
                result.credit(cand, "artist_image")
        if result.artwork and result.artist_image:
            return


# --------------------------------------------------------------------------
# operator-only YouTube path
# --------------------------------------------------------------------------

def _resolve_youtube(track: Track, out_dir: str, index: int, clip_duration: int,
                     audio_duration: int, cookie_file: Optional[str],
                     result: ResolvedMedia) -> None:
    """Operator-only. Imported lazily so a deployment that never enables it
    doesn't even need yt-dlp installed."""
    try:
        import youtube_source
    except ImportError:
        return

    got = youtube_source.fetch_clip(
        track, out_dir, index,
        clip_duration=clip_duration, audio_duration=audio_duration,
        cookie_file=cookie_file,
    )
    if not got:
        return
    if got.get("video") and not result.video:
        result.video = got["video"]
    if got.get("audio") and not result.audio:
        result.audio = got["audio"]
    result.sources.append({
        "kind": "youtube",
        "source": "youtube",
        "matched": got.get("matched", track.label()),
        "url": got.get("url"),
        "license": "Operator-only source; not used for subscriber jobs.",
    })


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------

def resolve_track(
    track: Track, out_dir: str, index: int,
    clip_duration: int = CLIP_DURATION,
    audio_duration: Optional[int] = None,
    allow_youtube: bool = False,
    cookie_file: Optional[str] = None,
) -> ResolvedMedia:
    """audio_duration lets the caller request a longer audio bed than the
    visual clip (e.g. the last featured track, whose audio also covers the
    outro card so the video is never silent).

    allow_youtube must only be true for the operator's own session.
    """
    audio_duration = audio_duration or clip_duration
    want = max(clip_duration, audio_duration)
    result = ResolvedMedia()

    got_audio = _resolve_preview_audio(track, out_dir, index, want, result)

    if not got_audio and allow_youtube:
        _resolve_youtube(track, out_dir, index, clip_duration, audio_duration, cookie_file, result)

    _resolve_images(track, out_dir, index, result)

    if not result.audio:
        result.needs_manual_audio = True
    return result


def process_track(
    track: Track, out_dir: str, index: int,
    clip_duration: int = CLIP_DURATION, audio_duration: Optional[int] = None,
    allow_youtube: bool = False, cookie_file: Optional[str] = None,
) -> dict:
    """Dict-returning wrapper kept for the existing callers in compose/app."""
    return resolve_track(
        track, out_dir, index,
        clip_duration=clip_duration, audio_duration=audio_duration,
        allow_youtube=allow_youtube, cookie_file=cookie_file,
    ).to_dict()


def find_audio_only(track: Track, out_dir: str, index: int, audio_duration: int = CLIP_DURATION,
                    allow_youtube: bool = False, cookie_file: Optional[str] = None) -> Optional[str]:
    """Audio without the imagery lookup -- used to extend the closing track's
    bed over the outro card."""
    result = ResolvedMedia()
    if _resolve_preview_audio(track, out_dir, index, audio_duration, result):
        return result.audio
    if allow_youtube:
        _resolve_youtube(track, out_dir, index, audio_duration, audio_duration, cookie_file, result)
    return result.audio


if __name__ == "__main__":
    import tempfile

    for t in [
        Track("The Cure", "Pictures of You", album="Extended Dub Mix"),
        Track("Kendrick Lamar", "Not Like Us"),
        Track("Noga Erez", "VIEWS"),
    ]:
        with tempfile.TemporaryDirectory() as d:
            r = resolve_track(t, d, 0)
            print(f"{t.label()!r:55s} -> matched={r.matched_label!r}")
            print(f"    audio={bool(r.audio)} artwork={bool(r.artwork)} "
                  f"artist_img={bool(r.artist_image)} manual={r.needs_manual_audio}")
