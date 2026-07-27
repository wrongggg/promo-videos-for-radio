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

# Per-MTok, matching curator.MODEL_SIMPLE / MODEL_ADVANCED.
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


def record_api_call(job_id: str | None, label: str, usage, model: str | None = None) -> float:
    cost = cost_from_usage(usage, model)
    _append({"type": "api_call", "job_id": job_id, "label": label, "cost_usd": round(cost, 6), "model": model})
    return cost


def record_visit(route: str, visitor_id: str):
    _append({"type": "visit", "route": route, "visitor_id": visitor_id})


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

    unique_visitors = {e["visitor_id"] for e in visits if e.get("visitor_id")}
    total_generations = len(job_starts)
    total_cost = sum(e.get("cost_usd", 0) for e in api_calls)
    downloaded_jobs = {e["job_id"] for e in downloads if e.get("job_id")}

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

    finished_durations = list(duration_by_job.values())
    avg_duration = sum(finished_durations) / len(finished_durations) if finished_durations else 0

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
        })

    return {
        "unique_visitors": len(unique_visitors),
        "total_visits": len(visits),
        "total_generations": total_generations,
        "total_cost_usd": round(total_cost, 4),
        "avg_cost_per_generation_usd": round(total_cost / total_generations, 4) if total_generations else 0,
        "videos_downloaded": len(downloaded_jobs),
        "download_rate": round(len(downloaded_jobs) / total_generations, 2) if total_generations else 0,
        "avg_duration": _format_duration(avg_duration) if finished_durations else "—",
        "recent_jobs": recent_jobs,
    }
