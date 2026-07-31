"""Signed-in accounts and the credit ledger.

One credit buys one clean (un-watermarked) export. Generating stays free and
watermarked, so a wasted render -- wrong tracklist, wrong theme -- never costs
anybody a credit. That is deliberate: the render costs us well under a cent, and
a burnt credit on a mistake is the complaint that would dominate support.

Storage is an append-only JSONL ledger next to analytics.jsonl, for the same
reason: this is a single-process app with no database, and a ledger is the one
thing you most want to be able to read back and audit by hand. Never rewrite or
compact this file in place -- balances are derived by replaying it.

Two event types:

    {"type": "grant", "account", "credits", "expires_at", "source", "ts"}
    {"type": "spend", "account", "job_id", "ts"}

`expires_at` is an epoch seconds, or null for credits that never expire. That
single field is the whole difference between the two ways credits arrive:

  * a subscription grants credits that expire when the month is up, so an
    unused month doesn't bank indefinitely;
  * a one-off pack (or a coupon) grants credits that never expire, because the
    customer bought those outright.

Payment providers are deliberately absent here. Paddle, when it lands, is just
another caller of grant() from its webhook -- the ledger neither knows nor cares
where a grant came from beyond the free-text `source`.
"""
import json
import os
import threading
import time

_DATA_DIR = os.environ.get("DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
LEDGER_PATH = os.path.join(_DATA_DIR, "credits.jsonl")
_lock = threading.RLock()


def normalize(account: str | None) -> str:
    return (account or "").strip().lower()


# --------------------------------------------------------------------------
# ledger primitives
# --------------------------------------------------------------------------

def _append(event: dict) -> None:
    event["ts"] = event.get("ts") or time.time()
    with _lock:
        with open(LEDGER_PATH, "a") as f:
            f.write(json.dumps(event) + "\n")


def _read_all() -> list[dict]:
    if not os.path.exists(LEDGER_PATH):
        return []
    events = []
    with open(LEDGER_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                # A torn final line (killed mid-write) must not take out every
                # balance in the file.
                continue
    return events


def _events_for(account: str) -> list[dict]:
    account = normalize(account)
    events = [e for e in _read_all() if normalize(e.get("account")) == account]
    events.sort(key=lambda e: e.get("ts") or 0)
    return events


def _replay(account: str) -> tuple[list[dict], set[str]]:
    """Replays the ledger into (open grants, job_ids already paid for).

    Spends are applied against the grants that were valid *at the time of the
    spend*, soonest-expiring first, so expiring credits are consumed before
    permanent ones. Computing a balance as `sum(unexpired grants) - count(spends)`
    would be wrong: it double-penalises anyone who spent an expiring credit and
    then let a later one lapse.
    """
    grants: list[dict] = []
    paid_jobs: set[str] = set()

    for e in _events_for(account):
        kind = e.get("type")
        if kind == "grant":
            grants.append({
                "left": int(e.get("credits") or 0),
                "expires_at": e.get("expires_at"),
                "source": e.get("source") or "",
                "ts": e.get("ts") or 0,
            })
        elif kind == "spend":
            job_id = e.get("job_id")
            if job_id:
                paid_jobs.add(job_id)
            at = e.get("ts") or 0
            live = [g for g in grants
                    if g["left"] > 0 and (g["expires_at"] is None or g["expires_at"] > at)]
            # None sorts last: burn dated credits before permanent ones.
            live.sort(key=lambda g: (g["expires_at"] is None, g["expires_at"] or 0))
            if live:
                live[0]["left"] -= 1

    return grants, paid_jobs


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def balance(account: str) -> int:
    """Credits available to spend right now."""
    if not normalize(account):
        return 0
    now = time.time()
    grants, _ = _replay(account)
    return sum(g["left"] for g in grants
               if g["left"] > 0 and (g["expires_at"] is None or g["expires_at"] > now))


def grant(account: str, credits: int, expires_at: float | None = None, source: str = "") -> None:
    """Adds credits. expires_at=None means they never expire (a bought pack or
    a coupon); a subscription renewal passes the end of the period it covers."""
    account = normalize(account)
    if not account:
        raise ValueError("account is required")
    if credits <= 0:
        raise ValueError("credits must be positive")
    _append({"type": "grant", "account": account, "credits": int(credits),
             "expires_at": expires_at, "source": source})


def has_paid_for(account: str, job_id: str) -> bool:
    """Whether this account already spent a credit on this job. Re-downloading
    something already paid for must never charge twice."""
    if not normalize(account) or not job_id:
        return False
    _, paid_jobs = _replay(account)
    return job_id in paid_jobs


def spend(account: str, job_id: str) -> bool:
    """Spends one credit on job_id. Returns True if the account may now have the
    clean export -- including when it had already paid for this job, in which
    case nothing is charged.

    The whole check-then-append runs under the lock so two concurrent download
    clicks can't both see the last credit and both spend it.
    """
    account = normalize(account)
    if not account or not job_id:
        return False
    with _lock:
        grants, paid_jobs = _replay(account)
        if job_id in paid_jobs:
            return True
        now = time.time()
        live = [g for g in grants
                if g["left"] > 0 and (g["expires_at"] is None or g["expires_at"] > now)]
        if not live:
            return False
        _append({"type": "spend", "account": account, "job_id": job_id})
        return True


def summary(account: str) -> dict:
    """Balance plus the soonest expiry, for showing "3 credits, 2 expire Aug 30"."""
    now = time.time()
    grants, paid_jobs = _replay(account)
    live = [g for g in grants
            if g["left"] > 0 and (g["expires_at"] is None or g["expires_at"] > now)]
    dated = [g for g in live if g["expires_at"] is not None]
    return {
        "balance": sum(g["left"] for g in live),
        "expiring": sum(g["left"] for g in dated),
        "next_expiry": min((g["expires_at"] for g in dated), default=None),
        "videos_paid_for": len(paid_jobs),
    }


# --------------------------------------------------------------------------
# coupons
# --------------------------------------------------------------------------

def _coupons() -> dict[str, int]:
    """Parsed from the COUPONS env var, "CODE:credits,OTHER:credits".

    Env rather than a file in the repo so a live code is never committed, and
    read per call so adding one needs a restart, not a deploy. Coupon credits
    never expire -- a starter pack you have to burn within the month is a
    deadline, not a gift.
    """
    raw = os.environ.get("COUPONS") or ""
    out: dict[str, int] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        code, _, amount = part.partition(":")
        try:
            n = int(amount)
        except ValueError:
            continue
        if code.strip() and n > 0:
            out[code.strip().upper()] = n
    return out


def redeem(account: str, code: str) -> tuple[bool, str]:
    """Returns (ok, message). One redemption of a given code per account."""
    account = normalize(account)
    code = (code or "").strip().upper()
    if not account:
        return False, "Sign in first."
    if not code:
        return False, "Enter a code."

    amount = _coupons().get(code)
    if not amount:
        return False, "That code isn't valid."

    source = f"coupon:{code}"
    with _lock:
        if any(e.get("type") == "grant" and e.get("source") == source
               for e in _events_for(account)):
            return False, "You've already used that code."
        grant(account, amount, expires_at=None, source=source)
    return True, f"Added {amount} credits."
