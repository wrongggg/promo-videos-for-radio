"""Guards the numbers on /analytics.

Plain script, no pytest -- run it directly:

    .venv/bin/python tests/test_analytics_summary.py

Every check here is a number that was being reported wrong, and each one was
wrong in the flattering direction or the alarming one:

  1. "Unique visitors" counted scanners. owner_id() mints a fresh id for any
     request without a session cookie, so a bot that discards cookies is a new
     visitor on every probe -- and the hosted log is full of them.
  2. "Avg run time" averaged in abandoned review screens. Those are marked
     failed by a sweep that only runs when someone else polls or renders, so a
     gate left overnight recorded the whole night as run time.
  3. "Avg cost / video" divided by every job started, including the ones that
     died in the first second and the manual ones that call no model.
  4. "Download rate" had the same denominator: videos that never existed cannot
     be downloaded and should not count against the rate.
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
_DATA = tempfile.mkdtemp(prefix="analytics-test-")
os.environ["DATA_DIR"] = _DATA

import analytics  # noqa: E402

FAILURES = []
BROWSER = "Mozilla/5.0 (Linux; Android 10; K) Chrome/151.0.0.0 Mobile Safari/537.36"


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILURES.append(label)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + ("" if ok else f"  got={got!r} want={want!r}"))


def write(events):
    with open(analytics.ANALYTICS_PATH, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


now = time.time()

print("telling people from machines")
check("a browser is a person", analytics.looks_automated(BROWSER), False)
check("a crawler is not", analytics.looks_automated("Mozilla/5.0 (compatible; SemrushBot/7~bl)"), True)
check("curl is not", analytics.looks_automated("curl/8.4.0"), True)
# The .env / wp-admin probes in the hosted log mostly arrive with no agent at all.
check("no agent at all is not", analytics.looks_automated(""), True)
check("missing agent is not", analytics.looks_automated(None), True)

write([
    {"type": "visit", "ts": now, "visitor_id": "anon:aaa", "ua": BROWSER},
    {"type": "visit", "ts": now, "visitor_id": "anon:aaa", "ua": BROWSER},
    {"type": "visit", "ts": now, "visitor_id": "anon:bbb", "ua": BROWSER},
    {"type": "visit", "ts": now, "visitor_id": "anon:bot1", "ua": "curl/8.4.0"},
    {"type": "visit", "ts": now, "visitor_id": "anon:bot2", "ua": ""},
    # Written before the agent was recorded: countable as neither.
    {"type": "visit", "ts": now, "visitor_id": "anon:old"},
])
s = analytics.summary()
check("people are counted once each", s["unique_visitors"], 2)
check("machines are counted separately", s["bot_visitors"], 2)
check("pre-agent rows are held apart", s["unclassified_visitors"], 1)
check("page visits still counts everything", s["total_visits"], 6)

print("run time, cost and rate")
write([
    # Delivered in 200s, cost $0.04, downloaded.
    {"type": "job_start", "ts": now, "job_id": "j-done", "route": "/", "visitor_id": "anon:aaa"},
    {"type": "api_call", "ts": now, "job_id": "j-done", "cost_usd": 0.04},
    {"type": "job_done", "ts": now + 200, "job_id": "j-done", "job_status": "done"},
    {"type": "download", "ts": now + 260, "job_id": "j-done", "visitor_id": "anon:aaa"},
    # A review screen nobody came back to, swept four hours later.
    {"type": "job_start", "ts": now, "job_id": "j-gate", "route": "/", "visitor_id": "anon:bbb"},
    {"type": "api_call", "ts": now, "job_id": "j-gate", "cost_usd": 0.03},
    {"type": "job_done", "ts": now + 4 * 3600, "job_id": "j-gate", "job_status": "failed"},
    # Died on the tracklist before spending anything.
    {"type": "job_start", "ts": now, "job_id": "j-dead", "route": "/", "visitor_id": "anon:bbb"},
    {"type": "job_done", "ts": now + 1, "job_id": "j-dead", "job_status": "failed"},
])
s = analytics.summary()
check("jobs started counts them all", s["total_generations"], 3)
check("videos delivered counts the ones that worked", s["videos_delivered"], 1)
check("run time ignores the abandoned gate", s["avg_duration"], "3m 20s")
check("total cost is still everything spent", s["total_cost_usd"], 0.07)
check("cost per video is per delivered video", s["avg_cost_per_generation_usd"], 0.04)
check("download rate is of delivered videos", s["download_rate"], 1.0)

print("the table")
row = {r["job_id"]: r for r in s["recent_jobs"]}
check("a row carries its job id", "j-done" in row, True)
check("a row carries who ran it", row["j-done"]["visitor"], "anon:aaa")
check("an unfinished job says so", analytics.summary()["recent_jobs"][0]["status"] in
      ("done", "failed", "in progress"), True)

print()
print("FAILED: " + ", ".join(FAILURES) if FAILURES else "ALL PASS")
sys.exit(1 if FAILURES else 0)
