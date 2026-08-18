"""Lightweight local usage analytics: visits, generations, API cost, downloads.

Stored as an append-only JSONL log (server/analytics.jsonl, gitignored) --
no database needed for a single-machine tool. Cost is estimated from each
Anthropic API response's `usage` block against per-model introductory pricing
(https://platform.claude.com/docs/en/about-claude/pricing, in effect through
Aug 31, 2026) -- update PRICING below if pricing changes or a new model is added.
"""
import json
import os
import threading
import time
from datetime import datetime

_DATA_DIR = os.environ.get("DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
ANALYTICS_PATH = os.path.join(_DATA_DIR, "analytics.jsonl")
_lock = threading.Lock()

# Per-MTok. Sonnet is the only model in use; the Opus row stays so historical rows in
# analytics.jsonl (from when "Advanced" meant Opus) still price correctly on read.
PRICING = {
    "claude-sonnet-5": {"input": 2, "output": 10, "cache_write": 2.5, "cache_read": 0.2},
    "claude-opus-4-8": {"input": 5, "output": 25, "cache_write": 6.25, "cache_read": 0.5},
}
DEFAULT_PRICING = PRICING["claude-sonnet-5"]
PRICE_PER_WEB_SEARCH = 10 / 1_000


def _append(event: dict):
    event["ts"] = time.time()
    line = json.dumps(event)
    with _lock:
        with open(ANALYTICS_PATH, "a") as f:
            f.write(line + "\n")


def cost_from_usage(usage, model: str | None = None) -> float:
    """usage: an Anthropic SDK Usage object (or plain dict) from response.usage."""
    def get(obj, name, default=0):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(name, default) or default
        return getattr(obj, name, default) or default

    prices = PRICING.get(model, DEFAULT_PRICING)
    server_tool_use = get(usage, "server_tool_use")
    return (
        get(usage, "input_tokens") * prices["input"] / 1_000_000
        + get(usage, "output_tokens") * prices["output"] / 1_000_000
        + get(usage, "cache_creation_input_tokens") * prices["cache_write"] / 1_000_000
        + get(usage, "cache_read_input_tokens") * prices["cache_read"] / 1_000_000
        + get(server_tool_use, "web_search_requests") * PRICE_PER_WEB_SEARCH
    )


def _usage_fields(usage) -> dict:
    """The token counts behind a cost, recorded alongside it so a surprising bill can be
    traced to the field that caused it instead of re-run to find out. Search-heavy calls
    grow their own input on every server-side iteration, which cost alone doesn't show."""
    def get(obj, name, default=0):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(name, default) or default
        return getattr(obj, name, default) or default

    return {
        "in": get(usage, "input_tokens"),
        "out": get(usage, "output_tokens"),
        "cache_w": get(usage, "cache_creation_input_tokens"),
        "cache_r": get(usage, "cache_read_input_tokens"),
        "searches": get(get(usage, "server_tool_use"), "web_search_requests"),
    }


def record_api_call(job_id: str | None, label: str, usage, model: str | None = None) -> float:
    cost = cost_from_usage(usage, model)
    _append({"type": "api_call", "job_id": job_id, "label": label, "cost_usd": round(cost, 6),
             "model": model, "tokens": _usage_fields(usage)})
    return cost


# Substrings that mark a request as automated. Matched case-insensitively against the
# user agent. Deliberately a short list of the honest ones plus the tools that turn up in
# this app's logs -- there is no winning an arms race here, and the point is not a perfect
# count. It is that "unique visitors" stopped meaning "people".
#
# The mechanism that inflated it: owner_id() mints a fresh random id for any request with
# no session cookie and records a visit against it. A browser keeps that cookie and stays
# one visitor forever; a scanner that discards cookies is a brand new visitor every single
# time it probes "/". The hosted log is thick with them -- /wp-admin/install.php,
# /phpinfo, /.env, /wp-login.php -- and each of those runs also pulls "/" at least once.
_BOT_UA_MARKERS = (
    "bot", "crawler", "spider", "slurp", "curl", "wget", "python-requests",
    "scrapy", "httpclient", "go-http-client", "libwww", "headlesschrome",
    "facebookexternalhit", "phantomjs", "masscan", "zgrab", "nmap",
)


def looks_automated(user_agent: str | None) -> bool:
    """A missing user agent counts as automated: every real browser sends one."""
    ua = (user_agent or "").strip().lower()
    if not ua:
        return True
    return any(marker in ua for marker in _BOT_UA_MARKERS)


def record_visit(route: str, visitor_id: str, user_agent: str | None = None):
    # The agent is stored, not judged, so the rule above can be changed later and
    # re-applied to everything already recorded. Rows written before this existed have
    # no "ua" at all, which is why summary() reports how many it could not classify
    # rather than quietly counting them as people.
    _append({"type": "visit", "route": route, "visitor_id": visitor_id,
             "ua": (user_agent or "")[:200]})


def record_job_start(job_id: str, route: str, visitor_id: str):
    _append({"type": "job_start", "job_id": job_id, "route": route, "visitor_id": visitor_id})


def record_download(job_id: str, visitor_id: str):
    _append({"type": "download", "job_id": job_id, "visitor_id": visitor_id})


def record_job_done(job_id: str, status: str):
    """status: "done" or "failed" -- paired with the job_start event's
    timestamp to compute run time."""
    _append({"type": "job_done", "job_id": job_id, "job_status": status})


def _read_all() -> list[dict]:
    if not os.path.exists(ANALYTICS_PATH):
        return []
    events = []
    with open(ANALYTICS_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _format_duration(seconds: float) -> str:
    seconds = round(seconds)
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def summary() -> dict:
    events = _read_all()
    visits = [e for e in events if e.get("type") == "visit"]
    job_starts = [e for e in events if e.get("type") == "job_start"]
    api_calls = [e for e in events if e.get("type") == "api_call"]
    downloads = [e for e in events if e.get("type") == "download"]
    job_dones = [e for e in events if e.get("type") == "job_done"]

    # People, machines, and the ones we cannot say. A visitor counts as a person only if
    # every visit it made carried a browser-shaped agent; one automated hit is enough to
    # disqualify an id, since a scanner never reuses one anyway. "Unclassified" is the
    # backlog: rows written before the agent was recorded at all.
    people, machines, unknown = set(), set(), set()
    for e in visits:
        vid = e.get("visitor_id")
        if not vid:
            continue
        if "ua" not in e:
            unknown.add(vid)
        elif looks_automated(e.get("ua")):
            machines.add(vid)
        else:
            people.add(vid)
    people -= machines
    unknown -= machines | people

    unique_visitors = people
    total_generations = len(job_starts)
    total_cost = sum(e.get("cost_usd", 0) for e in api_calls)
    downloaded_jobs = {e["job_id"] for e in downloads if e.get("job_id")}
    visitor_by_job = {j["job_id"]: j.get("visitor_id") for j in job_starts if j.get("job_id")}

    cost_by_job: dict[str, float] = {}
    for e in api_calls:
        jid = e.get("job_id")
        if jid:
            cost_by_job[jid] = cost_by_job.get(jid, 0) + e.get("cost_usd", 0)

    start_ts_by_job = {j["job_id"]: j["ts"] for j in job_starts}
    # last job_done per job_id (in case of odd double-writes) wins
    done_by_job: dict[str, dict] = {}
    for e in job_dones:
        jid = e.get("job_id")
        if jid:
            done_by_job[jid] = e

    duration_by_job: dict[str, float] = {}
    for jid, done in done_by_job.items():
        start_ts = start_ts_by_job.get(jid)
        if start_ts is not None:
            duration_by_job[jid] = max(0.0, done["ts"] - start_ts)

    # Only jobs that finished, and only the ones that finished by producing a video.
    # A failed job's duration is not a run time: a review screen nobody came back to is
    # marked failed by the expiry sweep, and that sweep is driven by /status polls and by
    # the next render rather than by a clock -- so an abandoned gate can sit for hours and
    # then record every one of them as though it had been working. That is what put the
    # average at nearly two hours.
    done_job_ids = {jid for jid, d in done_by_job.items() if d.get("job_status") == "done"}
    finished_durations = [d for jid, d in duration_by_job.items() if jid in done_job_ids]
    avg_duration = sum(finished_durations) / len(finished_durations) if finished_durations else 0
    # Cost per video delivered, not per job started. Blending in the ones that died in
    # the first second, and the manual-selection jobs that call no model at all, answers
    # a question nobody asked and always answers it low.
    delivered_cost = sum(cost_by_job.get(jid, 0) for jid in done_job_ids)

    recent_jobs = []
    for j in job_starts[-25:][::-1]:
        jid = j["job_id"]
        done = done_by_job.get(jid)
        duration = duration_by_job.get(jid)
        recent_jobs.append({
            "job_id": jid,
            "when": datetime.fromtimestamp(j["ts"]).strftime("%Y-%m-%d %H:%M"),
            "route": j.get("route"),
            "cost_usd": round(cost_by_job.get(jid, 0), 4),
            "downloaded": jid in downloaded_jobs,
            "status": done.get("job_status") if done else "in progress",
            "duration": _format_duration(duration) if duration is not None else "—",
            # Who ran it. The id is a random per-browser token, not a name -- there are
            # no accounts here -- but it is stable, so the same id down several rows is
            # one person's afternoon and that is the thing worth seeing.
            "visitor": visitor_by_job.get(jid) or "—",
        })

    return {
        "unique_visitors": len(unique_visitors),
        "bot_visitors": len(machines),
        "unclassified_visitors": len(unknown),
        "total_visits": len(visits),
        "total_generations": total_generations,
        "videos_delivered": len(done_job_ids),
        "total_cost_usd": round(total_cost, 4),
        "avg_cost_per_generation_usd": round(delivered_cost / len(done_job_ids), 4) if done_job_ids else 0,
        "videos_downloaded": len(downloaded_jobs),
        # Of the videos that exist to download, not of every job ever started.
        "download_rate": round(len(downloaded_jobs) / len(done_job_ids), 2) if done_job_ids else 0,
        "avg_duration": _format_duration(avg_duration) if finished_durations else "—",
        "recent_jobs": recent_jobs,
    }
