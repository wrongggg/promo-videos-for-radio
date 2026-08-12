import json
import os
import re
import secrets
import shutil
import threading
import time
import traceback
import uuid
from functools import wraps

from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, send_from_directory, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

import access
import accounts
import analytics
import audio_analysis
import compose
import curator
import languages
import media_finder
import paddle
import providers
import styles
import visuals
from track import Track, parse_tracklist

load_dotenv()

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# DATA_DIR points at a persistent volume when hosted (e.g. Railway, whose
# container filesystem otherwise resets on every deploy). Falls back to the
# project dir for local dev, unchanged from before.
DATA_DIR = os.environ.get("DATA_DIR", PROJECT_DIR)
RENDERS_DIR = os.path.join(DATA_DIR, "renders")
THEMES_PATH = os.path.join(DATA_DIR, "themes.json")
ENV_PATH = os.path.join(PROJECT_DIR, ".env")
os.makedirs(RENDERS_DIR, exist_ok=True)

# How long a finished job's files stay on disk. Nothing cleaned up renders/
# before this, and a finished job leaves roughly 37 MB there: a ~19 MB master, a
# watermarked copy of it, and the per-track clips that were downloaded to build
# them. On a mounted volume that is billed and it grows without bound -- it was
# on track to become the single largest running cost of the whole tool.
#
# A week is well past the point anyone comes back for a promo about an episode
# that has already aired. Set 0 to keep everything forever.
RENDER_RETENTION_DAYS = float(os.environ.get("RENDER_RETENTION_DAYS", "7"))
_prune_lock = threading.Lock()

# Branding, in one place because it appears in the wordmark, the legal pages and
# (via tools/make_watermark.py) burned into every free video. Always the full
# string including the suffix -- "onrepeat" alone is a common phrase and doesn't
# identify us, the domain does.
BRAND_NAME = os.environ.get("BRAND_NAME", "onrepeat.mov")
# Paddle's domain review requires the seller's legal name in the T&Cs and a
# working contact address on the site. Both must be set before submitting.
LEGAL_ENTITY_NAME = os.environ.get("LEGAL_ENTITY_NAME", "")
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "")

# The paywall as a single switch: watermarking, and the credit spend on export.
#
# OFF BY DEFAULT, deliberately. Watermarking only makes sense once there is a
# way to pay to remove it, and until Paddle is live in production there isn't
# one -- shipping the mark without the checkout would brand every video with no
# escape hatch, which is worse than free. Sign-in (GOOGLE_CLIENT_ID) and the buy
# buttons (PADDLE_CLIENT_TOKEN) are already env-gated on their own credentials,
# so this only has to cover the two places that would otherwise fire regardless.
#
# Set PAYWALL=1 alongside the Paddle vars to turn the commercial layer on. The
# code it gates is fully built and tested (tests/test_download_gate.py,
# tests/test_credits.py) -- this is a switch, not a stub.
PAYWALL = (os.environ.get("PAYWALL", "") or "").strip().lower() in ("1", "true", "yes", "on")

# The renderer serves the composition with PROJECT_DIR as its document root, so
# every media src in index.html has to name a path *inside* the project. With
# DATA_DIR on a mounted volume the job media sits outside it, and the obvious
# relpath produces "../data/renders/<job>/track0_audio.m4a" -- which the
# renderer's lint rejects (invalid_parent_traversal_in_asset_path) and Studio
# preview 404s on. Bridging the volume back in as PROJECT_DIR/renders makes the
# hosted src identical to the local-dev one: "renders/<job>/track0_audio.m4a".
RENDERS_LINK = os.path.join(PROJECT_DIR, "renders")


def _bridge_renders_dir() -> bool:
    """Expose RENDERS_DIR at PROJECT_DIR/renders. No-op in local dev, where
    DATA_DIR is the project dir and the two are already the same path."""
    if os.path.realpath(RENDERS_LINK) == os.path.realpath(RENDERS_DIR):
        return True
    if os.path.islink(RENDERS_LINK):
        os.unlink(RENDERS_LINK)  # stale link from an earlier DATA_DIR
    elif os.path.exists(RENDERS_LINK):
        # A leftover real directory from a run without DATA_DIR set. Never
        # delete it -- it may hold someone's renders -- but say so loudly,
        # because every composition built from here on carries broken paths.
        print(f"WARNING: {RENDERS_LINK} is a real directory but DATA_DIR points at "
              f"{DATA_DIR}. Move it aside so the volume can be linked in; until then "
              f"compositions will reference media above the project root.", flush=True)
        return False
    try:
        os.symlink(RENDERS_DIR, RENDERS_LINK)
    except OSError as e:
        # A read-only project dir or a filesystem without symlinks. Degraded,
        # not fatal: renders still work (the renderer copies out-of-project
        # assets), but lint errors and Studio preview 404s come back.
        print(f"WARNING: could not link {RENDERS_DIR} to {RENDERS_LINK} ({e}); "
              f"compositions will reference media above the project root.", flush=True)
        return False
    return True


RENDERS_BRIDGED = _bridge_renders_dir()


def _composition_path(path):
    """A media path as the composition must reference it: relative to
    PROJECT_DIR, never escaping it. Job media is addressed through the
    RENDERS_LINK bridge; anything already in the project (the bundled logo)
    by plain relpath."""
    if not path:
        return path
    if not os.path.isabs(path):
        return path  # already project-relative (e.g. compose.DEFAULT_LOGO_PATH)
    real = os.path.realpath(path)
    root = os.path.realpath(RENDERS_DIR)
    if RENDERS_BRIDGED and (real == root or real.startswith(root + os.sep)):
        return os.path.join("renders", os.path.relpath(real, root))
    return os.path.relpath(path, PROJECT_DIR)


# The operator's own show. Purely a UI convenience now -- it pre-fills the
# field for a station session and is never substituted server-side, so clearing
# the box really does mean "no show name" (see /start).
DEFAULT_SHOW_NAME = "Pop Lock"
MAX_EXTRA_FETCH_ATTEMPTS = 8
# Statuses where a job is parked waiting on a person. They share a hazard: nothing
# advances them, and prune_old_renders() skips anything that isn't done/failed, so an
# abandoned one pins its render directory on the billed volume forever.
AWAITING_STATUSES = ("awaiting_uploads", "awaiting_tracks", "awaiting_story")
# How many times a story can be re-framed before the job has to go one way or the
# other. Each regeneration is a real API call, and an unbounded Retell box is an
# unbounded bill.
MAX_STORY_REGENS = 5
# How long a parked job waits before it's given up on. Uploads get longer because
# finding files for six tracks is genuinely slow work.
GATE_TIMEOUT_SECONDS = float(os.environ.get("GATE_TIMEOUT_MINUTES", "30")) * 60
UPLOAD_GATE_TIMEOUT_SECONDS = float(os.environ.get("UPLOAD_GATE_TIMEOUT_MINUTES", "60")) * 60
# The on-screen line sits on one line of a 9:16 frame; past this it wraps badly.
MAX_BEAT_CHARS = 90
# The operator's own storyline note. Goes straight into a prompt, so it is bounded.
MAX_USER_BRIEF_CHARS = 600
PACE_SCENE_DURATION = {"chill": 11, "normal": 8, "fast": 6, "hyper": 4.5}
MAX_MANUAL_TRACKS_NON_ADMIN = 7
# Must match what the hyperframes renderer emits, or the baked per-frame audio
# arrays drift out of sync with the frames they are indexed against.
RENDER_FPS = 30

# Uploaded logos. Kept small deliberately -- this is a mark in a corner of the
# frame, not artwork, and an unbounded upload on a no-login endpoint is a free
# disk-fill for anyone who finds the URL.
MAX_LOGO_BYTES = 2 * 1024 * 1024
LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}

# The operator's own YouTube cookies, read from a path on disk rather than
# uploaded through the app -- there are no user accounts to attach them to any
# more, and they must never be reachable by anyone else's job.
YOUTUBE_COOKIE_FILE = os.environ.get("YOUTUBE_COOKIE_FILE") or None


def _load_or_create_secret_key() -> str:
    key = os.environ.get("FLASK_SECRET_KEY")
    if key:
        return key
    if os.environ.get("RAILWAY_ENVIRONMENT"):
        # Auto-generating here would silently rotate on every deploy (the
        # container filesystem doesn't persist), logging everyone out each
        # time -- must be set explicitly as a Railway variable instead.
        raise RuntimeError(
            "FLASK_SECRET_KEY must be set as a Railway environment variable -- "
            "auto-generating it in-container would reset (and invalidate all "
            "sessions) on every deploy."
        )
    key = secrets.token_hex(32)
    with open(ENV_PATH, "a") as f:
        f.write(f"\nFLASK_SECRET_KEY={key}\n")
    return key


app = Flask(__name__)
app.secret_key = _load_or_create_secret_key()
# Railway (and most PaaS) terminate HTTPS at their edge and forward to the
# container over plain HTTP -- without this, Flask thinks every request is
# HTTP and generates http:// URLs (breaking Google's redirect_uri match and
# cookie `Secure` handling). Trust one proxy hop for proto/host.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
JOBS = {}

# Google sign-in. Optional: with no credentials configured the app runs exactly
# as it did before, everyone anonymous and watermarked. That keeps local dev and
# the free tier working without anyone having to touch a Google Cloud console.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
oauth = OAuth(app)
if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def sign_in_available() -> bool:
    """Whether to offer Google sign-in -- checked by the UI and by both auth routes,
    so hiding the button also closes the door rather than just painting over it.

    Gated on PAYWALL as well as its own credentials: an account exists to hold credits
    and to attach a purchase to, and with the paywall off there is nothing to hold and
    nothing to buy. Signing in would cost the visitor a Google consent screen and get
    them a badge reading zero. Operator access is a separate token route and is
    unaffected."""
    return PAYWALL and bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


@app.context_processor
def inject_brand():
    """Available to every template, so the name lives in one place."""
    return {
        "brand_name": BRAND_NAME,
        "legal_entity_name": LEGAL_ENTITY_NAME,
        "support_email": SUPPORT_EMAIL,
        "retention_days": int(RENDER_RETENTION_DAYS),
    }


def current_account() -> str | None:
    """The signed-in email, or None. Signing in is never required to generate --
    it only decides whether credits can be spent on a clean export."""
    return session.get("account")

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not access.is_operator():
            # 404 rather than 403 -- an anonymous visitor shouldn't be able to
            # discover that these routes exist at all.
            abort(404)
        return view(*args, **kwargs)
    return wrapped


def _log(job, message):
    job["log"].append(message)


def _is_job_dir(name: str) -> bool:
    """Job directories are named with uuid4. Anything else in renders/ was put
    there by hand -- theme experiments, a keeper -- and must survive pruning."""
    try:
        uuid.UUID(name)
        return True
    except ValueError:
        return False


def _enter_gate(job, status, timeout_seconds):
    """Park a job waiting on a person, with a deadline so it can't wait forever."""
    job["status"] = status
    job["gate_deadline"] = time.time() + timeout_seconds


def _expire_stale_gates() -> int:
    """Fail jobs that have been waiting on a person past their deadline.

    Without this an abandoned gate holds its render directory on the billed volume
    indefinitely, because prune_old_renders() deliberately skips jobs that aren't
    done/failed. Marking them failed hands them back to the ordinary pruning path.

    A gate with no deadline at all is left alone on purpose: that shouldn't happen, and
    stalling visibly beats deleting someone's job on the strength of a missing field.

    Nothing in this process is a scheduler, so it's driven by prune_old_renders() and by
    /status -- any browser still polling, or any later render, sweeps up the stragglers."""
    now = time.time()
    expired = 0
    for job_id, job in list(JOBS.items()):
        if job.get("status") not in AWAITING_STATUSES:
            continue
        deadline = job.get("gate_deadline")
        if deadline is None or now < deadline:
            continue
        job["status"] = "failed"
        job["error"] = "This job was waiting for you and timed out. Start again when ready."
        _log(job, "ERROR: review timed out — job cancelled.")
        analytics.record_job_done(job_id, "failed")
        expired += 1
    return expired


def prune_old_renders() -> int:
    """Deletes finished jobs' files once they're past RENDER_RETENTION_DAYS.
    Returns how many directories went. Never raises: losing a cleanup pass is
    survivable, taking down a render because of one is not.

    Only jobs that are actually finished are eligible. Skipping everything still
    in JOBS would be wrong in a long-lived process -- JOBS never evicts, so every
    job made since the last restart would be protected forever and the directory
    would grow exactly as it does now.
    """
    if RENDER_RETENTION_DAYS <= 0:
        return 0
    # Convert abandoned gates to failures first, so the eligibility check below sees
    # them as finished instead of protecting them forever.
    _expire_stale_gates()
    cutoff = time.time() - RENDER_RETENTION_DAYS * 86400
    removed = 0
    with _prune_lock:
        try:
            names = os.listdir(RENDERS_DIR)
        except OSError:
            return 0
        for name in names:
            if not _is_job_dir(name):
                continue
            job = JOBS.get(name)
            if job is not None and job.get("status") not in ("done", "failed"):
                continue
            path = os.path.join(RENDERS_DIR, name)
            try:
                if not os.path.isdir(path) or os.path.getmtime(path) >= cutoff:
                    continue
                shutil.rmtree(path)
                # Drop the tracking entry too, so a download attempt afterwards
                # gets a clean "no such job" rather than send_file blowing up on
                # a path that is no longer there.
                JOBS.pop(name, None)
                removed += 1
            except OSError:
                continue
    return removed


# A pass at import as well as after each render: a container that has been idle
# (or asleep) for a week would otherwise sit on a full disk until somebody
# happened to generate something.
prune_old_renders()


def _load_saved_themes():
    if not os.path.exists(THEMES_PATH):
        return {}
    try:
        with open(THEMES_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_theme_to_disk(name, theme):
    themes = _load_saved_themes()
    themes[name] = theme
    with open(THEMES_PATH, "w") as f:
        json.dump(themes, f, indent=2)
    return themes


def _resolve_theme(theme_mode, theme_value, tracks, job_id=None, model=curator.MODEL_SIMPLE):
    """Returns a full theme dict: {"palettes": [...], "motion", "frame", "style"}.

    There is no "auto" mode any more. It spent an Anthropic call per job to pick
    a theme, and if that call failed or returned invalid JSON it fell back to
    the default silently -- so "Auto" could quietly mean "Classic with a stock
    palette" with nothing in the UI to say so. A picked theme is predictable and
    free, and the picker shows exactly what you get."""
    if theme_mode == "preset" and theme_value in curator.PRESET_THEMES:
        return curator.PRESET_THEMES[theme_value]
    if theme_mode == "saved":
        saved = _load_saved_themes()
        if theme_value in saved:
            return saved[theme_value]
    if theme_mode == "custom" and theme_value and theme_value.strip():
        return curator.theme_from_description(theme_value.strip(), job_id=job_id, model=model)
    return curator.PRESET_THEMES[curator.DEFAULT_PRESET]


def _resolve_standout_media(job, ranked, job_dir, num_standout, scene_duration, allow_youtube=False, cookie_file=None, show_info=False, collect_options=False):
    """Fetch media for ranked candidates (best first) until num_standout have resolved.

    Candidates beyond num_standout are pulled in only to replace ones that failed to
    resolve -- ranking decides what's featured, and nothing here reorders or evicts on
    the strength of the media that came back. Order is the caller's to decide (in story
    mode it carries the narrative), so this function must not quietly rearrange it."""
    resolved = []
    media_index = 0
    attempts = 0

    for cand in ranked:
        if attempts >= num_standout + MAX_EXTRA_FETCH_ATTEMPTS:
            break
        if len(resolved) >= num_standout:
            break
        attempts += 1

        track_obj = Track(artist=cand["artist"], title=cand["title"], album=cand.get("album"))
        _log(job, f"Fetching media for {cand['artist']} - {cand['title']}...")
        result = media_finder.process_track(track_obj, job_dir, media_index, clip_duration=scene_duration, allow_youtube=allow_youtube, cookie_file=cookie_file, collect_options=collect_options)
        this_index = media_index
        media_index += 1
        has_video = bool(result["video"])
        _log(job, f"  video={'yes' if result['video'] else 'no'} audio={'yes' if result['audio'] else 'no'} image={'yes' if result['image'] else 'no'}")

        entry = {
            "track": {"artist": cand["artist"], "title": cand["title"],
                      "reason": (cand.get("reason", "") or result.get("release_note", "")) if show_info else ""},
            "media": result,
            "has_video": has_video,
            "media_index": this_index,
            "track_obj": track_obj,
        }

        resolved.append(entry)

    return resolved


def _resolve_manual_media(job, picks, job_dir, scene_duration, language, job_id=None, personal=False, allow_youtube=False, cookie_file=None, use_search=True, model=curator.MODEL_SIMPLE, show_info=False, collect_options=False):
    """picks: list of {artist, title, album}. Unlike auto mode, there's no backup
    pool to draw from -- every pick is attempted once, in order, and only true
    dead-ends (no video, no image, no audio found anywhere) get dropped."""
    # The description line is operator-only. Without web search the model is
    # told to stay silent rather than guess, so most tracks got nothing anyway,
    # and a line a subscriber can't review before it ships is a quality risk on
    # someone else's promo. Skipping the call outright also drops an Anthropic
    # request per job for every non-operator run.
    trivia_map = {}
    if show_info:
        tracks_for_trivia = [Track(artist=p["artist"], title=p["title"], album=p.get("album")) for p in picks]
        _log(job, f"Checking for notable trivia on {len(picks)} selected tracks...")
        trivia_map = curator.trivia_for_tracks(tracks_for_trivia, language=language, job_id=job_id, personal=personal, use_search=use_search, model=model)

    resolved = []
    media_index = 0
    for p in picks:
        track_obj = Track(artist=p["artist"], title=p["title"], album=p.get("album"))
        _log(job, f"Fetching media for {p['artist']} - {p['title']}...")
        result = media_finder.process_track(track_obj, job_dir, media_index, clip_duration=scene_duration, allow_youtube=allow_youtube, cookie_file=cookie_file, collect_options=collect_options)
        this_index = media_index
        media_index += 1
        has_video = bool(result["video"])
        _log(job, f"  video={'yes' if result['video'] else 'no'} audio={'yes' if result['audio'] else 'no'} image={'yes' if result['image'] else 'no'}")

        if not result["video"] and not result["image"] and not result["audio"]:
            _log(job, f"  couldn't find anything for {p['artist']} - {p['title']} — skipping it")
            continue

        # Fall back to the factual release line from catalog metadata when the
        # model had nothing verifiable to say.
        reason = ""
        if show_info:
            reason = (trivia_map.get((p["artist"].lower(), p["title"].lower()), "")
                      or result.get("release_note", ""))
        resolved.append({
            "track": {"artist": p["artist"], "title": p["title"], "reason": reason},
            "media": result,
            "has_video": has_video,
            "media_index": this_index,
            "track_obj": track_obj,
        })

    return resolved


def _extend_closing_audio(job, last_entry, job_dir, scene_duration, allow_youtube=False, cookie_file=None):
    extended = scene_duration + compose.OUTRO_DURATION
    track_obj = last_entry["track_obj"]
    idx = last_entry["media_index"]
    _log(job, f"Extending closing track's audio to cover the outro card...")
    try:
        audio_path = media_finder.find_audio_only(
            track_obj, job_dir, idx, audio_duration=extended,
            allow_youtube=allow_youtube, cookie_file=cookie_file,
        )
        if audio_path:
            last_entry["media"]["audio"] = audio_path
    except Exception as e:
        _log(job, f"  could not extend closing audio ({e}) — outro may be quiet")


def _friendly_error(e: Exception) -> str:
    """What the user sees in the error box. Most exceptions here are already written
    for a person (ValueError("No tracks found...")); the ones that aren't come from the
    API and read like stack noise, so they get translated."""
    text = str(e)
    if "content filtering policy" in text:
        return ("The model's response was blocked by a content filter. This is usually "
                "a one-off -- try generating again.")
    if "rate_limit" in text or "429" in text:
        return "The model is rate-limited right now. Wait a moment and try again."
    return text


def _stage(fn):
    """Wrap a pipeline stage so a failure marks the job failed in exactly one place.

    Every stage runs on its own thread with no one to catch what it raises, and there
    are several of them now -- without this each would need its own identical except."""
    @wraps(fn)
    def wrapper(job_id, *args, **kwargs):
        job = JOBS.get(job_id)
        if job is None:
            return None
        try:
            return fn(job_id, *args, **kwargs)
        except Exception as e:
            job["status"] = "failed"
            job["error"] = _friendly_error(e)
            _log(job, f"ERROR: {e}")
            traceback.print_exc()
            analytics.record_job_done(job_id, "failed")
            return None
    return wrapper


def _rank_for_story(ranked, sequence):
    """Reorder the candidate pool so the story's picks come first, in its order.

    The pool is ranked by standout-ness, but a story selects for what serves the
    through-line, which is not the same thing -- left alone, media resolution takes the
    top N by rank and the promo ends up featuring tracks the story never mentions while
    missing ones it does. Everything the story skipped stays on the end, still available
    as a backup when one of its picks won't resolve."""
    if not sequence:
        return ranked
    wanted = [(s.get("artist", "").lower(), s.get("title", "").lower()) for s in sequence]
    by_key = {(r["artist"].lower(), r["title"].lower()): r for r in ranked}
    picked = [by_key[k] for k in wanted if k in by_key]
    picked_keys = {(r["artist"].lower(), r["title"].lower()) for r in picked}
    return picked + [r for r in ranked
                     if (r["artist"].lower(), r["title"].lower()) not in picked_keys]


def _clip_beat(text: str) -> str:
    """Fit an on-screen line to the slot without cutting a word in half.

    A safety net, not the mechanism -- the prompt asks for lines this short. But a
    blind slice at the limit puts "spliced tape loops and droning analogue oscil" on
    screen, which reads as a bug to anyone watching."""
    text = (text or "").strip()
    if len(text) <= MAX_BEAT_CHARS:
        return text
    cut = text[:MAX_BEAT_CHARS - 1]
    spaced = cut.rsplit(" ", 1)[0]
    # Only fall back to the hard cut if there's no sensible word break to use.
    return (spaced if len(spaced) >= MAX_BEAT_CHARS * 0.6 else cut).rstrip(" ,;:-") + "…"


def _apply_story_order(resolved, sequence):
    """Reorder resolved entries into the story's running order and write each beat onto
    the track, where it renders as the on-screen line under the title.

    Matches on artist/title rather than position: media resolution can drop a track that
    wouldn't resolve and pull in a backup the story never saw. Anything the story doesn't
    mention keeps its relative order at the end rather than being dropped."""
    def key_of(artist, title):
        return (artist or "").lower(), (title or "").lower()

    by_key = {key_of(r["track"]["artist"], r["track"]["title"]): r for r in resolved}
    ordered, used = [], set()
    for step in sequence or []:
        key = key_of(step.get("artist"), step.get("title"))
        entry = by_key.get(key)
        if entry is None or key in used:
            continue
        beat = (step.get("beat") or "").strip()
        if beat:
            entry["track"]["reason"] = _clip_beat(beat)
        ordered.append(entry)
        used.add(key)
    ordered.extend(r for r in resolved
                   if key_of(r["track"]["artist"], r["track"]["title"]) not in used)
    return ordered


def _apply_story_quality(job_id, job, bundle, ranked, total_tracks):
    """Run a fresh bundle through the coverage gate and the beat filter, then install it.

    The gate lives here rather than in the prompt because the prompt version did not
    hold: the model kept returning a framing that explained a third of the tracklist and
    grading it "strong". It now states its coverage as a number and curator grades that
    number arithmetically -- see curator.COVERAGE_CEILINGS.

    When the primary fails the floor outright we do not ship it. The honest replacement is
    the best surviving alternate, which costs one expand_story call (~$0.02, ~25s) -- paid
    only on the jobs where the first answer was not good enough, which is exactly when it
    is worth paying. If nothing survives, the primary ships graded at the floor: a thin
    story the operator can see and retell beats no story and a dead screen."""
    p = job["params"]
    curator.enforce_story_quality(bundle, total_tracks)
    q = bundle["quality"]

    for headline in q["dropped"]:
        _log(job, f"Dropped a framing that covered too little of the set: {headline}")
    for beat in q["blanked"]:
        _log(job, f"Dropped a line about the running order rather than the record: {beat}")

    if q["primary_failed"] and bundle["alternates"]:
        weak = bundle["story"]
        _log(job, f"\"{weak.get('headline', '')}\" only covers part of the set; "
                  f"working up a stronger framing instead...")
        # Best surviving alternate, not the first one offered -- the model puts its
        # strongest framing in the primary slot, so once that slot has failed the
        # ordering of what is left carries no signal and the grades do.
        rank = {"strong": 0, "good": 1, "solid": 2}
        promoted = min(bundle["alternates"], key=lambda a: rank.get(a.get("confidence"), 9))
        bundle["alternates"].remove(promoted)
        story = curator.expand_story(
            ranked, promoted, num_featured=len(weak.get("sequence", [])) or p["num_standout"],
            language=p["language"], job_id=job_id, model=p["model"],
            track_facts=job.get("track_facts"), total_tracks=total_tracks)
        # The replacement is graded but not gated -- there is nothing further to fall
        # back to, and a second expand call chasing a better one is not worth the money.
        for beat in curator.regrade_story(story, total_tracks):
            _log(job, f"Dropped a line about the running order rather than the record: {beat}")
        bundle["story"] = story
    elif q["primary_failed"]:
        _log(job, "No framing covered enough of the set to be confident about; "
                  "offering the closest reading found.")
        bundle["story"]["confidence"] = "solid"

    job["story"] = bundle["story"]
    job["story_alternates"] = bundle["alternates"]


@_stage
def _run_curation_stage(job_id):
    """Parse, theme, and decide what's in the promo -- everything before the slow part.

    Ends by calling the media stage on this same thread. The story review gate will slot
    in between the two, which is why they are separate functions rather than one."""
    job = JOBS[job_id]
    p = job["params"]
    job_dir = os.path.join(RENDERS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    job["job_dir"] = job_dir
    job["cookie_file"] = p["cookie_file"]
    job["allow_youtube"] = p["allow_youtube"]
    job["logo_path"] = p["logo_path"]
    job["scene_duration"] = PACE_SCENE_DURATION.get(p["pace"], PACE_SCENE_DURATION["normal"])

    job["status"] = "parsing"
    tracks = parse_tracklist(p["tracklist_text"])
    if not tracks:
        raise ValueError("No tracks found in tracklist")
    _log(job, f"Parsed {len(tracks)} tracks")
    job["tracks"] = tracks

    job["status"] = "theming"
    _log(job, "Choosing a visual theme...")
    # The layout rides in the theme itself -- picking a theme is the whole
    # decision, and there is no second control to reconcile with.
    job["theme"] = _resolve_theme(p["theme_mode"], p["theme_value"], tracks,
                                  job_id=job_id, model=p["model"])

    if p["selection_mode"] == "manual":
        by_key = {(t.artist.lower(), t.title.lower()): t for t in tracks}
        picks = []
        for pick in p["manual_picks"]:
            t = by_key.get((pick.get("artist", "").lower(), pick.get("title", "").lower()))
            if t:
                picks.append({"artist": t.artist, "title": t.title, "album": t.album})
        if not picks:
            raise ValueError("No tracks were selected")
        job["picks"] = picks
    else:
        job["status"] = "curating"
        if p["story_on"]:
            _log(job, "Ranking tracks and finding the story...")
            # Fetched here rather than inside the curation call so the same facts are
            # available later to expand_story -- a promoted alternate should be grounded
            # in the same catalogue data the primary was, and re-fetching them would be a
            # second sweep of MusicBrainz for answers we already have.
            job["track_facts"] = providers.facts_for_tracks(tracks)
            ranked, bundle = curator.curate_with_story(
                tracks, n=p["num_standout"], language=p["language"], job_id=job_id,
                personal=p["personal"], use_search=p["use_search"], model=p["model"],
                user_brief=p["user_brief"], track_facts=job["track_facts"])
            _apply_story_quality(job_id, job, bundle, ranked, len(tracks))
            job["story_rev"] = 0
            job["story_regens_left"] = MAX_STORY_REGENS
            _log(job, f"Story [{job['story'].get('confidence', '?')}]: "
                      f"{job['story'].get('headline', '')}")
        else:
            _log(job, "Ranking standout tracks" + (" (web research)..." if p["use_search"] else "..."))
            ranked = curator.curate_ranked(
                tracks, n=p["num_standout"], language=p["language"], job_id=job_id,
                personal=p["personal"], use_search=p["use_search"], model=p["model"])
        _log(job, "Ranked candidates: " + ", ".join(f"{r['artist']} - {r['title']}" for r in ranked))
        job["ranked"] = ranked

    # The story gate sits here: after the AI has decided, before anything is fetched.
    # Rejecting a framing at this point costs nothing but the call already made.
    if job.get("story") and p.get("gate_story"):
        _enter_gate(job, "awaiting_story", GATE_TIMEOUT_SECONDS)
        return

    _run_media_stage(job_id)


@_stage
def _run_restory_stage(job_id, instruction):
    """Re-frame the current story from the user's own words, then park again.

    On its own thread rather than inside the POST: this is an adaptive-thinking call
    that runs for tens of seconds, and gunicorn's request timeout is 180s. The browser
    is already polling, so it finds out the same way it finds out about everything else."""
    job = JOBS[job_id]
    p = job["params"]
    total = len(job.get("tracks") or [])
    bundle = curator.restory(
        job["story"].get("sequence", []), job["story"], instruction,
        language=p["language"], job_id=job_id, model=p["model"],
        total_tracks=total)
    # Graded and scrubbed, but never dropped: they asked for this telling by name, and a
    # button that refuses to produce what was asked for is a broken button, not a quality
    # control. An honest confidence chip is the right answer to a thin re-framing.
    for beat in curator.regrade_story(bundle["story"], total):
        _log(job, f"Dropped a line about the running order rather than the record: {beat}")
    job["story"] = bundle["story"]
    job["story_alternates"], _ = curator.filter_pitches(bundle["alternates"], total)
    job["story_rev"] = job.get("story_rev", 0) + 1
    _log(job, f"Retold [{bundle['story'].get('confidence', '?')}]: "
              f"{bundle['story'].get('headline', '')}")
    _enter_gate(job, "awaiting_story", GATE_TIMEOUT_SECONDS)


@_stage
def _run_expand_stage(job_id, index):
    """Work a chosen alternate up into a full framing, then park again.

    Alternates arrive as pitches -- a headline and a line of body -- because working
    all of them up in full was most of the curation call's output, and output is most
    of its cost. Only the one actually picked gets expanded."""
    job = JOBS[job_id]
    p = job["params"]
    pitch = job["story_alternates"][index]
    _log(job, f"Working up: {pitch.get('headline', '')}")
    total = len(job.get("tracks") or [])
    story = curator.expand_story(
        job["ranked"], pitch, num_featured=len(job["story"].get("sequence", [])) or p["num_standout"],
        language=p["language"], job_id=job_id, model=p["model"],
        track_facts=job.get("track_facts"), total_tracks=total)
    # Graded, not gated: this is the framing they picked off the screen. If working it up
    # in full shows it to be thinner than the pitch suggested, the chip says so.
    for beat in curator.regrade_story(story, total):
        _log(job, f"Dropped a line about the running order rather than the record: {beat}")
    # The chosen pitch swaps places with the story it replaces, so the previous
    # framing stays offered rather than disappearing when someone changes their mind.
    previous = job["story"]
    job["story_alternates"][index] = {
        "headline": previous.get("headline", ""),
        "body": previous.get("body", ""),
        "confidence": previous.get("confidence", "solid"),
        # Carried across so the demoted framing is still graded on the same ladder if it
        # is picked again -- without it, swapping back and forth would launder its grade.
        "covers": previous.get("covers", 0),
    }
    job["story"] = story
    job["story_rev"] = job.get("story_rev", 0) + 1
    _enter_gate(job, "awaiting_story", GATE_TIMEOUT_SECONDS)


@_stage
def _run_media_stage(job_id):
    """The slow half: fetch audio and artwork, then hand off to render."""
    job = JOBS[job_id]
    p = job["params"]
    job_dir = job["job_dir"]
    scene_duration = job["scene_duration"]

    job["status"] = "fetching_media"
    # Alternates are only worth collecting if someone is going to look at them.
    collect_options = bool(p.get("gate_tracks"))
    if p["selection_mode"] == "manual":
        resolved = _resolve_manual_media(
            job, job["picks"], job_dir, scene_duration, p["language"], job_id=job_id,
            personal=p["personal"], allow_youtube=p["allow_youtube"],
            cookie_file=p["cookie_file"], use_search=p["use_search"], model=p["model"],
            show_info=p["show_info"], collect_options=collect_options)
    else:
        ranked = job["ranked"]
        if job.get("story"):
            ranked = _rank_for_story(ranked, job["story"].get("sequence"))
        # The story settled the count when it chose its sequence; manual picks settle it
        # by being picked. Either way it is decided by now, not configured up front.
        want = len(job["story"].get("sequence", [])) if job.get("story") else p["num_standout"]
        resolved = _resolve_standout_media(
            job, ranked, job_dir, want, scene_duration,
            allow_youtube=p["allow_youtube"], cookie_file=p["cookie_file"],
            show_info=p["show_info"], collect_options=collect_options)

    if not resolved:
        raise ValueError("Could not resolve media for any standout track")

    # Story order is applied after resolution, not before: only now is it settled which
    # tracks actually made it in.
    if job.get("story"):
        resolved = _apply_story_order(resolved, job["story"].get("sequence"))
        _log(job, "Running order: " + " / ".join(
            f"{r['track']['artist']} - {r['track']['title']}" for r in resolved))

    tracks = job["tracks"]
    show_name, episode_label = p["show_name"], p["episode_label"]
    allow_youtube, cookie_file = p["allow_youtube"], p["cookie_file"]
    language = p["language"]

    job["resolved"] = resolved
    _extend_closing_audio(job, resolved[-1], job_dir, scene_duration, allow_youtube=allow_youtube, cookie_file=cookie_file)

    # Exclude by artist too, not just exact track match -- the "also in this
    # episode" list shouldn't repeat an artist already featured in the video.
    chosen_artists = {r["track"]["artist"].lower() for r in resolved}
    remaining = [
        {"artist": t.artist, "title": t.title}
        for t in tracks
        if t.artist.lower() not in chosen_artists
    ]

    # Paths stay absolute in job state. Everything on this side reads them
    # off disk, and the cwd differs between local dev (the project dir) and
    # the container (gunicorn runs with --chdir server), so a relative path
    # here silently fails os.path.exists in _analyze_scene_audio. They are
    # made document-root-relative once, in _run_render_stage.
    standout = [{"track": r["track"], "media": {
        "video": r["media"]["video"],
        "audio": r["media"]["audio"],
        "image": r["media"]["image"],
    }} for r in resolved]

    needs_upload = [
        {"index": i, "artist": s["track"]["artist"], "title": s["track"]["title"]}
        for i, s in enumerate(standout) if not s["media"]["audio"]
    ]

    video_count = sum(1 for r in resolved if r["has_video"])
    _log(job, f"{video_count}/{len(resolved)} standout tracks have video ({round(100 * video_count / len(resolved))}%)")

    job["standout"] = standout
    job["remaining"] = remaining
    job["show_name"] = show_name
    job["episode_label"] = episode_label
    job["language"] = language
    job["needs_upload"] = needs_upload

    _advance_after_media(job_id)


def _advance_after_media(job_id):
    """The single decision point between media resolution and render.

    Every path that can resume a job funnels through here -- the media stage, /upload
    and /skip -- so the order of the pauses is stated once instead of being re-derived
    at each call site."""
    job = JOBS[job_id]
    # Uploads first: /skip can drop a track and renumber everything after it, or empty
    # the job entirely. Reviewing covers for a track that then disappears wastes the
    # user's attention and would need the same index-shifting a second time.
    if job.get("needs_upload"):
        _enter_gate(job, "awaiting_uploads", UPLOAD_GATE_TIMEOUT_SECONDS)
        _log(job, "Waiting on manual audio uploads before rendering.")
        return
    if job["params"].get("gate_tracks") and not job.get("tracks_confirmed"):
        job["track_choices"] = _build_track_choices(job_id)
        _enter_gate(job, "awaiting_tracks", GATE_TIMEOUT_SECONDS)
        return
    job["status"] = "ready_to_render"
    _log(job, "All media resolved — ready to render.")
    _start_render(job_id)


def _build_track_choices(job_id):
    """What the tracklist screen renders: one row per track, in running order.

    Options come straight from what media resolution already collected -- they are
    remote provider URLs, so nothing here touches the network or disk. Option 0 is the
    cover on disk, and is pointed at the local /media route so the screen always opens
    on an image that is certainly there."""
    job = JOBS[job_id]
    rows = []
    for i, (item, res) in enumerate(zip(job["standout"], job["resolved"])):
        options = [dict(o) for o in res["media"].get("image_options", [])]
        if options:
            options[0]["thumb"] = f"/media/{job_id}/track{res['media_index']}_art.jpg"
        rows.append({
            "index": i,
            "artist": item["track"]["artist"],
            "title": item["track"]["title"],
            "line": item["track"].get("reason", ""),
            "options": options,
        })
    return rows


def _start_render(job_id):
    thread = threading.Thread(target=_run_render_stage, args=(job_id,), daemon=True)
    thread.start()


def _save_logo(file_storage, job_dir) -> str | None:
    """Store an uploaded logo inside the job directory and return its path.

    Returns None for anything that isn't plausibly an image, rather than
    raising -- a bad logo should cost the user their logo, not their render.
    The extension is checked against an allowlist and the name is run through
    secure_filename, so nothing here can escape the job directory."""
    if not file_storage or not file_storage.filename:
        return None
    name = secure_filename(file_storage.filename)
    ext = os.path.splitext(name)[1].lower()
    if ext not in LOGO_EXTENSIONS:
        return None

    data = file_storage.read(MAX_LOGO_BYTES + 1)
    if not data or len(data) > MAX_LOGO_BYTES:
        return None

    os.makedirs(job_dir, exist_ok=True)
    dest = os.path.join(job_dir, f"logo{ext}")
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def _analyze_scene_audio(job):
    """Bake each scene's audio into per-frame energy arrays for the reactive
    visuals. Runs once here rather than live in the browser, because the
    renderer seeks a paused timeline and a live analyser would read silence
    (and could disagree between passes). Roughly a second of CPU per clip and
    no API calls -- see audio_analysis.py."""
    _log(job, "Analyzing audio for reactive visuals...")
    for item in job["standout"]:
        audio_path = (item.get("media") or {}).get("audio")
        if not audio_path or not os.path.exists(audio_path):
            item["analysis"] = None
            continue
        try:
            item["analysis"] = audio_analysis.analyze(audio_path, fps=RENDER_FPS)
        except Exception as e:
            # A failed analysis just means that scene isn't reactive; it must
            # never take down the render.
            item["analysis"] = None
            _log(job, f"  audio analysis skipped for one track ({e})")


def _run_render_stage(job_id):
    job = JOBS[job_id]
    composition_name = f"composition-{job_id}.html"
    try:
        job["status"] = "composing"
        _analyze_scene_audio(job)
        visuals.install_vendor(PROJECT_DIR)
        styles.install_fonts(PROJECT_DIR)
        _log(job, "Building composition...")
        # Absolute job paths become document-root-relative here and nowhere
        # else -- this is the boundary between "paths we read" and "paths the
        # browser fetches". See _composition_path.
        standout = [{**item, "media": {k: _composition_path(v) for k, v in item["media"].items()}}
                    for item in job["standout"]]
        html = compose.build_composition_html(
            job["show_name"], job["episode_label"], standout, job["remaining"],
            job["theme"], job["scene_duration"], language=job.get("language", "en"),
            logo_path=_composition_path(job.get("logo_path")),
        )
        # Per job, not the shared index.html: two jobs rendering at once would
        # otherwise overwrite each other's composition between write and render, and
        # holding jobs open at a review screen makes that overlap ordinary rather than
        # rare. It sits at the project root so the media paths above resolve the same
        # way they do for index.html. index.html is still written, purely so the last
        # render stays inspectable by hand.
        with open(os.path.join(PROJECT_DIR, composition_name), "w") as f:
            f.write(html)
        with open(os.path.join(PROJECT_DIR, "index.html"), "w") as f:
            f.write(html)

        job["status"] = "rendering"
        _log(job, "Rendering MP4 (this can take a minute)...")
        output_rel = _composition_path(os.path.join(job["job_dir"], "output.mp4"))
        compose.render_video(PROJECT_DIR, output_rel, quality="standard",
                             composition=composition_name)

        master = os.path.join(job["job_dir"], "output.mp4")
        # The clean master never leaves the server unless the session is
        # entitled to it. Everything on-screen -- and every unentitled download
        # -- comes from the watermarked copy, so the master is not reachable by
        # reading a URL out of the page source.
        #
        # With the paywall off there is nothing to withhold, so the pass is
        # skipped entirely rather than made and ignored -- it is a full extra
        # ffmpeg encode. _served_path falls back to the master when no
        # watermarked copy exists, so preview and download both keep working.
        watermarked = None
        if PAYWALL:
            _log(job, "Adding watermark...")
            watermarked = os.path.join(job["job_dir"], "output-watermarked.mp4")
            compose.watermark_video(master, watermarked)

        job["status"] = "done"
        job["output_path"] = master
        job["watermarked_path"] = watermarked
        _log(job, "Done!")
        analytics.record_job_done(job_id, "done")
        # Piggy-backing cleanup on renders means disk gets reclaimed in
        # proportion to how hard the tool is being used, with no scheduler and
        # no extra process to babysit.
        prune_old_renders()
    except Exception as e:
        job["status"] = "failed"
        job["error"] = _friendly_error(e)
        _log(job, f"ERROR: {e}")
        traceback.print_exc()
        analytics.record_job_done(job_id, "failed")
    finally:
        # Whether it rendered or blew up, the per-job composition has done its job.
        try:
            os.unlink(os.path.join(PROJECT_DIR, composition_name))
        except OSError:
            pass


# Paddle's domain review requires terms, a refund policy and a privacy policy,
# each reachable from the site's navigation rather than buried. LAST_UPDATED is
# a constant so the date reflects when the wording actually changed, not when
# the container last restarted.
LEGAL_LAST_UPDATED = "31 July 2026"


@app.route("/terms")
def terms():
    return render_template("terms.html", page="terms", last_updated=LEGAL_LAST_UPDATED)


@app.route("/refunds")
def refunds():
    return render_template("refunds.html", page="refunds", last_updated=LEGAL_LAST_UPDATED)


@app.route("/privacy")
def privacy():
    return render_template("privacy.html", page="privacy", last_updated=LEGAL_LAST_UPDATED)


@app.route("/auth/google")
def auth_google():
    if not sign_in_available():
        return ("Sign-in is off right now -- every export is free and unwatermarked, "
                "so there is nothing an account would get you."), 503
    session["post_login_redirect"] = request.args.get("next") or url_for("index")
    return oauth.google.authorize_redirect(url_for("auth_google_callback", _external=True))


@app.route("/auth/google/callback")
def auth_google_callback():
    if not sign_in_available():
        return "Google sign-in isn't configured.", 503
    try:
        token = oauth.google.authorize_access_token()
    except Exception:
        # A stale/replayed callback (back button, expired state) must land the
        # visitor back on a working page, not a 500.
        return redirect(url_for("index", signin="failed"))

    userinfo = token.get("userinfo") or {}
    email = userinfo.get("email")
    if not email or not userinfo.get("email_verified"):
        return redirect(url_for("index", signin="unverified"))

    # Deliberately NOT session.clear(). The old sign-in did, and here that would
    # discard "vid" -- the id every job is owned by -- so anyone who generated a
    # promo and then signed in to export it would lose the job they came to buy.
    # Only the operator flag is dropped, since that is granted per browser by a
    # token and shouldn't silently ride along into a different account.
    session.pop("operator", None)
    session["account"] = accounts.normalize(email)
    session.permanent = True
    return redirect(session.pop("post_login_redirect", None) or url_for("index"))


@app.route("/logout", methods=["POST"])
def logout():
    """Signs out without touching "vid", so the jobs made in this browser are
    still reachable afterwards."""
    session.pop("account", None)
    return redirect(url_for("index"))


@app.route("/credits")
def credits_status():
    """Balance for the header. Also reports whether sign-in is even available,
    so the UI can hide the button rather than offer a 503."""
    account = current_account()
    payload = {"signed_in": bool(account), "account": account,
               "sign_in_available": sign_in_available(), "operator": access.is_operator(),
               # So the page can drop the export note and the buy buttons rather
               # than warn about a watermark that isn't being applied.
               "paywall": PAYWALL}
    payload.update(accounts.summary(account) if account else
                   {"balance": 0, "expiring": 0, "next_expiry": None, "videos_paid_for": 0})
    # Whether this particular video is already paid for, so the UI can say
    # "already yours" instead of threatening to charge again for a re-download.
    job_id = request.args.get("job_id")
    payload["paid_for_job"] = bool(account and job_id and accounts.has_paid_for(account, job_id))
    return jsonify(payload)


@app.route("/paddle/webhook", methods=["POST"])
def paddle_webhook():
    """Paddle's notification endpoint. This is the only way credits are created
    by a payment, so it is the one route where being strict matters more than
    being helpful.

    The body is read raw and verified before it is parsed: Paddle signs the exact
    bytes it sent, so round-tripping through json.loads/dumps first would change
    the whitespace and fail every signature.
    """
    secret = paddle.webhook_secret()
    if not secret:
        # Refuse rather than accept unverified events. An unconfigured secret
        # with a permissive endpoint is a free credit generator for anyone who
        # finds the URL.
        return jsonify({"error": "webhook not configured"}), 503

    raw = request.get_data()
    if not paddle.verify_signature(raw, request.headers.get("Paddle-Signature", ""), secret):
        return jsonify({"error": "bad signature"}), 403

    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return jsonify({"error": "malformed body"}), 400

    try:
        result = paddle.handle_event(event)
    except Exception as e:
        # A 500 makes Paddle retry, which is right for a transient fault and
        # harmless for a permanent one because grants are deduplicated.
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    print(f"[paddle] {event.get('event_type')} -> {result}", flush=True)
    return jsonify(result), 200


@app.route("/credits/checkout")
def credits_checkout():
    """What the page needs to open a Paddle checkout. Never exposes the webhook
    secret -- the client token is public by design."""
    return jsonify({
        "configured": paddle.is_configured(),
        "environment": paddle.env(),
        "client_token": paddle.client_token(),
        "prices": paddle.price_map(),
        "account": current_account(),
    })


@app.route("/credits/redeem", methods=["POST"])
def redeem_coupon():
    account = current_account()
    if not account:
        return jsonify({"error": "Sign in to redeem a code."}), 401
    code = (request.get_json(force=True, silent=True) or {}).get("code", "")
    ok, message = accounts.redeem(account, code)
    return jsonify({"ok": ok, "message": message, "balance": accounts.balance(account)}), (200 if ok else 400)


@app.route("/o/<token>")
def grant_operator(token):
    """The operator's private door. Visit once per browser; the session flag
    persists from then on. Replaces the whole Google sign-in flow -- there are
    no user accounts any more, only "is this the operator's browser"."""
    if not access.token_matches(token):
        abort(404)
    access.grant_operator()
    return redirect(url_for("index"))


@app.route("/s/<token>")
def grant_station(token):
    """The station link. Hand this to colleagues: it defaults their promos to
    the station logo and grants nothing else."""
    if not access.station_token_matches(token):
        abort(404)
    access.grant_station()
    return redirect(url_for("index"))


@app.route("/kzradio")
def kzradio():
    """The station's front door, under a name you can say out loud.

    Identical to /s/<STATION_TOKEN> in what it grants -- the station session,
    which means the KZ Radio mark is pre-selected as the logo -- but with a
    memorable URL instead of a secret one, so it can go in a bio or be told to
    someone over the phone.

    That is a deliberate trade, and it is the whole of the trade: a guessable
    URL means anyone who tries /kzradio gets the KZ mark offered as a default
    on their own promo. Nothing else follows from a station session -- no
    operator perks, no AI curation, no YouTube source (see access.py) -- and
    the token route stays for anyone who wants the unguessable version."""
    analytics.record_visit("/kzradio", access.visitor_id())
    access.grant_station()
    return redirect(url_for("index"))


@app.route("/o/revoke", methods=["POST"])
def revoke_operator():
    access.revoke_operator()
    return redirect(url_for("index"))


@app.route("/")
def index():
    analytics.record_visit("/", access.visitor_id())
    # personal_mode gates operator-only UI: custom/saved theme tools, AI
    # curation, quality mode and the Analytics link. Pop Lock curation
    # personalization is separate -- it keys off the show name (see /start).
    return render_template(
        "index.html", default_show_name=DEFAULT_SHOW_NAME,
        language_choices=languages.choices(),
        theme_preview_css=styles.thumbnail_css(curator.PRESET_THEMES),
        theme_layout_css=styles.preview_layout_css(curator.PRESET_THEMES),
        theme_choices=styles.choices(curator.PRESET_THEMES),
        personal_mode=access.is_operator(),
        station_mode=access.is_station(),
        station_logo_url=url_for("static", filename=os.path.basename(compose.DEFAULT_LOGO_PATH)),
        max_manual_tracks=MAX_MANUAL_TRACKS_NON_ADMIN,
        max_beat_chars=MAX_BEAT_CHARS,
        signed_in=bool(current_account()),
        account=current_account(),
        sign_in_available=sign_in_available(),
    )


@app.route("/analytics")
@admin_required
def analytics_page():
    return render_template("analytics.html", stats=analytics.summary())


@app.route("/themes")
def themes():
    return jsonify({"presets": list(curator.PRESET_THEMES.keys()), "saved": _load_saved_themes()})


@app.route("/themes/save", methods=["POST"])
@admin_required
def save_theme():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    valid = curator._valid_theme(data.get("theme"))
    if not valid:
        return jsonify({"error": "invalid theme"}), 400
    themes = _save_theme_to_disk(name, valid)
    return jsonify({"ok": True, "saved": themes})


@app.route("/parse_tracklist", methods=["POST"])
def parse_tracklist_route():
    data = request.get_json(force=True)
    tracks = parse_tracklist(data.get("tracklist", ""))
    return jsonify({"tracks": [{"artist": t.artist, "title": t.title, "album": t.album} for t in tracks]})


@app.route("/start", methods=["POST"])
def start():
    is_admin_user = access.is_operator()

    # With sign-in gone there's nothing in front of a job that spends Anthropic
    # tokens and render CPU, so an anonymous visitor can be given a daily cap.
    # Off by default -- see access.DAILY_JOB_LIMIT. The operator is exempt.
    allowed, remaining = access.check_job_quota()
    if not allowed:
        return jsonify({
            "error": "Daily limit reached. You can make more promos again tomorrow."
        }), 429

    tracklist_text = request.form.get("tracklist", "")
    # Show name and episode label are both optional and neither has a default.
    # There is no sensible generic value for a name that sits on screen for the
    # whole promo, and substituting one behind the user's back is worse than
    # leaving it out: it either puts the operator's own show ("Pop Lock") on a
    # stranger's video, or overrides someone who deliberately cleared the field.
    # An empty field simply renders nothing -- see compose._header_html.
    show_name = request.form.get("show_name", "").strip()
    episode_label = request.form.get("episode_label", "").strip()
    # No longer a user setting. For the story path the model chooses how many tracks the
    # through-line needs (curator.MIN_FEATURED..MAX_FEATURED) and this is only the ceiling
    # on the candidate pool; for manual selection the count is however many boxes were ticked.
    num_standout = curator.MAX_FEATURED
    pace = request.form.get("pace", "normal")
    theme_mode = request.form.get("theme_mode", "auto")
    theme_value = request.form.get("theme_value", "")
    selection_mode = request.form.get("selection_mode", "manual")
    if selection_mode not in ("auto", "brief", "manual"):
        selection_mode = "manual"
    language = languages.normalize(request.form.get("language"))
    # Custom/saved themes are admin (Roni) only -- enforced here too, not just
    # hidden in the UI, since form fields can be submitted directly.
    if theme_mode in ("custom", "saved") and not is_admin_user:
        theme_mode = "auto"
    # AI curation and the story layer are open to everyone: with web search off they
    # cost ~$0.05 a job, which the daily quota above already bounds. What stays
    # admin-only is web research (`quality_mode`, several times the cost) and Pop Lock's
    # own show context.
    # The story isn't a separate option -- it IS what AI selection means. Picking tracks
    # by theme, ordering them to tell it and writing the lines is one act of curation, and
    # it costs the same as ranking alone, so there was never a reason to offer the weaker
    # half on its own.
    story_on = selection_mode == "auto"
    user_brief = request.form.get("user_brief", "").strip()[:MAX_USER_BRIEF_CHARS]
    # Each mode has one "just make it" button and one "show me first" button. Which
    # review screen that second button opens depends on the mode: the story path stops at
    # the story (and can go on to the covers from there), the manual path has no story to
    # show, so it stops at the covers.
    review_first = request.form.get("review_first") == "1"
    gate_story = story_on and review_first
    gate_tracks = (not story_on) and review_first
    # Pop Lock personalization keys off the show name itself, admin only --
    # a colleague typing "Pop Lock" as their own show name doesn't get it.
    personal = is_admin_user and show_name.strip().lower() in ("pop lock", "poplock")
    # Colleagues always get "simple" (no search). Admin can pick "simple" too, to
    # preview exactly what colleagues experience, or "advanced" for web research.
    # Both run the same model -- "advanced" buys searching, not a bigger model.
    quality_mode = request.form.get("quality_mode", "simple")
    if not is_admin_user or quality_mode not in ("simple", "advanced"):
        quality_mode = "simple"
    use_search = quality_mode == "advanced"
    model = curator.MODEL_SIMPLE
    manual_picks = []
    if selection_mode == "manual":
        try:
            manual_picks = json.loads(request.form.get("manual_tracks", "[]"))
        except Exception:
            manual_picks = []
        if not is_admin_user:
            manual_picks = manual_picks[:MAX_MANUAL_TRACKS_NON_ADMIN]

    # The YouTube source is operator-only: downloading from YouTube breaks its
    # terms of service, so it must never run for anyone else's job.
    allow_youtube = is_admin_user
    # show_info is what lets a line render under the track title. It used to track
    # "is admin"; now it tracks "is there anything to show" -- a story writes a beat
    # for every track, and suppressing it would render the feature invisible.
    show_info = is_admin_user or story_on
    cookie_file = YOUTUBE_COOKIE_FILE if (allow_youtube and YOUTUBE_COOKIE_FILE) else None

    owner = access.owner_id()
    job_id = str(uuid.uuid4())
    # The KZ Radio mark is the operator's own branding -- it must never end up
    # on a stranger's promo. Everyone else gets their upload, or no logo.
    # "show" means: the user's upload if they gave one, otherwise the station
    # mark for a station session. Anyone else who picks Show without uploading
    # simply gets no logo rather than someone else's branding.
    use_logo = request.form.get("use_logo", "none")
    logo_path = None
    if use_logo == "show":
        logo_path = _save_logo(request.files.get("logo"), os.path.join(RENDERS_DIR, job_id))
    if not logo_path and use_logo == "show" and access.is_station():
        # Gated server-side as well as hidden in the UI -- the option is a form
        # field, so hiding it is not a control.
        logo_path = compose.DEFAULT_LOGO_PATH
    JOBS[job_id] = {"status": "queued", "log": [], "error": None, "needs_upload": [], "owner": owner}
    # Stashed rather than passed down a long argument list: once a job can pause at a
    # review screen, the stage that resumes it needs every one of these again and has
    # only the job id to work from.
    JOBS[job_id]["params"] = {
        "tracklist_text": tracklist_text, "show_name": show_name,
        "episode_label": episode_label, "num_standout": num_standout, "pace": pace,
        "theme_mode": theme_mode, "theme_value": theme_value,
        "selection_mode": selection_mode, "manual_picks": manual_picks,
        "language": language, "personal": personal, "allow_youtube": allow_youtube,
        "cookie_file": cookie_file, "use_search": use_search, "model": model,
        "show_info": show_info, "logo_path": logo_path,
        "story_on": story_on, "user_brief": user_brief, "gate_tracks": gate_tracks,
        "gate_story": gate_story,
    }

    analytics.record_job_start(job_id, "/", owner)

    thread = threading.Thread(target=_run_curation_stage, args=(job_id,), daemon=True)
    thread.start()
    return jsonify({"job_id": job_id})


def _owns_job(job) -> bool:
    """Ownership keys off the bare browser id, not the analytics label.

    The stored value is split on ':' so jobs created before this fix -- whose
    owner was recorded as "anon:<id>" or "operator:<id>" -- still match after a
    deploy, instead of every in-flight job 403ing."""
    if job is None:
        return False
    stored = (job.get("owner") or "").split(":", 1)[-1]
    return stored == access.owner_id()


@app.route("/status/<job_id>")
def status(job_id):
    # Any polling browser doubles as the sweeper for jobs nobody came back to.
    _expire_stale_gates()
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    if not _owns_job(job):
        return jsonify({"error": "not your job"}), 403
    return jsonify({
        "status": job["status"],
        "log": job["log"],
        "error": job.get("error"),
        "needs_upload": job.get("needs_upload", []),
        "theme": job.get("theme"),
        # Absent on jobs with no story, so the existing client sees exactly today's
        # payload shape and ignores these.
        "story": job.get("story"),
        "story_alternates": job.get("story_alternates", []),
        "story_rev": job.get("story_rev", 0),
        "story_regens_left": job.get("story_regens_left", 0),
        # Epoch seconds, so a parked job can show how long it has left.
        "gate_deadline": job.get("gate_deadline"),
        # Only present while parked at the tracklist screen; the panel is built once
        # from it and this stops being sent the moment the choices are confirmed.
        "track_choices": job.get("track_choices"),
    })


# Only the two images a job actually writes. An allowlist rather than a sanitiser: the
# set is tiny and known, so nothing has to be reasoned about at request time.
_MEDIA_NAME_RE = re.compile(r"^track\d+_(art|artist)\.jpg$")


@app.route("/media/<job_id>/<name>")
def job_media(job_id, name):
    """Serve a job's downloaded cover to its owner's browser.

    Alternates in the picker are remote provider URLs, so this only ever serves the one
    image that is actually on disk -- the cover currently in use."""
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    if not _owns_job(job):
        return jsonify({"error": "not your job"}), 403
    if not job.get("job_dir") or not _MEDIA_NAME_RE.match(name):
        abort(404)
    # send_from_directory rejects traversal on its own; the regex above means it never
    # has to.
    resp = send_from_directory(job["job_dir"], name)
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp


@app.route("/story/<job_id>", methods=["POST"])
def story_action(job_id):
    """Confirm, swap, or re-frame the story a job is parked on.

    Everything that runs a model call answers immediately and works on a thread; the
    poll already running in the browser reports what happened."""
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    if not _owns_job(job):
        return jsonify({"error": "not your job"}), 403
    if job.get("status") != "awaiting_story":
        return jsonify({"error": "this job isn't waiting on a story"}), 400

    body = request.get_json(silent=True) or {}
    action = body.get("action")

    if action == "confirm":
        job["params"]["gate_tracks"] = bool(body.get("review_tracks"))
        job.pop("gate_deadline", None)
        threading.Thread(target=_run_media_stage, args=(job_id,), daemon=True).start()
        return jsonify({"ok": True})

    if action == "use_alternate":
        try:
            index = int(body.get("index"))
            job["story_alternates"][index]
        except (TypeError, ValueError, IndexError):
            return jsonify({"error": "no such alternate"}), 400
        job["status"] = "restorying"
        threading.Thread(target=_run_expand_stage, args=(job_id, index), daemon=True).start()
        return jsonify({"ok": True})

    if action == "regenerate":
        if job.get("story_regens_left", 0) <= 0:
            return jsonify({"error": "no retells left for this job"}), 429
        instruction = (body.get("instruction") or "").strip()[:MAX_USER_BRIEF_CHARS]
        if not instruction:
            return jsonify({"error": "say how you'd like it told"}), 400
        job["story_regens_left"] -= 1
        job["status"] = "restorying"
        threading.Thread(target=_run_restory_stage, args=(job_id, instruction), daemon=True).start()
        return jsonify({"ok": True})

    return jsonify({"error": "unknown action"}), 400


@app.route("/tracks/<job_id>", methods=["POST"])
def confirm_tracks(job_id):
    """Apply the cover and tagline choices from the tracklist screen, then render.

    Both maps are sparse -- only tracks the user actually changed appear, so an empty
    body means "everything as proposed"."""
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    if not _owns_job(job):
        return jsonify({"error": "not your job"}), 403
    if job.get("status") != "awaiting_tracks":
        return jsonify({"error": "this job isn't waiting for track choices"}), 400

    body = request.get_json(silent=True) or {}
    covers = body.get("covers") or {}
    lines = body.get("lines") or {}

    for key, line in lines.items():
        try:
            i = int(key)
            item = job["standout"][i]
        except (ValueError, IndexError):
            continue
        # Escaped downstream by compose._esc; capped because the slot is one line of a
        # 9:16 frame, not because of anything unsafe.
        item["track"]["reason"] = _clip_beat(line)

    for key, option_id in covers.items():
        try:
            i = int(key)
            item, res = job["standout"][i], job["resolved"][i]
        except (ValueError, IndexError):
            continue
        option = next((o for o in res["media"].get("image_options", [])
                       if o["id"] == option_id), None)
        if option is None or option.get("id") == "art_0":
            continue  # unknown id, or the one already in use
        dest = os.path.join(job["job_dir"], f"track{res['media_index']}_art.jpg")
        # Downloaded only now, and only for a track the user actually changed -- the
        # whole point of offering alternates as URLs. Overwriting the existing filename
        # keeps every downstream path valid; nothing has rendered yet, so there is no
        # cached colour sampled from the old image to invalidate.
        if not providers.download(option["url"], dest):
            _log(job, f"Could not fetch the chosen cover for {item['track']['artist']}.")
            continue
        item["media"]["image"] = dest
        res["media"]["image"] = dest
        res["media"].setdefault("sources", []).append({
            "kind": "image_selected", "source": option["source"],
            "matched": option.get("matched", ""), "url": option.get("link"),
            "license": option.get("license", ""),
        })
        _log(job, f"Cover swapped for {item['track']['artist']} ({option['source']}).")

    job["tracks_confirmed"] = True
    job.pop("track_choices", None)
    _advance_after_media(job_id)
    return jsonify({"ok": True, "status": job["status"]})


@app.route("/cancel/<job_id>", methods=["POST"])
def cancel(job_id):
    """Give up on a job parked at a gate, without waiting out its timeout.

    The honest counterpart to holding jobs open: someone who changes their mind can
    release the render directory immediately instead of it sitting on the volume for
    the next half hour."""
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    if not _owns_job(job):
        return jsonify({"error": "not your job"}), 403
    if job.get("status") not in AWAITING_STATUSES:
        return jsonify({"error": "this job isn't waiting on you"}), 400
    job["status"] = "failed"
    job["error"] = "Cancelled."
    _log(job, "Cancelled by user.")
    analytics.record_job_done(job_id, "failed")
    return jsonify({"ok": True})


@app.route("/upload/<job_id>/<int:track_index>", methods=["POST"])
def upload(job_id, track_index):
    job = JOBS.get(job_id)
    if not _owns_job(job):
        return jsonify({"error": "not your job"}), 403
    if job["status"] != "awaiting_uploads":
        return jsonify({"error": "job not accepting uploads"}), 400
    if "audio_file" not in request.files:
        return jsonify({"error": "no file"}), 400

    file = request.files["audio_file"]
    ext = os.path.splitext(secure_filename(file.filename))[1] or ".mp3"
    dest = os.path.join(job["job_dir"], f"track{track_index}_audio{ext}")
    file.save(dest)

    job["standout"][track_index]["media"]["audio"] = dest
    job["needs_upload"] = [u for u in job["needs_upload"] if u["index"] != track_index]
    _log(job, f"Received manual audio for track {track_index}")

    if not job["needs_upload"]:
        _advance_after_media(job_id)

    return jsonify({"ok": True, "remaining": job["needs_upload"]})


@app.route("/skip/<job_id>/<int:track_index>", methods=["POST"])
def skip_upload(job_id, track_index):
    job = JOBS.get(job_id)
    if not _owns_job(job):
        return jsonify({"error": "not your job"}), 403
    if job["status"] != "awaiting_uploads":
        return jsonify({"error": "job not accepting uploads"}), 400
    if track_index < 0 or track_index >= len(job["standout"]):
        return jsonify({"error": "invalid track index"}), 400

    removed = job["standout"].pop(track_index)
    job["resolved"].pop(track_index)
    job["needs_upload"] = [
        {**u, "index": u["index"] - 1 if u["index"] > track_index else u["index"]}
        for u in job["needs_upload"] if u["index"] != track_index
    ]
    _log(job, f"Skipped {removed['track']['artist']} - {removed['track']['title']} (no audio provided)")

    if not job["standout"]:
        job["status"] = "failed"
        job["error"] = "No tracks left after skipping uploads"
        _log(job, "ERROR: no tracks left to render")
        analytics.record_job_done(job_id, "failed")
        return jsonify({"ok": True, "remaining": [], "status": job["status"]})

    # If the skipped track was the closing one, the new closing track's audio
    # needs to be re-extended to cover the outro card (only if it already has
    # audio resolved -- if it's itself pending upload, leave it be).
    if track_index == len(job["standout"]):
        last_standout = job["standout"][-1]
        if last_standout["media"]["audio"]:
            _extend_closing_audio(job, job["resolved"][-1], job["job_dir"], job["scene_duration"], allow_youtube=job.get("allow_youtube", False), cookie_file=job.get("cookie_file"))
            if job["resolved"][-1]["media"]["audio"]:
                last_standout["media"]["audio"] = job["resolved"][-1]["media"]["audio"]

    if not job["needs_upload"]:
        _advance_after_media(job_id)

    return jsonify({"ok": True, "remaining": job["needs_upload"], "status": job["status"]})


def _served_path(job, clean: bool) -> str:
    """The file to hand out. Falls back to the master only when no watermarked
    copy exists -- which now means a job rendered before watermarking existed,
    not a job whose watermark step failed (that fails the whole render, so an
    unmarked file can never reach an unentitled visitor by accident)."""
    if clean:
        return job["output_path"]
    return job.get("watermarked_path") or job["output_path"]


@app.route("/preview/<job_id>")
def preview_video(job_id):
    """Serves the rendered video inline for the in-page <video> player -- not
    counted as a download (see /download for the actual export/save action).

    Always the watermarked cut, even for the operator. The player is the one
    URL that is trivially readable from the page source, so serving the clean
    master here would hand it to anyone with a network tab open and make the
    download gate meaningless."""
    job = JOBS.get(job_id)
    if not _owns_job(job):
        return jsonify({"error": "not your job"}), 403
    if job["status"] != "done":
        return jsonify({"error": "not ready"}), 400
    return send_file(_served_path(job, clean=False))


def _clean_export_allowed(job_id: str) -> bool:
    """Whether to hand over the un-watermarked master -- and the point where a
    credit is actually spent.

    One credit buys one clean export. accounts.spend is idempotent per job, so
    re-downloading a video this account already paid for costs nothing; that is
    why the job id is threaded all the way down here rather than the decision
    being made from the session alone.

    With the paywall off, everyone gets the clean master and no credit is spent
    -- checked first so a signed-in visitor with a balance isn't quietly charged
    for something currently being given away.
    """
    if not PAYWALL:
        return True
    if access.is_operator():
        return True
    account = current_account()
    if not account:
        return False
    return accounts.spend(account, job_id)


@app.route("/download/<job_id>")
def download(job_id):
    """The export. A credit (or operator status) gets the clean master;
    everyone else gets the same watermarked cut they were watching."""
    job = JOBS.get(job_id)
    if not _owns_job(job):
        return jsonify({"error": "not your job"}), 403
    if job["status"] != "done":
        return jsonify({"error": "not ready"}), 400
    clean = _clean_export_allowed(job_id)
    analytics.record_download(job_id, access.visitor_id())
    suffix = "" if clean else "-watermarked"
    return send_file(
        _served_path(job, clean=clean),
        as_attachment=True,
        download_name=f"promo-{job_id}{suffix}.mp4",
    )


if __name__ == "__main__":
    # Local dev only -- when hosted, gunicorn imports the app and never runs
    # this block.
    #
    # The reloader is on because its absence kept producing "my change didn't
    # apply" confusion: Python modules and Jinja templates are both cached for
    # the life of the process, so editing compose.py or index.html and
    # refreshing the browser showed the old output with nothing to indicate
    # why. use_reloader without debug=True deliberately: the Werkzeug debugger
    # is an arbitrary-code-execution console, which has no business being
    # reachable even locally.
    port = int(os.environ.get("PORT", 5050))
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=True)
