"""Catalog providers -- where audio clips and imagery actually come from.

Each provider wraps one public music catalog API and returns normalized
`Candidate` objects. `media_finder` picks the first candidate that genuinely
matches the requested track and downloads from it.

Legal posture, in short:

  * iTunes / Deezer expose 30-second preview clips and artwork specifically so
    third parties can surface and link back to their catalogs. Using them to
    promote a radio show that played the track is the promotional use those
    APIs exist for. Every candidate carries a `license_note` and an
    `attribution_url` so the composition can credit and link back -- that
    link-back is what keeps the use inside the intent of the API.
  * yt-dlp is *not* in this module. It stays in media_finder behind an
    operator-only flag, because downloading from YouTube violates its terms of
    service and must never run on behalf of a paying subscriber.

Neither preview provider needs an API key, which is why this whole chain works
without asking anyone to upload anything.
"""
import json
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

import requests

from track import Track

_DATA_DIR = os.environ.get("DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
_CACHE_PATH = os.path.join(_DATA_DIR, "catalog_cache.json")

USER_AGENT = "RadioPromoVideos/1.0 (+radio promo generator; contact via app)"
TIMEOUT = 15

# iTunes soft-limits uncredited callers to roughly 20 requests/minute. A shared
# throttle plus the on-disk cache below keeps a busy multi-track job well under
# that without anyone needing to think about it.
_ITUNES_MIN_INTERVAL = 3.2
_MUSICBRAINZ_MIN_INTERVAL = 1.1  # MusicBrainz asks for <=1 req/sec, strictly


@dataclass
class Candidate:
    """One catalog hit, normalized across providers."""
    source: str                      # "itunes" | "deezer" | "musicbrainz"
    artist: str
    title: str
    version: str = ""                # remix/edit qualifier, structured when available
    is_alternate_version: bool = False  # right artist + track, but not the version asked for
    duration: Optional[float] = None  # full-track duration in seconds, if known
    audio_url: Optional[str] = None   # direct URL to a downloadable preview clip
    audio_seconds: Optional[float] = None  # length of that preview
    artwork_url: Optional[str] = None      # release/album art, largest available
    artist_image_url: Optional[str] = None
    album: str = ""                  # release the track appears on
    year: str = ""                   # release year, 4 digits
    attribution_url: Optional[str] = None  # link back to the track on the service
    license_note: str = ""
    raw: dict = field(default_factory=dict)

    def label(self) -> str:
        v = f" ({self.version})" if self.version else ""
        return f"{self.artist} - {self.title}{v}"

    def release_note(self) -> str:
        """A short factual line about the release, for scenes where the model
        found no trivia worth stating. Drawn straight from catalog metadata, so
        it is always true -- unlike asking a model to fill the gap."""
        album = (self.album or "").strip()
        # Singles are usually filed under "<Title> - Single"; repeating the
        # track name back at the viewer says nothing.
        if album.lower().rstrip(" -").endswith("single") or not album:
            return f"Released {self.year}" if self.year else ""
        return f"From {album}" + (f" ({self.year})" if self.year else "")


# --------------------------------------------------------------------------
# shared HTTP plumbing: one session, per-host throttle, persistent cache
# --------------------------------------------------------------------------

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT

_lock = threading.Lock()
_last_call: dict[str, float] = {}
_cache: Optional[dict] = None


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(_CACHE_PATH) as f:
                _cache = json.load(f)
        except Exception:
            _cache = {}
    return _cache


def _save_cache() -> None:
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        tmp = _CACHE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_cache, f)
        os.replace(tmp, _CACHE_PATH)
    except Exception:
        pass  # cache is an optimization; never let it break a render


def _throttle(host: str, min_interval: float) -> None:
    with _lock:
        elapsed = time.monotonic() - _last_call.get(host, 0.0)
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _last_call[host] = time.monotonic()


def _get_json(host: str, url: str, params: dict, min_interval: float = 0.0) -> Optional[dict]:
    """Cached, throttled GET. Search results are stable enough to cache
    indefinitely -- a track's preview URL doesn't change day to day."""
    # The URL is part of the key, not just host+params: lookup-by-id endpoints put the
    # identifier in the path and often send identical params, so keying on params alone
    # makes every such call collide onto whichever one ran first.
    key = f"{host}:{url}:{json.dumps(params, sort_keys=True, ensure_ascii=False)}"
    cache = _load_cache()
    if key in cache:
        return cache[key]

    if min_interval:
        _throttle(host, min_interval)
    try:
        resp = _session.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    with _lock:
        cache[key] = data
        _save_cache()
    return data


# --------------------------------------------------------------------------
# text matching -- shared by every provider
# --------------------------------------------------------------------------

VERSION_KEYWORDS = [
    "remix", "rmx", "mix", "edit", "re-edit", "reedit", "dub", "version",
    "vip", "flip", "rework", "bootleg", "mashup", "extended", "instrumental",
    "acapella", "acoustic", "unplugged", "demo", "session", "live",
]

# Cheap words that shouldn't count toward an artist match on their own --
# otherwise "The Cure" would match "The Doors" on "the".
_ARTIST_STOPWORDS = {"the", "and", "feat", "featuring", "ft", "with", "presents", "pres", "vs"}


def normalize(s: str) -> str:
    """Casefold, strip accents, collapse punctuation. Keeps non-Latin scripts
    (Hebrew, Cyrillic, CJK) intact -- the app ships a Hebrew mode and Hebrew
    titles must still match."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.casefold()
    return re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)


def _words(s: str, minlen: int = 3) -> list[str]:
    return [w for w in normalize(s).split() if len(w) >= minlen]


def extract_version_keywords(*parts: str) -> set:
    blob = normalize(" ".join(p for p in parts if p))
    return {kw for kw in VERSION_KEYWORDS if kw in blob}


def artist_matches(requested: str, candidate: str) -> bool:
    """Guards against the failure mode where a search for an obscure track
    returns a completely different artist with a vaguely similar name --
    e.g. "Batu" resolving to "Bkabytruth & Bau Marlo".

    Collaborations are matched per-artist. A tracklist may credit
    "Head High & Cassy" while the catalog credits only "Head High", or lists
    the pair in the other order, or puts one of them in a "feat." -- so any one
    side matching is enough. Requiring the full combined string would reject
    almost every collab."""
    parts = [p.strip() for p in re.split(r"\s+&\s+|\s+\+\s+", requested or "") if p.strip()]
    if len(parts) > 1:
        return any(_artist_part_matches(p, candidate) for p in parts)
    return _artist_part_matches(requested, candidate)


def _artist_part_matches(requested: str, candidate: str) -> bool:
    req_words = [w for w in _words(requested, 2) if w not in _ARTIST_STOPWORDS]
    cand_norm = normalize(candidate)
    if not req_words:
        return True
    # Compilations and features list several artists, so require most of the
    # requested name to be present rather than the candidate matching exactly.
    hits = sum(1 for w in req_words if w in cand_norm)
    return hits >= max(1, len(req_words) - (1 if len(req_words) > 2 else 0))


# Versions that are not the artist's own performance. These must never be
# substituted for a requested track -- a karaoke backing track or an 8-bit
# cover in a radio promo is worse than no audio at all.
_IMPOSTOR_KEYWORDS = [
    "karaoke", "originally performed", "tribute", "cover version", "8 bit",
    "8bit", "made popular by", "in the style of", "backing track", "as made famous",
]


def is_impostor(cand: Candidate) -> bool:
    blob = normalize(f"{cand.artist} {cand.title} {cand.version}")
    return any(normalize(k) in blob for k in _IMPOSTOR_KEYWORDS)


def candidate_matches(track: Track, cand: Candidate, allow_alternate: bool = False) -> bool:
    """Reject anything that isn't clearly the same track *and* the same
    version. Never accept the original mix for a remix request, or vice versa.

    With allow_alternate, the version requirement is relaxed -- but only to a
    different version *by the same artist* (an official live take or remix),
    never to a cover or karaoke. Callers must surface that substitution."""
    if is_impostor(cand):
        return False
    if not artist_matches(track.artist, cand.artist):
        return False

    cand_title_blob = normalize(f"{cand.title} {cand.version}")
    for w in _words(track.title):
        if w not in cand_title_blob:
            return False

    qualifier = track.album or ""
    required = extract_version_keywords(qualifier)
    if required:
        # A specific version was asked for -- the candidate must carry it,
        # either verbatim or via the same version keywords.
        if normalize(qualifier).strip() in cand_title_blob:
            return True
        if required.issubset(extract_version_keywords(cand.title, cand.version)):
            return True
        return allow_alternate

    # No version requested -- reject remixes/edits/live takes of the track.
    if not extract_version_keywords(cand.title, cand.version):
        return True
    return allow_alternate


# --------------------------------------------------------------------------
# iTunes / Apple Music
# --------------------------------------------------------------------------

ITUNES_LICENSE = (
    "30s preview clip and artwork from the public iTunes Search API, used to "
    "promote and link back to the release on Apple Music."
)

_ITUNES_VERSION_RE = re.compile(r"[\(\[]([^\)\]]*)[\)\]]\s*$")


def _split_itunes_version(track_name: str) -> tuple[str, str]:
    """iTunes bakes the version into trackName -- "Vocoder [Club Mix]".
    Pull it out so it can be compared as structured data, like Deezer's."""
    m = _ITUNES_VERSION_RE.search(track_name or "")
    if not m:
        return (track_name or "").strip(), ""
    inner = m.group(1).strip()
    if extract_version_keywords(inner):
        return track_name[: m.start()].strip(), inner
    return (track_name or "").strip(), ""


def thumb_url(url: str, source: str, size: int = 300) -> str:
    """A small version of an image URL, for browsing a gallery without downloading.

    Every provider encodes the size in the URL, so a thumbnail costs a string rewrite
    rather than a fetch -- which is what lets the cover picker offer ten alternates per
    track at no bandwidth or disk cost. An unrecognised shape falls back to the original:
    a needlessly large thumbnail is a slow gallery, a broken rewrite is a blank one."""
    if not url:
        return url
    if source == "itunes":
        return upscale_itunes_art(url, size)
    if source == "deezer":
        # .../1000x1000-000000-80-0-0.jpg
        return re.sub(r"/\d+x\d+(-[\d-]+)?\.jpg$", f"/{size}x{size}\\1.jpg", url)
    if source == "musicbrainz":
        # .../release/<mbid>/front-1200
        return re.sub(r"/front-\d+$", f"/front-{min(size, 500)}", url)
    return url


def upscale_itunes_art(url: str, size: int = 3000) -> str:
    """iTunes hands back a 100x100 thumbnail but will serve the same asset at
    up to 3000x3000 -- worth taking for a 1080x1920 frame with a Ken Burns move."""
    return re.sub(r"/\d+x\d+bb\.(jpg|png)$", f"/{size}x{size}bb.jpg", url or "")


def search_itunes(track: Track, limit: int = 15) -> list[Candidate]:
    term = " ".join(x for x in (track.artist, track.title, track.album) if x)
    data = _get_json(
        "itunes", "https://itunes.apple.com/search",
        {"term": term, "media": "music", "entity": "song", "limit": limit},
        min_interval=_ITUNES_MIN_INTERVAL,
    )
    if not data:
        return []

    out = []
    for r in data.get("results") or []:
        if not r.get("previewUrl"):
            continue
        title, version = _split_itunes_version(r.get("trackName") or "")
        out.append(Candidate(
            source="itunes",
            artist=r.get("artistName") or "",
            title=title,
            version=version,
            duration=(r.get("trackTimeMillis") or 0) / 1000.0 or None,
            audio_url=r.get("previewUrl"),
            audio_seconds=30.0,
            artwork_url=upscale_itunes_art(r.get("artworkUrl100") or ""),
            album=r.get("collectionName") or "",
            year=(r.get("releaseDate") or "")[:4],
            attribution_url=r.get("trackViewUrl"),
            license_note=ITUNES_LICENSE,
            raw=r,
        ))
    return out


# --------------------------------------------------------------------------
# Deezer
# --------------------------------------------------------------------------

DEEZER_LICENSE = (
    "30s preview clip, cover art and artist photo from the public Deezer API, "
    "used to promote and link back to the release on Deezer."
)


def search_deezer(track: Track, limit: int = 15) -> list[Candidate]:
    q = " ".join(x for x in (track.artist, track.title, track.album) if x)
    data = _get_json("deezer", "https://api.deezer.com/search", {"q": q, "limit": limit})
    if not data:
        return []

    out = []
    for r in data.get("data") or []:
        if not r.get("preview"):
            continue
        artist = r.get("artist") or {}
        album = r.get("album") or {}
        out.append(Candidate(
            source="deezer",
            artist=artist.get("name") or "",
            # Deezer separates the base title from its version qualifier, which
            # is a far cleaner remix signal than regexing it out of the title.
            title=r.get("title_short") or r.get("title") or "",
            version=(r.get("title_version") or "").strip("()[] "),
            duration=r.get("duration") or None,
            audio_url=r.get("preview"),
            audio_seconds=30.0,
            artwork_url=album.get("cover_xl") or album.get("cover_big"),
            artist_image_url=artist.get("picture_xl") or artist.get("picture_big"),
            album=album.get("title") or "",
            year=(r.get("release_date") or album.get("release_date") or "")[:4],
            attribution_url=r.get("link"),
            license_note=DEEZER_LICENSE,
            raw=r,
        ))
    return out


# --------------------------------------------------------------------------
# Cover Art Archive (via MusicBrainz) -- artwork only
# --------------------------------------------------------------------------

CAA_LICENSE = (
    "Cover art from the Cover Art Archive via MusicBrainz (open data), used to "
    "illustrate the release."
)


MB_FACT_MIN_SCORE = 90  # MusicBrainz scores 0-100; below this the match is a guess


def recording_facts(track: Track) -> Optional[dict]:
    """Structured catalogue facts for one recording, or None if there's no confident
    match. One request (the search endpoint already carries artist credits and the
    first-release date), so a whole tracklist costs one throttled call per track.

    This exists to keep the curator honest. Left to its own recollection the model will
    confidently invent a feature credit -- it claimed Peggy Gou's "It Makes You Forget"
    was built on a Lenny Kravitz vocal, which it is not. MusicBrainz says the recording
    is credited to Peggy Gou alone, and a few hundred tokens of that beats paying for
    web search to relearn it (search results cost ~$0.24/call in input tokens; this is
    free)."""
    data = _get_json(
        "musicbrainz", "https://musicbrainz.org/ws/2/recording",
        {"query": f'artist:"{track.artist}" AND recording:"{track.title}"',
         "fmt": "json", "limit": 5},
        min_interval=_MUSICBRAINZ_MIN_INTERVAL,
    )
    recs = [r for r in ((data or {}).get("recordings") or [])
            if (r.get("score") or 0) >= MB_FACT_MIN_SCORE]
    if not recs:
        return None

    # Several pressings of one track all score 100 -- the single, the album cut, a
    # remaster, a compilation appearance -- and their first-release-dates disagree
    # wildly (Four Tet's "Baby" offers 2020-01-23, 2026-05-01 and null). Taking the
    # top hit gets an arbitrary one, so take the EARLIEST instead: a re-release is
    # always later than the original, never earlier. Year-only values like "2018"
    # sort before "2018-05-04", which is the right answer at lower precision.
    dates = sorted(d for d in (r.get("first-release-date") for r in recs) if d)
    # Credits, unlike dates, agree across every pressing, so the top hit is fine --
    # and they are the reason this function exists.
    credited = [c.get("name") for c in (recs[0].get("artist-credit") or []) if c.get("name")]
    return {
        "title": recs[0].get("title") or track.title,
        "credited": credited,
        "first_release": dates[0] if dates else "",
    }


def facts_for_tracks(tracks: list[Track]) -> dict:
    """recording_facts across a tracklist, keyed like the rest of the app
    (artist_lower, title_lower). Best-effort: a provider hiccup drops that track's
    facts rather than failing the job."""
    out = {}
    for t in tracks:
        try:
            f = recording_facts(t)
        except Exception:
            f = None
        if f:
            out[(t.artist.lower(), t.title.lower())] = f
    return out


def search_cover_art(track: Track) -> list[Candidate]:
    """Artwork fallback for vinyl-only / small-label releases the streaming
    catalogs miss. No audio -- MusicBrainz is metadata only.

    NOTE: the URL is constructed for every release found, but the Cover Art Archive
    only holds art for some of them -- measured around 40% of these 404. That is
    invisible in the download path (a failed fetch just moves to the next candidate),
    but it matters to the cover picker, which offers these as remote URLs it never
    fetches. Any UI showing them must drop options whose image fails to load rather
    than render a blank slot. Verifying here instead would cost a request per option,
    which is exactly the cost the picker is designed to avoid."""
    query = f'artist:"{track.artist}" AND recording:"{track.title}"'
    data = _get_json(
        "musicbrainz", "https://musicbrainz.org/ws/2/recording",
        {"query": query, "fmt": "json", "limit": 3},
        min_interval=_MUSICBRAINZ_MIN_INTERVAL,
    )
    if not data:
        return []

    out = []
    for rec in data.get("recordings") or []:
        credit = rec.get("artist-credit") or []
        artist = credit[0].get("name") if credit else ""
        for rel in (rec.get("releases") or [])[:2]:
            rid = rel.get("id")
            if not rid:
                continue
            out.append(Candidate(
                source="musicbrainz",
                artist=artist or track.artist,
                title=rec.get("title") or track.title,
                artwork_url=f"https://coverartarchive.org/release/{rid}/front-1200",
                attribution_url=f"https://musicbrainz.org/release/{rid}",
                license_note=CAA_LICENSE,
                raw=rel,
            ))
    return out


# --------------------------------------------------------------------------
# provider chain
# --------------------------------------------------------------------------

AUDIO_PROVIDERS = [
    ("itunes", search_itunes),
    ("deezer", search_deezer),
]

IMAGE_PROVIDERS = [
    ("itunes", search_itunes),
    ("deezer", search_deezer),
    ("musicbrainz", search_cover_art),
]


def find_audio_candidate(track: Track, allow_alternate: bool = False) -> Optional[Candidate]:
    """First provider with an exact-version match wins.

    A tracklist is a factual record of what actually aired, so the version is
    not negotiable: a live take, a remix, or an edit is a *different recording*
    than the one the show played. When no provider has the exact version we
    return None and the track falls back to artwork-plus-generative visuals --
    skipping a track is always better than misrepresenting one.

    `allow_alternate` exists only so a caller can explicitly opt into a
    same-artist substitution; nothing in the app turns it on today, and
    anything it returns is marked `is_alternate_version` so it can never be
    presented as the original."""
    results = {name: search(track) for name, search in AUDIO_PROVIDERS}

    for name, _search in AUDIO_PROVIDERS:
        for cand in results[name]:
            if cand.audio_url and candidate_matches(track, cand):
                return cand

    if not allow_alternate:
        return None

    for name, _search in AUDIO_PROVIDERS:
        for cand in results[name]:
            if cand.audio_url and candidate_matches(track, cand, allow_alternate=True):
                cand.is_alternate_version = True
                return cand
    return None


def find_image_candidates(track: Track) -> list[Candidate]:
    """All matching candidates that carry imagery, best-provider-first, so the
    caller can take album art from one and an artist photo from another.

    Unlike audio, imagery tolerates an alternate version: when the exact
    version isn't in any catalog the track still needs something on screen, and
    a different release by the same artist is still a true picture of that
    artist. Impostors (karaoke, covers) are rejected here exactly as they are
    for audio."""
    out = []
    for _name, search in IMAGE_PROVIDERS:
        for cand in search(track):
            if not (cand.artwork_url or cand.artist_image_url):
                continue
            if candidate_matches(track, cand, allow_alternate=True):
                out.append(cand)
    return out


def download(url: str, dest: str) -> Optional[str]:
    try:
        resp = _session.get(url, timeout=TIMEOUT, stream=True)
        resp.raise_for_status()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(65536):
                f.write(chunk)
        return dest if os.path.getsize(dest) > 0 else None
    except Exception:
        return None
