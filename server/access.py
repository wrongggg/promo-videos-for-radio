"""Access control after sign-in was removed.

Google sign-in existed for exactly one reason: each user had to supply their
own YouTube cookies, and those could never be shared between people. The
catalog preview chain in `providers` needs no credentials from anyone, so that
reason is gone and with it the whole login flow.

What sign-in was also quietly providing, re-homed here:

  * Operator privileges (AI curation, web search, the Opus model, custom and
    saved themes, unlimited manual tracks, /analytics, and the operator-only
    YouTube source). Now granted by visiting /o/<ADMIN_TOKEN> once, which sets
    a signed, long-lived session flag.
  * A stable id for analytics. Now a random per-browser id in the session
    rather than an email address -- the numbers still work, nobody is named.
  * A cap on cost. With no login there is no gate at all in front of a job
    that spends Anthropic tokens and render CPU, so an per-IP daily quota
    takes over that job until real billing exists.
"""
import os
import secrets
import threading
import time
from collections import defaultdict

from flask import request, session

# Read lazily, never captured at import time. app.py imports this module before
# it calls load_dotenv(), so a module-level os.environ.get() captured an empty
# string and every token in .env was silently ignored -- which meant
# /o/<ADMIN_TOKEN> 404'd forever for anyone who configured it the way
# .env.example says to. Reading per call also means rotating a token only needs
# a restart, not a code change.
def _env(name: str) -> str:
    return os.environ.get(name) or ""


# Jobs per IP per rolling 24h for anonymous visitors. Generous enough that a
# radio producer making promos all afternoon never notices, low enough that a
# scraper can't run up an API bill. The operator is exempt.
DAILY_JOB_LIMIT = int(os.environ.get("DAILY_JOB_LIMIT", "25"))
_WINDOW = 24 * 3600

_lock = threading.Lock()
_job_times: dict[str, list[float]] = defaultdict(list)


# --------------------------------------------------------------------------
# operator
# --------------------------------------------------------------------------

def grant_operator() -> None:
    session["operator"] = True
    session.permanent = True


def revoke_operator() -> None:
    session.pop("operator", None)


def is_operator() -> bool:
    return bool(session.get("operator"))


def grant_station() -> None:
    session["station"] = True
    session.permanent = True


def revoke_station() -> None:
    session.pop("station", None)


def is_station() -> bool:
    """Operator counts as station -- they are the station."""
    return bool(session.get("station")) or is_operator()


def token_matches(token: str) -> bool:
    """Constant-time compare, and never true when no token is configured --
    otherwise an unset ADMIN_TOKEN would hand operator rights to /o/."""
    expected = _env("ADMIN_TOKEN")
    if not expected or not token:
        return False
    return secrets.compare_digest(token, expected)


def station_token_matches(token: str) -> bool:
    """A weaker, shareable token for colleagues at the same station. It grants
    exactly one thing -- the station's own logo as the default -- and none of
    the operator perks. It exists because the station logo must never end up on
    a stranger's promo, but with no login there is no other way to tell a
    colleague from a passer-by."""
    expected = _env("STATION_TOKEN")
    if not expected or not token:
        return False
    return secrets.compare_digest(token, expected)


# --------------------------------------------------------------------------
# anonymous visitor id (analytics only)
# --------------------------------------------------------------------------

def visitor_id() -> str:
    vid = session.get("vid")
    if not vid:
        vid = secrets.token_hex(8)
        session["vid"] = vid
        session.permanent = True
    return ("operator:" if is_operator() else "anon:") + vid


# --------------------------------------------------------------------------
# rate limiting
# --------------------------------------------------------------------------

def _client_ip() -> str:
    # ProxyFix has already normalized X-Forwarded-For into remote_addr.
    return request.remote_addr or "unknown"


def check_job_quota() -> tuple[bool, int]:
    """Returns (allowed, remaining). Records the job when allowed."""
    if is_operator():
        return True, DAILY_JOB_LIMIT

    ip = _client_ip()
    now = time.time()
    with _lock:
        recent = [t for t in _job_times[ip] if now - t < _WINDOW]
        if len(recent) >= DAILY_JOB_LIMIT:
            _job_times[ip] = recent
            return False, 0
        recent.append(now)
        _job_times[ip] = recent
        return True, DAILY_JOB_LIMIT - len(recent)
