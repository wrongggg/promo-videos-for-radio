"""Resolves each tracklist entry into the media a promo scene needs: an audio
clip, and something to look at.

Audio comes from the catalog preview chain in `providers` (iTunes, then
Deezer) -- 30-second clips published by the rights holders' own APIs, fetched
with a single HTTP GET, no API key and nothing for the user to upload.

With `client_fetch`, that GET is made by the user's browser instead of by this
process: resolution stops at the candidate and hands its `audio_url` upward, and
the browser posts the bytes back to /preview_audio for trimming. The user still
uploads nothing -- both CDNs send `access-control-allow-origin: *`, so the page
fetches the preview itself and the flow looks identical from the outside. The
point is that the copy is then made on the user's machine, from their session,
rather than by the server on their behalf. See app.py CLIENT_FETCH_AUDIO.

The version is never negotiable. A tracklist records what actually aired, so a
live take or a remix is a different recording, not a substitute. When no
provider carries the exact version the track resolves to artwork only and the
scene falls back to generative visuals.

yt-dlp remains available but is gated behind `allow_youtube`, which only the
operator's own session sets. Downloading from YouTube violates its terms of
service, so it must never run on behalf of a subscriber -- see app.py.
"""
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import static_ffmpeg

import audio_analysis
import providers
from providers import Candidate
from track import Track

static_ffmpeg.add_paths()

CLIP_DURATION = 10
MAX_SOURCE_DURATION = 900  # skip full sets/mixes
# How many covers the picker offers per track. Providers return 17-21 distinct ones,
# which is more browsing than anyone wants from a next/prev gallery.
MAX_IMAGE_OPTIONS = 10
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
    # Set instead of `audio` under client_fetch: the preview the browser should
    # fetch, and how many seconds the trim should keep once it posts the bytes back.
    audio_url: Optional[str] = None
    audio_seconds: Optional[float] = None
    # Seconds of audio beyond the scene itself -- the closing track's bed runs
    # under the outro card. Kept apart from audio_seconds because only the scene
    # portion gets rounded to a bar.
    audio_extra: float = 0.0
    # How long this scene should actually run. The requested duration rounded to
    # a whole number of bars of this particular track -- see _bar_aligned. Only
    # known once the audio has been analysed, so it travels back with the media.
    scene_seconds: Optional[float] = None
    needs_manual_audio: bool = False
    matched_label: str = ""
    release_note: str = ""
    sources: list[dict] = field(default_factory=list)
    # Covers the user could swap to, as URLs -- nothing here is on disk. Only the
    # chosen one is downloaded, which is what makes offering ten of them free.
    image_options: list[dict] = field(default_factory=list)

    def credit(self, cand: Candidate, kind: str) -> None:
        self.sources.append({
            "kind": kind,
            "source": cand.source,
            "matched": cand.label(),
            "url": cand.attribution_url,
            "license": cand.license_note,
        })

    def offer(self, cand: Candidate, kind: str, url: str) -> None:
        """Record an image the user could pick, without fetching it.

        Carries the same provenance credit() writes, so a later selection can be
        credited from what was captured here rather than re-querying the provider."""
        self.image_options.append({
            "id": f"art_{len(self.image_options)}",
            "kind": kind,
            "source": cand.source,
            "matched": cand.label(),
            "url": url,
            "thumb": providers.thumb_url(url, cand.source),
            "link": cand.attribution_url,
            "license": cand.license_note,
        })

    def to_dict(self) -> dict:
        # Kept key-compatible with what compose.py/app.py already consume.
        return {
            "audio": self.audio,
            "video": self.video,
            "image": self.artwork,
            "artwork": self.artwork,
            "audio_url": self.audio_url,
            "audio_seconds": self.audio_seconds,
            "audio_extra": self.audio_extra,
            "scene_seconds": self.scene_seconds,
            "needs_manual_audio": self.needs_manual_audio,
            "matched_label": self.matched_label,
            "release_note": self.release_note,
            "sources": self.sources,
            "image_options": self.image_options,
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


# How much a window's *lift* counts next to its loudness when picking what to
# keep. A hook is where a track arrives, and an arrival is usually preceded by a
# breakdown -- so scoring on loudness alone finds a loud sustained middle and
# walks straight past the drop. Weighted above 1 so a genuine arrival beats a
# slightly louder plateau, but not so high that a quiet track's least-quiet
# moment outranks a real chorus.
LIFT_WEIGHT = 1.6
# How far back "before" reaches when measuring that lift.
LIFT_LOOKBACK = 2.0
# A chosen start is pulled to a real onset within this distance. Kept under half
# a beat at 150 BPM, so snapping can only ever move the cut to the hit it was
# already next to -- never to the previous or next one.
SNAP_WINDOW = 0.18


def _pick_window(probe: dict, want_seconds: float) -> float:
    """Where to start the cut, in seconds, given audio_analysis.probe output.

    Two decisions. Which window to keep, scored by loudness *and* lift so the
    cut lands on an arrival rather than a plateau; then where exactly to start
    it, snapped to a real onset so the scene opens on a hit instead of part way
    through one."""
    fps = probe["fps"]
    energy = probe["energy"]
    n = len(energy)
    win = max(1, int(round(want_seconds * fps)))
    if n <= win:
        return 0.0

    # Cumulative sums make every candidate window a constant-time lookup; at
    # 100fps over 30s there are ~2000 candidates and scoring them one at a
    # time in Python is the difference between milliseconds and a second.
    cum = np.cumsum(np.insert(energy, 0, 0.0))
    starts = np.arange(0, n - win + 1)
    inside = (cum[starts + win] - cum[starts]) / win

    look = max(1, int(round(LIFT_LOOKBACK * fps)))
    before_start = np.maximum(0, starts - look)
    before_len = np.maximum(1, starts - before_start)
    before = (cum[starts] - cum[before_start]) / before_len
    # Only a rise counts. A window quieter than what preceded it is not an
    # arrival, but it shouldn't be penalised below a flat one either.
    lift = np.maximum(0.0, inside - before)

    score = inside + LIFT_WEIGHT * lift

    # Same margins as before: a cold intro and an outro fade are almost never
    # the right ten seconds, whatever they score.
    margin = max(1, int(round(n * 0.05)))
    lo, hi = margin, max(margin + 1, len(score) - margin)
    if hi <= lo:
        lo, hi = 0, len(score)
    start_frame = int(lo + int(np.argmax(score[lo:hi])))
    start = start_frame / fps

    # Snap to the nearest onset, but never past the end of the audio.
    onsets = probe.get("onsets") or []
    latest = max(0.0, probe["duration"] - want_seconds)
    candidates = [o for o in onsets if abs(o - start) <= SNAP_WINDOW and o <= latest]
    if candidates:
        start = min(candidates, key=lambda o: abs(o - start))
    return float(min(start, latest))


def _energetic_start(pcm: np.ndarray, sample_rate: int, window_length: float) -> Optional[float]:
    """Fallback for when probe() can't decode the source: the original
    highest-average-energy window, at one-second resolution and with no onset
    snapping. Worse cuts, but a cut."""
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


# A scene may stretch or shrink by this much to land on a bar line. Wide enough
# to reach the nearest bar at any tempo we detect, narrow enough that the promo's
# pacing is still the one the user picked.
BAR_FLEX = 0.25
# Below this, the tempo estimate is a guess -- rubato, ambient, spoken word. The
# scene keeps its requested length rather than being cut to an imaginary grid.
MIN_BEAT_CONFIDENCE = 0.35
BEATS_PER_BAR = 4


def _bar_aligned(probe: dict, target: float) -> float:
    """Round `target` to a whole number of bars, within BAR_FLEX.

    A cut on a bar line is a cut where the music was going to change anyway,
    which is what makes a scene boundary feel authored instead of arbitrary.
    Returns `target` unchanged whenever the tempo isn't trustworthy enough to
    be worth bending the pacing for."""
    bpm = probe.get("bpm")
    if not bpm or probe.get("beat_confidence", 0.0) < MIN_BEAT_CONFIDENCE:
        return target

    bar = BEATS_PER_BAR * 60.0 / bpm
    if bar <= 0.05:
        return target

    lo, hi = target * (1.0 - BAR_FLEX), target * (1.0 + BAR_FLEX)
    # Whole bars first; at slow tempos a bar can be most of a scene, so a half
    # bar is allowed as a fallback rather than giving up on alignment entirely.
    for unit in (bar, bar / 2.0):
        n = round(target / unit)
        for cand_n in (n, n - 1, n + 1):
            if cand_n < 1:
                continue
            cand = cand_n * unit
            if lo <= cand <= hi:
                return round(cand, 3)
    return target


def _trim_to_best_window(src: str, dest: str, scene_target: float,
                         extra: float = 0.0) -> tuple[Optional[str], float]:
    """Cut the best window out of an already-downloaded clip.

    Returns (path, scene_seconds). `scene_seconds` is the requested length
    rounded to a whole number of bars, and is what the scene should actually be
    laid out at; the file itself runs `scene_seconds + extra`, where extra
    covers the outro card for the closing track.

    Catalog previews are label-chosen and usually already sit on the hook, but
    they run 30s and a scene needs ~10s, so it still pays to pick the best
    window inside them -- to start it on a hit, and to end it on a bar line."""
    start = 0.0
    scene_seconds = scene_target
    probe = None
    try:
        probe = audio_analysis.probe(src)
    except Exception:
        probe = None

    if probe:
        scene_seconds = _bar_aligned(probe, scene_target)

    want = scene_seconds + extra
    if probe and probe["duration"] > want:
        start = _pick_window(probe, want)
    else:
        # probe() failed, or the source is too short to choose a window inside.
        pcm = _decode_pcm(src)
        if pcm is not None and len(pcm) > 0:
            available = len(pcm) / ENERGY_SAMPLE_RATE
            if available > want:
                found = _energetic_start(pcm, ENERGY_SAMPLE_RATE, want)
                if found is not None:
                    start = min(found, max(0.0, available - want))

    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start), "-i", src, "-t", str(want),
         "-acodec", "aac", "-b:a", "192k", dest],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if not (os.path.exists(dest) and os.path.getsize(dest) > 0):
        return None, scene_target
    return dest, scene_seconds


# --------------------------------------------------------------------------
# catalog preview path (the default for everyone)
# --------------------------------------------------------------------------

def _resolve_preview_audio(track: Track, out_dir: str, index: int, scene_target: float,
                           result: ResolvedMedia, collect_options: bool = False,
                           client_fetch: bool = False, extra: float = 0.0) -> bool:
    cand = providers.find_audio_candidate(track)
    if not cand or not cand.audio_url:
        return False

    if client_fetch:
        # Stop at the candidate. The browser fetches the preview and posts it to
        # /preview_audio, which runs the same trim this function would have --
        # including the bar alignment, which needs the audio to decide.
        result.audio_url = cand.audio_url
        result.audio_seconds = scene_target
        result.audio_extra = extra
    else:
        raw = os.path.join(out_dir, f"track{index}_preview_raw")
        downloaded = providers.download(cand.audio_url, raw)
        if not downloaded:
            return False

        dest = os.path.join(out_dir, f"track{index}_audio.m4a")
        trimmed, scene_seconds = _trim_to_best_window(downloaded, dest, scene_target, extra=extra)
        try:
            os.remove(downloaded)
        except OSError:
            pass

        if not trimmed:
            return False
        result.audio = trimmed
        result.scene_seconds = scene_seconds

    result.matched_label = cand.label()
    result.release_note = cand.release_note()
    result.credit(cand, "audio")

    # The audio provider usually carries usable artwork too -- take it now and
    # save a second round of lookups.
    if cand.artwork_url and not result.artwork:
        art = providers.download(cand.artwork_url, os.path.join(out_dir, f"track{index}_art.jpg"))
        if art:
            result.artwork = art
            result.credit(cand, "artwork")
            if collect_options:
                # Offered first so it is option 0 -- this is the cover actually on disk
                # and on screen, and the gallery has to open on the one in use. The
                # audio candidate isn't necessarily among find_image_candidates()'
                # results, so without this the current cover can be missing from its
                # own picker.
                result.offer(cand, "artwork", cand.artwork_url)
    return True


def _resolve_images(track: Track, out_dir: str, index: int, result: ResolvedMedia,
                    collect_options: bool = False) -> None:
    """Album art, from whichever provider has it. This is what the
    generative/audio-reactive scenes are built around, so it's worth checking
    every provider rather than stopping at the first.

    Release artwork only -- artist photos are not sourced; see
    providers.find_image_candidates for why.

    With collect_options, keep walking after the cover is downloaded to record the
    other covers as URLs for the picker. Image matching allows alternate releases, so
    the first hit is often a compilation or best-of sleeve rather than the single that
    aired -- the alternates are usually where the right one is. Nothing extra is
    fetched: the candidates come from the same cached searches, and only URLs are kept.

    Without it, the control flow is exactly what it was, early return included."""
    if result.artwork and not collect_options:
        return

    # Seeded with whatever the audio candidate already offered, so the cover in use
    # isn't listed twice.
    seen_urls = {_image_key(o["url"]) for o in result.image_options}
    pool = []
    for cand in providers.find_image_candidates(track):
        if not result.release_note:
            result.release_note = cand.release_note()
        if cand.artwork_url and not result.artwork:
            art = providers.download(cand.artwork_url, os.path.join(out_dir, f"track{index}_art.jpg"))
            if art:
                result.artwork = art
                result.credit(cand, "artwork")

        if collect_options:
            # The same sleeve comes back repeatedly within one provider's results and
            # again from the others; without deduping, the gallery is mostly repeats.
            key = _image_key(cand.artwork_url)
            if cand.artwork_url and key not in seen_urls:
                seen_urls.add(key)
                pool.append((cand, "artwork", cand.artwork_url))
        elif result.artwork:
            return

    if collect_options:
        for cand, kind, url in _pick_varied(pool, MAX_IMAGE_OPTIONS - len(result.image_options)):
            result.offer(cand, kind, url)


def _image_key(url: str) -> str:
    """Identify an image by its asset, not its URL.

    The same sleeve comes back at different sizes and with different query strings
    across providers -- iTunes encodes the size in the path, Deezer in the filename --
    so comparing whole URLs leaves near-duplicates in the gallery. Matching on the
    identifying part of the path collapses them without downloading anything to compare.
    """
    if not url:
        return ""
    path = url.split("?", 1)[0].rstrip("/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return url
    tail = parts[-1]
    # A size-only leaf (iTunes "3000x3000bb.jpg", Deezer "1000x1000-000000-80-0-0.jpg")
    # says nothing about which image it is -- the identity is the segment before it.
    if re.match(r"^\d+x\d+", tail) and len(parts) > 1:
        return parts[-2]
    return re.sub(r"^\d+x\d+[-\w]*\.", "", tail)


def _pick_varied(pool: list[tuple], limit: int) -> list[tuple]:
    """Take `limit` images spread across providers rather than the first N.

    Candidates arrive grouped by provider, so a plain head-of-list slice fills the whole
    gallery with one source -- ten near-identical iTunes releases, and the Deezer sleeve
    never seen. Round-robin keeps the browsing worthwhile."""
    if limit <= 0:
        return []
    buckets: dict[str, list] = {}
    for item in pool:
        buckets.setdefault(f"{item[0].source}:{item[1]}", []).append(item)
    picked = []
    while len(picked) < limit and any(buckets.values()):
        for key in list(buckets):
            if not buckets[key]:
                continue
            picked.append(buckets[key].pop(0))
            if len(picked) >= limit:
                break
    return picked


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
    collect_options: bool = False,
    client_fetch: bool = False,
) -> ResolvedMedia:
    """audio_duration lets the caller request a longer audio bed than the
    visual clip (e.g. the last featured track, whose audio also covers the
    outro card so the video is never silent).

    allow_youtube must only be true for the operator's own session.

    client_fetch leaves `audio` unset and reports `audio_url` instead; the track
    is pending, not failed, so `needs_manual_audio` stays false and the caller
    must not treat it as a dead end.
    """
    audio_duration = audio_duration or clip_duration
    # Only the scene itself is rounded to a bar; anything asked for beyond it
    # is a bed under the outro and must keep its exact length.
    extra = max(0.0, audio_duration - clip_duration)
    result = ResolvedMedia()

    got_audio = _resolve_preview_audio(track, out_dir, index, clip_duration, result,
                                       collect_options=collect_options,
                                       client_fetch=client_fetch, extra=extra)

    if not got_audio and allow_youtube:
        _resolve_youtube(track, out_dir, index, clip_duration, audio_duration, cookie_file, result)

    _resolve_images(track, out_dir, index, result, collect_options=collect_options)

    if not result.audio and not result.audio_url:
        result.needs_manual_audio = True
    return result


def process_track(
    track: Track, out_dir: str, index: int,
    clip_duration: int = CLIP_DURATION, audio_duration: Optional[int] = None,
    allow_youtube: bool = False, cookie_file: Optional[str] = None,
    collect_options: bool = False, client_fetch: bool = False,
) -> dict:
    """Dict-returning wrapper kept for the existing callers in compose/app."""
    return resolve_track(
        track, out_dir, index,
        clip_duration=clip_duration, audio_duration=audio_duration,
        allow_youtube=allow_youtube, cookie_file=cookie_file,
        collect_options=collect_options, client_fetch=client_fetch,
    ).to_dict()


def trim_uploaded_preview(src: str, dest: str, scene_target: float,
                          extra: float = 0.0) -> tuple[Optional[str], float]:
    """Public entry point for /preview_audio: the browser fetched the preview,
    so all that's left is the window pick and bar alignment this module would
    have done inline. Returns (path, scene_seconds)."""
    return _trim_to_best_window(src, dest, scene_target, extra=extra)


def find_audio_only(track: Track, out_dir: str, index: int, scene_target: float = CLIP_DURATION,
                    extra: float = 0.0, allow_youtube: bool = False,
                    cookie_file: Optional[str] = None) -> tuple[Optional[str], Optional[float]]:
    """Audio without the imagery lookup -- used to extend the closing track's
    bed over the outro card. Returns (path, scene_seconds); the re-cut picks its
    own window and so its own bar alignment, which the caller must adopt."""
    result = ResolvedMedia()
    if _resolve_preview_audio(track, out_dir, index, scene_target, result, extra=extra):
        return result.audio, result.scene_seconds
    if allow_youtube:
        total = scene_target + extra
        _resolve_youtube(track, out_dir, index, total, total, cookie_file, result)
    return result.audio, result.scene_seconds


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
                  f"manual={r.needs_manual_audio}")
