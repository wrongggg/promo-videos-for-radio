"""Paddle checkout and webhook handling.

Paddle is the merchant of record: it is the legal seller, it collects the money,
and it handles VAT/sales tax in every country we sell into. We never see a card
number. Stripe would be cheaper (2.9% + $0.30 against Paddle's 5% + $0.50) but
does not accept Israeli businesses at all, so this is not a trade we get to make.

This module is deliberately thin. Everything it does ends in one call to
accounts.grant_once() -- the credit ledger has no idea Paddle exists, and
swapping providers later means rewriting this file and nothing else.

Configuration (all from the environment, nothing committed):

    PADDLE_ENV              "sandbox" (default) or "production"
    PADDLE_CLIENT_TOKEN     public, safe in the page -- used by Paddle.js
    PADDLE_WEBHOOK_SECRET   secret, per notification destination (pdl_ntfset_...)
    PADDLE_PRICES           "pri_abc:10,pri_def:45" -- price id to credits

Note PADDLE_PRICES maps to credits only, not to "subscription or pack". Whether
credits expire is read from the transaction itself (a subscription payment
carries a billing period, a one-off doesn't), so it cannot drift out of sync with
what Paddle actually charged for.
"""
import hashlib
import hmac
import os
import time
from datetime import datetime

import accounts

# Paddle's own recommendation for replay protection. Generous enough here that a
# slow render blocking the worker for a moment can't reject a genuine delivery.
DEFAULT_TOLERANCE_SECONDS = 60


def env() -> str:
    return (os.environ.get("PADDLE_ENV") or "sandbox").strip().lower()


def client_token() -> str:
    return os.environ.get("PADDLE_CLIENT_TOKEN") or ""


def webhook_secret() -> str:
    return os.environ.get("PADDLE_WEBHOOK_SECRET") or ""


def is_configured() -> bool:
    """Checkout is only offered when Paddle is actually set up. Without this the
    UI would show a Buy button that opens a broken overlay."""
    return bool(client_token() and price_map())


def price_map() -> dict[str, int]:
    """price id -> credits, parsed from PADDLE_PRICES."""
    out: dict[str, int] = {}
    for part in (os.environ.get("PADDLE_PRICES") or "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        price_id, _, amount = part.partition(":")
        try:
            n = int(amount)
        except ValueError:
            continue
        if price_id.strip() and n > 0:
            out[price_id.strip()] = n
    return out


# --------------------------------------------------------------------------
# signature verification
# --------------------------------------------------------------------------

def verify_signature(raw_body: bytes, signature_header: str, secret: str,
                     tolerance: int = DEFAULT_TOLERANCE_SECONDS,
                     now: float | None = None) -> bool:
    """Verifies a Paddle-Signature header of the form "ts=<unix>;h1=<hex>".

    The signed payload is "{ts}:{raw_body}" HMAC-SHA256'd with the endpoint
    secret. raw_body must be the bytes exactly as received -- parsing the JSON
    and re-serialising it changes the whitespace and the signature will not
    match, which is why the route reads request.get_data() and verifies before
    it ever looks at the contents.
    """
    if not signature_header or not secret:
        return False

    parts = {}
    for chunk in signature_header.split(";"):
        key, _, value = chunk.partition("=")
        if key.strip():
            parts[key.strip()] = value.strip()

    ts, received = parts.get("ts"), parts.get("h1")
    if not ts or not received:
        return False

    try:
        ts_int = int(ts)
    except ValueError:
        return False

    # Reject stale events so a captured delivery can't be replayed later. Only
    # guards the past: a small amount of clock skew the other way is harmless.
    if (now or time.time()) - ts_int > tolerance:
        return False

    expected = hmac.new(
        secret.encode(), f"{ts}:".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, received)


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------

def _parse_time(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _account_for(data: dict) -> str:
    """Who to credit.

    custom_data.account is set when we open the checkout, so it is the account
    that was signed in at the time and the one we trust. The customer's Paddle
    email is only a fallback: someone can pay with a different address than they
    signed in with, and crediting that would silently create an account they
    never asked for and cannot reach.
    """
    custom = data.get("custom_data") or {}
    account = custom.get("account") if isinstance(custom, dict) else None
    if not account:
        customer = data.get("customer") or {}
        account = customer.get("email") if isinstance(customer, dict) else None
    return accounts.normalize(account)


def handle_event(event: dict) -> dict:
    """Applies a verified webhook event. Returns a small dict for logging.

    Only transaction.completed grants anything. Every payment Paddle takes --
    a one-off pack or a subscription's first charge or its renewals -- produces
    exactly one completed transaction, so handling this single event type covers
    all of them with no risk of double-granting from also listening to
    subscription.created.

    Cancellations are deliberately not handled: credits already paid for stay
    valid until the period they were bought for ends, which is what the refund
    policy promises.
    """
    event_type = event.get("event_type") or ""
    if event_type != "transaction.completed":
        return {"ignored": event_type}

    data = event.get("data") or {}
    account = _account_for(data)
    if not account:
        return {"error": "no account on transaction", "transaction": data.get("id")}

    prices = price_map()
    # Paddle retries until it gets a 2xx, so the dedup key has to be stable
    # across deliveries of the same event. The transaction id is -- the event id
    # is not, since a retry is a fresh delivery of the same transaction.
    transaction_id = data.get("id") or event.get("event_id")

    granted, skipped = 0, []
    for index, item in enumerate(data.get("items") or []):
        price_id = ((item.get("price") or {}).get("id")) or item.get("price_id")
        per_unit = prices.get(price_id)
        if not per_unit:
            skipped.append(price_id)
            continue

        quantity = int(item.get("quantity") or 1)
        credits = per_unit * quantity

        # A billing period means this was a subscription charge, so the credits
        # last exactly as long as the period paid for and don't roll over. No
        # billing period means an outright purchase, which never expires.
        period = item.get("billing_period") or {}
        expires_at = _parse_time(period.get("ends_at"))

        # Indexed so a transaction containing two different priced items grants
        # both instead of the second being mistaken for a replay of the first.
        source = f"paddle:{transaction_id}:{index}"
        if accounts.grant_once(account, credits, expires_at=expires_at, source=source):
            granted += credits

    return {
        "account": account,
        "granted": granted,
        "transaction": transaction_id,
        "unknown_prices": skipped,
    }
