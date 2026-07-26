import json
import os
import secrets
import threading
import traceback
import uuid
from functools import wraps

from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, send_file, send_from_directory, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

import analytics
import compose
import curator
import media_finder
import users
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

DEFAULT_SHOW_NAME = "Pop Lock"
VIDEO_RATIO_TARGET = 0.6
MAX_EXTRA_FETCH_ATTEMPTS = 8
PACE_SCENE_DURATION = {"chill": 11, "normal": 8, "fast": 6, "hyper": 4.5}


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


# manage_users.py operates on local files, which is useless once DATA_DIR
# points at a remote volume you can't shell into directly -- this env var
# bootstraps the first admin on startup instead. Idempotent (safe to leave
# set permanently, or remove after first deploy).
_initial_admin = os.environ.get("INITIAL_ADMIN_EMAIL")
if _initial_admin:
    users.add_admin(_initial_admin)


def current_username() -> str | None:
    return session.get("username")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_username():
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        username = current_username()
        if not username:
            return redirect(url_for("login", next=request.path))
        if not users.is_admin(username):
            return jsonify({"error": "admin only"}), 403
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


def _resolve_theme(theme_mode, theme_value, tracks, job_id=None):
    """Returns a full theme dict: {"palettes": [...], "motion": "...", "frame": "..."}."""
    if theme_mode == "preset" and theme_value in curator.PRESET_THEMES:
        return curator.PRESET_THEMES[theme_value]
    if theme_mode == "saved":
        saved = _load_saved_themes()
        if theme_value in saved:
            return saved[theme_value]
        return curator.DEFAULT_THEME
    if theme_mode == "custom" and theme_value and theme_value.strip():
        return curator.theme_from_description(theme_value.strip(), job_id=job_id)
    # auto (default)
    return curator.suggest_theme(tracks, job_id=job_id)


def _resolve_standout_media(job, ranked, job_dir, num_standout, scene_duration, cookie_file=None):
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
        result = media_finder.process_track(track_obj, job_dir, media_index, clip_duration=scene_duration, cookie_file=cookie_file)
        this_index = media_index
        media_index += 1
        has_video = bool(result["video"])
        _log(job, f"  video={'yes' if result['video'] else 'no'} audio={'yes' if result['audio'] else 'no'} image={'yes' if result['image'] else 'no'}")

        entry = {
            "track": {"artist": cand["artist"], "title": cand["title"], "reason": cand.get("reason", "")},
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


def _resolve_manual_media(job, picks, job_dir, scene_duration, language, job_id=None, personal=False, cookie_file=None, use_search=True):
    """picks: list of {artist, title, album}. Unlike auto mode, there's no backup
    pool to draw from -- every pick is attempted once, in order, and only true
    dead-ends (no video, no image, no audio found anywhere) get dropped."""
    tracks_for_trivia = [Track(artist=p["artist"], title=p["title"], album=p.get("album")) for p in picks]
    _log(job, f"Checking for notable trivia on {len(picks)} selected tracks...")
    trivia_map = curator.trivia_for_tracks(tracks_for_trivia, language=language, job_id=job_id, personal=personal, use_search=use_search)

    resolved = []
    media_index = 0
    for p in picks:
        track_obj = Track(artist=p["artist"], title=p["title"], album=p.get("album"))
        _log(job, f"Fetching media for {p['artist']} - {p['title']}...")
        result = media_finder.process_track(track_obj, job_dir, media_index, clip_duration=scene_duration, cookie_file=cookie_file)
        this_index = media_index
        media_index += 1
        has_video = bool(result["video"])
        _log(job, f"  video={'yes' if result['video'] else 'no'} audio={'yes' if result['audio'] else 'no'} image={'yes' if result['image'] else 'no'}")

        if not result["video"] and not result["image"] and not result["audio"]:
            _log(job, f"  couldn't find anything for {p['artist']} - {p['title']} — skipping it")
            continue

        reason = trivia_map.get((p["artist"].lower(), p["title"].lower()), "")
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


def _extend_closing_audio(job, last_entry, job_dir, scene_duration, cookie_file=None):
    extended = scene_duration + compose.OUTRO_DURATION
    track_obj = last_entry["track_obj"]
    idx = last_entry["media_index"]
    _log(job, f"Extending closing track's audio to cover the outro card...")
    try:
        if last_entry["has_video"]:
            video_result = media_finder.find_youtube_video_clip(
                track_obj, job_dir, idx, clip_duration=scene_duration, audio_duration=extended, cookie_file=cookie_file,
            )
            if video_result and video_result.get("audio"):
                last_entry["media"]["audio"] = video_result["audio"]
        else:
            audio_path = media_finder.find_youtube_audio_only(track_obj, job_dir, idx, audio_duration=extended, cookie_file=cookie_file)
            if audio_path:
                last_entry["media"]["audio"] = audio_path
    except Exception as e:
        _log(job, f"  could not extend closing audio ({e}) — outro may be quiet")


def _run_fetch_stage(job_id, tracklist_text, show_name, episode_label, num_standout, pace, theme_mode, theme_value, selection_mode, manual_picks, language, personal=False, cookie_file=None, use_search=True):
    job = JOBS[job_id]
    job["cookie_file"] = cookie_file
    job_dir = os.path.join(RENDERS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    scene_duration = PACE_SCENE_DURATION.get(pace, PACE_SCENE_DURATION["normal"])

    try:
        job["status"] = "parsing"
        tracks = parse_tracklist(tracklist_text)
        if not tracks:
            raise ValueError("No tracks found in tracklist")
        _log(job, f"Parsed {len(tracks)} tracks")
        if not cookie_file:
            _log(job, "No YouTube cookies linked for your account — some videos may fail to fetch (add them under Account).")

        job["status"] = "theming"
        _log(job, "Choosing a visual theme...")
        theme = _resolve_theme(theme_mode, theme_value, tracks, job_id=job_id)
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
            resolved = _resolve_manual_media(job, picks, job_dir, scene_duration, language, job_id=job_id, personal=personal, cookie_file=cookie_file, use_search=use_search)
        else:
            job["status"] = "curating"
            _log(job, "Ranking standout tracks" + (" (web research)..." if use_search else "..."))
            ranked = curator.curate_ranked(tracks, n=num_standout, language=language, job_id=job_id, personal=personal, use_search=use_search)
            _log(job, "Ranked candidates: " + ", ".join(f"{r['artist']} - {r['title']}" for r in ranked))

            job["status"] = "fetching_media"
            resolved = _resolve_standout_media(job, ranked, job_dir, num_standout, scene_duration, cookie_file=cookie_file)

        if not resolved:
            raise ValueError("Could not resolve media for any standout track")

        job["resolved"] = resolved
        _extend_closing_audio(job, resolved[-1], job_dir, scene_duration, cookie_file=cookie_file)

        # Exclude by artist too, not just exact track match -- the "also in this
        # episode" list shouldn't repeat an artist already featured in the video.
        chosen_artists = {r["track"]["artist"].lower() for r in resolved}
        remaining = [
            {"artist": t.artist, "title": t.title}
            for t in tracks
            if t.artist.lower() not in chosen_artists
        ]

        standout = [{"track": r["track"], "media": {
            "video": os.path.relpath(r["media"]["video"], PROJECT_DIR) if r["media"]["video"] else None,
            "audio": os.path.relpath(r["media"]["audio"], PROJECT_DIR) if r["media"]["audio"] else None,
            "image": os.path.relpath(r["media"]["image"], PROJECT_DIR) if r["media"]["image"] else None,
        }} for r in resolved]

        needs_upload = [
            {"index": i, "artist": s["track"]["artist"], "title": s["track"]["title"]}
            for i, s in enumerate(standout) if not s["media"]["audio"]
        ]

        video_count = sum(1 for r in resolved if r["has_video"])
        _log(job, f"{video_count}/{len(resolved)} standout tracks have video ({round(100 * video_count / len(resolved))}%)")

        job["standout"] = standout
        job["remaining"] = remaining
        job["show_name"] = show_name or DEFAULT_SHOW_NAME
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


def _start_render(job_id):
    thread = threading.Thread(target=_run_render_stage, args=(job_id,), daemon=True)
    thread.start()


def _run_render_stage(job_id):
    job = JOBS[job_id]
    try:
        job["status"] = "composing"
        _log(job, "Building composition...")
        html = compose.build_composition_html(
            job["show_name"], job["episode_label"], job["standout"], job["remaining"],
            job["theme"], job["scene_duration"], language=job.get("language", "en"),
        )
        with open(os.path.join(PROJECT_DIR, "index.html"), "w") as f:
            f.write(html)

        job["status"] = "rendering"
        _log(job, "Rendering MP4 (this can take a minute)...")
        output_rel = os.path.relpath(os.path.join(job["job_dir"], "output.mp4"), PROJECT_DIR)
        compose.render_video(PROJECT_DIR, output_rel, quality="standard")

        job["status"] = "done"
        job["output_path"] = os.path.join(job["job_dir"], "output.mp4")
        _log(job, "Done!")
    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
        _log(job, f"ERROR: {e}")
        traceback.print_exc()


@app.route("/login")
def login():
    return render_template("login.html", error=None, next=request.args.get("next", ""))


@app.route("/auth/google")
def auth_google():
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        return "Google sign-in isn't configured yet (missing GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET in .env).", 503
    session["post_login_redirect"] = request.args.get("next") or url_for("index")
    redirect_uri = url_for("auth_google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route("/auth/google/callback")
def auth_google_callback():
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        return "Google sign-in isn't configured yet.", 503
    token = oauth.google.authorize_access_token()
    userinfo = token.get("userinfo") or {}
    email = userinfo.get("email")
    if not email or not userinfo.get("email_verified"):
        return render_template("login.html", error="Google didn't return a verified email address.", next=""), 400

    session.clear()
    session["username"] = email.strip().lower()
    session.permanent = True
    next_url = session.pop("post_login_redirect", None) or url_for("index")
    return redirect(next_url)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    username = current_username()
    error = None
    if request.method == "POST":
        file = request.files.get("cookies_file")
        if not file or not file.filename:
            error = "Choose a cookies.txt file first."
        else:
            data = file.read()
            if len(data) > 1_000_000:
                error = "That file is too large to be a cookies.txt export."
            elif b"\t" not in data and b"Netscape" not in data:
                error = "That doesn't look like a Netscape-format cookies.txt export."
            else:
                users.save_cookies(username, data)
    return render_template(
        "account.html", username=username,
        has_cookies=users.has_cookies(username), error=error,
    )


@app.route("/")
def index():
    analytics.record_visit("/", current_username())
    # personal_mode gates admin-only UI: custom/saved theme tools and the
    # Analytics link. Pop Lock curation personalization is separate -- it
    # keys off the show name typed into the form (see /start).
    return render_template(
        "index.html", default_show_name=DEFAULT_SHOW_NAME, username=current_username(),
        presets=list(curator.PRESET_THEMES.keys()), personal_mode=users.is_admin(current_username()),
    )


@app.route("/analytics")
@admin_required
def analytics_page():
    return render_template("analytics.html", stats=analytics.summary())


@app.route("/themes")
def themes():
    return jsonify({"presets": list(curator.PRESET_THEMES.keys()), "saved": _load_saved_themes()})


@app.route("/themes/save", methods=["POST"])
@login_required
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
@login_required
def start():
    username = current_username()
    is_admin_user = users.is_admin(username)

    tracklist_text = request.form.get("tracklist", "")
    show_name = request.form.get("show_name", "").strip() or DEFAULT_SHOW_NAME
    episode_label = request.form.get("episode_label", "")
    num_standout = max(2, min(15, int(request.form.get("num_standout", 5))))
    pace = request.form.get("pace", "normal")
    theme_mode = request.form.get("theme_mode", "auto")
    theme_value = request.form.get("theme_value", "")
    selection_mode = request.form.get("selection_mode", "auto")
    language = request.form.get("language", "en")
    # Custom/saved themes are admin (Roni) only -- enforced here too, not just
    # hidden in the UI, since form fields can be submitted directly.
    if theme_mode in ("custom", "saved") and not is_admin_user:
        theme_mode = "auto"
    # Pop Lock personalization keys off the show name itself, admin only --
    # a colleague typing "Pop Lock" as their own show name doesn't get it.
    personal = is_admin_user and show_name.strip().lower() in ("pop lock", "poplock")
    # Web search is a real cost driver (~$0.01/search on top of tokens) --
    # keep it to admin-triggered runs only.
    use_search = is_admin_user
    manual_picks = []
    if selection_mode == "manual":
        try:
            manual_picks = json.loads(request.form.get("manual_tracks", "[]"))
        except Exception:
            manual_picks = []

    cookie_file = users.cookie_path(username) if users.has_cookies(username) else None

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued", "log": [], "error": None, "needs_upload": [], "owner": username}

    analytics.record_job_start(job_id, "/", username)

    thread = threading.Thread(
        target=_run_fetch_stage,
        args=(job_id, tracklist_text, show_name, episode_label, num_standout, pace, theme_mode, theme_value, selection_mode, manual_picks, language, personal, cookie_file, use_search),
        daemon=True,
    )
    thread.start()
    return jsonify({"job_id": job_id})


def _owns_job(job) -> bool:
    return job is not None and job.get("owner") == current_username()


@app.route("/status/<job_id>")
@login_required
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
@login_required
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

    job["standout"][track_index]["media"]["audio"] = os.path.relpath(dest, PROJECT_DIR)
    job["needs_upload"] = [u for u in job["needs_upload"] if u["index"] != track_index]
    _log(job, f"Received manual audio for track {track_index}")

    if not job["needs_upload"]:
        job["status"] = "ready_to_render"
        _log(job, "All media resolved — ready to render.")
        _start_render(job_id)

    return jsonify({"ok": True, "remaining": job["needs_upload"]})


@app.route("/skip/<job_id>/<int:track_index>", methods=["POST"])
@login_required
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
        return jsonify({"ok": True, "remaining": [], "status": job["status"]})

    # If the skipped track was the closing one, the new closing track's audio
    # needs to be re-extended to cover the outro card (only if it already has
    # audio resolved -- if it's itself pending upload, leave it be).
    if track_index == len(job["standout"]):
        last_standout = job["standout"][-1]
        if last_standout["media"]["audio"]:
            _extend_closing_audio(job, job["resolved"][-1], job["job_dir"], job["scene_duration"], cookie_file=job.get("cookie_file"))
            if job["resolved"][-1]["media"]["audio"]:
                last_standout["media"]["audio"] = os.path.relpath(job["resolved"][-1]["media"]["audio"], PROJECT_DIR)

    if not job["needs_upload"]:
        job["status"] = "ready_to_render"
        _log(job, "All media resolved — ready to render.")
        _start_render(job_id)

    return jsonify({"ok": True, "remaining": job["needs_upload"], "status": job["status"]})


@app.route("/preview/<job_id>")
@login_required
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
@login_required
def download(job_id):
    job = JOBS.get(job_id)
    if not _owns_job(job):
        return jsonify({"error": "not your job"}), 403
    if job["status"] != "done":
        return jsonify({"error": "not ready"}), 400
    analytics.record_download(job_id, current_username())
    return send_file(job["output_path"], as_attachment=True, download_name=f"promo-{job_id}.mp4")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
