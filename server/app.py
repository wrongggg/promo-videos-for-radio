import json
import os
import secrets
import threading
import traceback
import uuid
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, send_from_directory, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

import access
import analytics
import audio_analysis
import compose
import curator
import languages
import media_finder
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
VIDEO_RATIO_TARGET = 0.6
MAX_EXTRA_FETCH_ATTEMPTS = 8
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


def _resolve_standout_media(job, ranked, job_dir, num_standout, scene_duration, allow_youtube=False, cookie_file=None, show_info=False):
    """Fetch media for ranked candidates (best first), enforcing: at least
    VIDEO_RATIO_TARGET of the final picks have video, and extra candidates are
    pulled in to satisfy that when the top picks don't have enough video."""
    resolved = []
    media_index = 0
    attempts = 0

    for cand in ranked:
        if attempts >= num_standout + MAX_EXTRA_FETCH_ATTEMPTS:
            break
        if len(resolved) >= num_standout:
            video_count = sum(1 for r in resolved if r["has_video"])
            if video_count / len(resolved) >= VIDEO_RATIO_TARGET:
                break
        attempts += 1

        track_obj = Track(artist=cand["artist"], title=cand["title"], album=cand.get("album"))
        _log(job, f"Fetching media for {cand['artist']} - {cand['title']}...")
        result = media_finder.process_track(track_obj, job_dir, media_index, clip_duration=scene_duration, allow_youtube=allow_youtube, cookie_file=cookie_file)
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

        if len(resolved) < num_standout:
            resolved.append(entry)
        else:
            video_count = sum(1 for r in resolved if r["has_video"])
            if has_video and video_count / len(resolved) < VIDEO_RATIO_TARGET:
                for i in range(len(resolved) - 1, -1, -1):
                    if not resolved[i]["has_video"]:
                        _log(job, f"  swapping in for {resolved[i]['track']['artist']} - {resolved[i]['track']['title']} (needed more video)")
                        resolved[i] = entry
                        break

    if resolved and not resolved[0]["has_video"]:
        for i, r in enumerate(resolved):
            if r["has_video"]:
                resolved[0], resolved[i] = resolved[i], resolved[0]
                _log(job, "Reordered so the video opens on footage.")
                break

    return resolved


def _resolve_manual_media(job, picks, job_dir, scene_duration, language, job_id=None, personal=False, allow_youtube=False, cookie_file=None, use_search=True, model=curator.MODEL_SIMPLE, show_info=False):
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
        result = media_finder.process_track(track_obj, job_dir, media_index, clip_duration=scene_duration, allow_youtube=allow_youtube, cookie_file=cookie_file)
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

    if resolved and not resolved[0]["has_video"]:
        for i, r in enumerate(resolved):
            if r["has_video"]:
                resolved[0], resolved[i] = resolved[i], resolved[0]
                _log(job, "Reordered so the video opens on footage.")
                break

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


def _run_fetch_stage(job_id, tracklist_text, show_name, episode_label, num_standout, pace, theme_mode, theme_value, selection_mode, manual_picks, language, personal=False, allow_youtube=False, cookie_file=None, use_search=True, model=curator.MODEL_SIMPLE, show_info=False, logo_path=None):
    job = JOBS[job_id]
    job["cookie_file"] = cookie_file
    job["allow_youtube"] = allow_youtube
    job["logo_path"] = logo_path
    job_dir = os.path.join(RENDERS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    scene_duration = PACE_SCENE_DURATION.get(pace, PACE_SCENE_DURATION["normal"])

    try:
        job["status"] = "parsing"
        tracks = parse_tracklist(tracklist_text)
        if not tracks:
            raise ValueError("No tracks found in tracklist")
        _log(job, f"Parsed {len(tracks)} tracks")

        job["status"] = "theming"
        _log(job, "Choosing a visual theme...")
        theme = _resolve_theme(theme_mode, theme_value, tracks, job_id=job_id, model=model)
        job["theme"] = theme

        if selection_mode == "manual":
            by_key = {(t.artist.lower(), t.title.lower()): t for t in tracks}
            picks = []
            for p in manual_picks:
                t = by_key.get((p.get("artist", "").lower(), p.get("title", "").lower()))
                if t:
                    picks.append({"artist": t.artist, "title": t.title, "album": t.album})
            if not picks:
                raise ValueError("No tracks were selected")
            job["status"] = "curating"
            job["status"] = "fetching_media"
            resolved = _resolve_manual_media(job, picks, job_dir, scene_duration, language, job_id=job_id, personal=personal, allow_youtube=allow_youtube, cookie_file=cookie_file, use_search=use_search, model=model, show_info=show_info)
        else:
            job["status"] = "curating"
            _log(job, "Ranking standout tracks" + (" (web research)..." if use_search else "..."))
            ranked = curator.curate_ranked(tracks, n=num_standout, language=language, job_id=job_id, personal=personal, use_search=use_search, model=model)
            _log(job, "Ranked candidates: " + ", ".join(f"{r['artist']} - {r['title']}" for r in ranked))

            job["status"] = "fetching_media"
            resolved = _resolve_standout_media(job, ranked, job_dir, num_standout, scene_duration, allow_youtube=allow_youtube, cookie_file=cookie_file, show_info=show_info)

        if not resolved:
            raise ValueError("Could not resolve media for any standout track")

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
        job["job_dir"] = job_dir
        job["scene_duration"] = scene_duration
        job["language"] = language
        job["needs_upload"] = needs_upload

        if needs_upload:
            job["status"] = "awaiting_uploads"
            _log(job, "Waiting on manual audio uploads before rendering.")
        else:
            job["status"] = "ready_to_render"
            _log(job, "All media resolved — ready to render.")
            _start_render(job_id)

    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
        _log(job, f"ERROR: {e}")
        traceback.print_exc()
        analytics.record_job_done(job_id, "failed")


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
    try:
        job["status"] = "composing"
        _analyze_scene_audio(job)
        visuals.install_vendor(PROJECT_DIR)
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
        with open(os.path.join(PROJECT_DIR, "index.html"), "w") as f:
            f.write(html)

        job["status"] = "rendering"
        _log(job, "Rendering MP4 (this can take a minute)...")
        output_rel = _composition_path(os.path.join(job["job_dir"], "output.mp4"))
        compose.render_video(PROJECT_DIR, output_rel, quality="standard")

        job["status"] = "done"
        job["output_path"] = os.path.join(job["job_dir"], "output.mp4")
        _log(job, "Done!")
        analytics.record_job_done(job_id, "done")
    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
        _log(job, f"ERROR: {e}")
        traceback.print_exc()
        analytics.record_job_done(job_id, "failed")


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
        theme_preview_css=styles.thumbnail_css(),
        theme_layout_css=styles.preview_layout_css(),
        theme_choices=styles.choices({
            k: (t["palettes"] or [{}])[0] for k, t in curator.PRESET_THEMES.items()
        }),
        personal_mode=access.is_operator(),
        station_mode=access.is_station(),
        station_logo_url=url_for("static", filename=os.path.basename(compose.DEFAULT_LOGO_PATH)),
        max_manual_tracks=MAX_MANUAL_TRACKS_NON_ADMIN,
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
    # tokens and render CPU, so an anonymous visitor gets a daily cap. The
    # operator is exempt.
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
    num_standout = max(2, min(15, int(request.form.get("num_standout", 5))))
    pace = request.form.get("pace", "normal")
    theme_mode = request.form.get("theme_mode", "auto")
    theme_value = request.form.get("theme_value", "")
    selection_mode = request.form.get("selection_mode", "auto")
    language = languages.normalize(request.form.get("language"))
    # Custom/saved themes are admin (Roni) only -- enforced here too, not just
    # hidden in the UI, since form fields can be submitted directly.
    if theme_mode in ("custom", "saved") and not is_admin_user:
        theme_mode = "auto"
    # AI track curation is admin only -- everyone else picks their own tracks,
    # capped so a colleague can't kick off a 15-track curation run.
    if not is_admin_user:
        selection_mode = "manual"
    # Pop Lock personalization keys off the show name itself, admin only --
    # a colleague typing "Pop Lock" as their own show name doesn't get it.
    personal = is_admin_user and show_name.strip().lower() in ("pop lock", "poplock")
    # Colleagues always get "simple" (cheap model, no search). Admin can pick
    # "simple" too, to preview exactly what colleagues experience, or
    # "advanced" for Opus + web research.
    quality_mode = request.form.get("quality_mode", "simple")
    if not is_admin_user or quality_mode not in ("simple", "advanced"):
        quality_mode = "simple"
    use_search = quality_mode == "advanced"
    model = curator.MODEL_ADVANCED if quality_mode == "advanced" else curator.MODEL_SIMPLE
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
    # Track description lines stay an operator feature for now.
    show_info = is_admin_user
    allow_youtube = is_admin_user
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

    analytics.record_job_start(job_id, "/", owner)

    thread = threading.Thread(
        target=_run_fetch_stage,
        args=(job_id, tracklist_text, show_name, episode_label, num_standout, pace, theme_mode, theme_value, selection_mode, manual_picks, language, personal, allow_youtube, cookie_file, use_search, model, show_info, logo_path),
        daemon=True,
    )
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
    })


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
        job["status"] = "ready_to_render"
        _log(job, "All media resolved — ready to render.")
        _start_render(job_id)

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
        job["status"] = "ready_to_render"
        _log(job, "All media resolved — ready to render.")
        _start_render(job_id)

    return jsonify({"ok": True, "remaining": job["needs_upload"], "status": job["status"]})


@app.route("/preview/<job_id>")
def preview_video(job_id):
    """Serves the rendered video inline for the in-page <video> player -- not
    counted as a download (see /download for the actual export/save action)."""
    job = JOBS.get(job_id)
    if not _owns_job(job):
        return jsonify({"error": "not your job"}), 403
    if job["status"] != "done":
        return jsonify({"error": "not ready"}), 400
    return send_file(job["output_path"])


@app.route("/download/<job_id>")
def download(job_id):
    """The export."""
    job = JOBS.get(job_id)
    if not _owns_job(job):
        return jsonify({"error": "not your job"}), 403
    if job["status"] != "done":
        return jsonify({"error": "not ready"}), 400
    analytics.record_download(job_id, access.visitor_id())
    return send_file(job["output_path"], as_attachment=True, download_name=f"promo-{job_id}.mp4")


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
