"""Paddle webhook tests. Plain script, no pytest:

    .venv/bin/python tests/test_paddle.py

The webhook is the only path that turns money into credits, so the cases that
matter most are the adversarial and the repeated ones: a forged signature must
never grant, and a genuine event delivered five times must grant exactly once.
"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
os.environ.setdefault("FLASK_SECRET_KEY", "test-only-not-a-real-key")
_DATA = tempfile.mkdtemp(prefix="rotation-paddle-")
os.environ["DATA_DIR"] = _DATA
os.environ["PADDLE_WEBHOOK_SECRET"] = "pdl_ntfset_test_secret"
os.environ["PADDLE_CLIENT_TOKEN"] = "test_client_token"
os.environ["PADDLE_PRICES"] = "pri_pack10:10,pri_sub10:10,pri_station:45"

import accounts        # noqa: E402
import app as A        # noqa: E402
import paddle          # noqa: E402

SECRET = os.environ["PADDLE_WEBHOOK_SECRET"]
FAILURES = []
HOUR = 3600


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILURES.append(label)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + ("" if ok else f": got {got!r}, expected {want!r}"))


def sign(body: bytes, ts: int | None = None, secret: str = SECRET) -> str:
    ts = ts or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={mac}"


def transaction(account, price_id, quantity=1, txn="txn_1", period_end=None):
    item = {"price": {"id": price_id}, "quantity": quantity}
    if period_end:
        item["billing_period"] = {"ends_at": period_end}
    return {
        "event_id": "evt_1",
        "event_type": "transaction.completed",
        "data": {"id": txn, "custom_data": {"account": account}, "items": [item]},
    }


def post(client, event, header=None, raw=None):
    body = raw if raw is not None else json.dumps(event).encode()
    return client.post("/paddle/webhook", data=body,
                       headers={"Paddle-Signature": header if header is not None else sign(body),
                                "Content-Type": "application/json"})


def main():
    A.app.config["TESTING"] = True
    c = A.app.test_client()

    print("signature verification:")
    body = b'{"hello":"world"}'
    check("a good signature verifies", paddle.verify_signature(body, sign(body), SECRET), True)
    check("wrong secret rejected", paddle.verify_signature(body, sign(body, secret="nope"), SECRET), False)
    check("tampered body rejected", paddle.verify_signature(b'{"hello":"evil"}', sign(body), SECRET), False)
    check("missing header rejected", paddle.verify_signature(body, "", SECRET), False)
    check("garbage header rejected", paddle.verify_signature(body, "not-a-header", SECRET), False)
    check("no h1 rejected", paddle.verify_signature(body, "ts=123", SECRET), False)
    old = sign(body, ts=int(time.time()) - 3600)
    check("stale timestamp rejected (replay)", paddle.verify_signature(body, old, SECRET), False)

    print("\nthe route refuses anything it cannot verify:")
    r = post(c, transaction("a@b.com", "pri_pack10"), header="ts=1;h1=deadbeef")
    check("forged signature -> 403", r.status_code, 403)
    check("nothing granted", accounts.balance("a@b.com"), 0)
    r = post(c, transaction("a@b.com", "pri_pack10"), header="")
    check("no signature -> 403", r.status_code, 403)

    print("\na one-off pack:")
    ev = transaction("buyer@example.com", "pri_pack10", txn="txn_pack")
    r = post(c, ev)
    check("accepted", r.status_code, 200)
    check("10 credits granted", accounts.balance("buyer@example.com"), 10)
    check("they never expire", accounts.summary("buyer@example.com")["expiring"], 0)

    print("\nthe same event delivered again (Paddle retries):")
    for _ in range(4):
        post(c, ev)
    check("still 10 credits", accounts.balance("buyer@example.com"), 10)

    print("\na subscription charge:")
    ends = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 30 * 24 * HOUR))
    ev = transaction("sub@example.com", "pri_sub10", txn="txn_sub", period_end=ends)
    post(c, ev)
    s = accounts.summary("sub@example.com")
    check("10 credits granted", s["balance"], 10)
    check("and they expire", s["expiring"], 10)
    check("at the period end", abs(s["next_expiry"] - (time.time() + 30 * 24 * HOUR)) < 120, True)

    print("\nrenewal next month is a separate transaction:")
    ev2 = transaction("sub@example.com", "pri_sub10", txn="txn_sub_2", period_end=ends)
    post(c, ev2)
    check("tops up to 20", accounts.balance("sub@example.com"), 20)

    print("\nquantities and multiple items:")
    ev = transaction("bulk@example.com", "pri_pack10", quantity=3, txn="txn_bulk")
    post(c, ev)
    check("quantity 3 grants 30", accounts.balance("bulk@example.com"), 30)

    two = {
        "event_id": "evt_two", "event_type": "transaction.completed",
        "data": {"id": "txn_two", "custom_data": {"account": "two@example.com"}, "items": [
            {"price": {"id": "pri_pack10"}, "quantity": 1},
            {"price": {"id": "pri_station"}, "quantity": 1},
        ]},
    }
    post(c, two)
    check("two different items both grant", accounts.balance("two@example.com"), 55)

    print("\nthings that must not grant:")
    ev = transaction("unknown@example.com", "pri_not_configured", txn="txn_unknown")
    r = post(c, ev)
    check("unknown price id -> 200 but nothing granted", r.status_code, 200)
    check("balance 0", accounts.balance("unknown@example.com"), 0)
    check("and it is reported", r.get_json()["unknown_prices"], ["pri_not_configured"])

    noacct = {"event_id": "e", "event_type": "transaction.completed",
              "data": {"id": "txn_na", "items": [{"price": {"id": "pri_pack10"}, "quantity": 1}]}}
    r = post(c, noacct)
    check("no account on transaction -> reported", "error" in r.get_json(), True)

    other = {"event_id": "e2", "event_type": "subscription.canceled",
             "data": {"id": "sub_1", "custom_data": {"account": "cancel@example.com"}}}
    r = post(c, other)
    check("subscription.canceled ignored", r.get_json().get("ignored"), "subscription.canceled")
    check("cancelling grants nothing", accounts.balance("cancel@example.com"), 0)

    print("\ncancelling does not claw back credits already paid for:")
    ev = transaction("keeper@example.com", "pri_pack10", txn="txn_keep")
    post(c, ev)
    post(c, {"event_id": "e3", "event_type": "subscription.canceled",
             "data": {"id": "sub_2", "custom_data": {"account": "keeper@example.com"}}})
    check("credits survive", accounts.balance("keeper@example.com"), 10)

    print("\nthe checkout config endpoint:")
    cfg = c.get("/credits/checkout").get_json()
    check("reports configured", cfg["configured"], True)
    check("exposes the client token", cfg["client_token"], "test_client_token")
    check("never exposes the webhook secret", SECRET not in json.dumps(cfg), True)

    print("\nwith no webhook secret set:")
    saved = os.environ.pop("PADDLE_WEBHOOK_SECRET")
    try:
        r = post(c, transaction("x@y.com", "pri_pack10", txn="txn_off"))
        check("route refuses rather than accepting unverified", r.status_code, 503)
        check("nothing granted", accounts.balance("x@y.com"), 0)
    finally:
        os.environ["PADDLE_WEBHOOK_SECRET"] = saved

    print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURE(S): {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
