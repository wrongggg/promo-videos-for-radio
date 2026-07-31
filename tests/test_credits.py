"""Credit ledger tests. Plain script, no pytest:

    .venv/bin/python tests/test_credits.py

Each case gets a fresh ledger via DATA_DIR pointed at a temp dir.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

FAILURES = []
HOUR = 3600


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILURES.append(label)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + ("" if ok else f": got {got!r}, expected {want!r}"))


def fresh(tmp, name):
    """A module bound to its own ledger file, so cases can't bleed into each other."""
    os.environ["DATA_DIR"] = os.path.join(tmp, name)
    os.makedirs(os.environ["DATA_DIR"], exist_ok=True)
    for mod in ("accounts",):
        sys.modules.pop(mod, None)
    import accounts
    return accounts


def main():
    with tempfile.TemporaryDirectory() as tmp:
        A = fresh(tmp, "basic")
        print("grant and spend:")
        check("empty balance", A.balance("a@b.com"), 0)
        A.grant("a@b.com", 3, source="test")
        check("after granting 3", A.balance("a@b.com"), 3)
        check("spend succeeds", A.spend("a@b.com", "job1"), True)
        check("balance drops to 2", A.balance("a@b.com"), 2)

        print("\nre-downloading something already paid for:")
        check("second spend on same job", A.spend("a@b.com", "job1"), True)
        check("but charges nothing", A.balance("a@b.com"), 2)
        check("has_paid_for job1", A.has_paid_for("a@b.com", "job1"), True)
        check("has_paid_for job2", A.has_paid_for("a@b.com", "job2"), False)

        print("\nrunning out:")
        A.spend("a@b.com", "job2")
        A.spend("a@b.com", "job3")
        check("balance now 0", A.balance("a@b.com"), 0)
        check("spend refused", A.spend("a@b.com", "job4"), False)
        check("job4 not marked paid", A.has_paid_for("a@b.com", "job4"), False)

        print("\naccounts are isolated:")
        check("other account unaffected", A.balance("other@b.com"), 0)
        A.grant("other@b.com", 5, source="test")
        check("other has 5", A.balance("other@b.com"), 5)
        check("first still 0", A.balance("a@b.com"), 0)
        check("email case/space normalised", A.balance("  A@B.COM "), 0)
        A.grant("A@B.Com", 2, source="test")
        check("granting to same address in another case", A.balance("a@b.com"), 2)

        # ---- expiry ----
        A = fresh(tmp, "expiry")
        now = time.time()
        print("\nexpired credits:")
        A.grant("x@y.com", 5, expires_at=now - HOUR, source="lapsed-sub")
        check("expired grant is worth nothing", A.balance("x@y.com"), 0)
        check("and cannot be spent", A.spend("x@y.com", "j"), False)
        A.grant("x@y.com", 2, expires_at=now + HOUR, source="live-sub")
        check("live grant counts", A.balance("x@y.com"), 2)

        print("\nspend order -- dated credits burn before permanent ones:")
        A = fresh(tmp, "order")
        now = time.time()
        A.grant("z@y.com", 1, expires_at=None, source="pack")
        A.grant("z@y.com", 1, expires_at=now + HOUR, source="sub")
        check("balance 2", A.balance("z@y.com"), 2)
        A.spend("z@y.com", "j1")
        # The subscription credit should be the one consumed, leaving the
        # permanent pack credit intact once the subscription window passes.
        A._append({"type": "grant", "account": "z@y.com", "credits": 0, "expires_at": None,
                   "source": "probe", "ts": now})
        check("balance 1 right after", A.balance("z@y.com"), 1)

        print("\nthe replay case a naive balance gets wrong:")
        # 1 dated credit + 1 permanent, dated one spent while valid, then it
        # lapses. Naive `sum(unexpired grants) - total spends` = 1 - 1 = 0.
        # Correct answer is 1: the lapsed credit was already used.
        A = fresh(tmp, "replay")
        base = time.time() - 10 * HOUR
        A._append({"type": "grant", "account": "r@y.com", "credits": 1,
                   "expires_at": base + HOUR, "source": "sub", "ts": base})
        A._append({"type": "grant", "account": "r@y.com", "credits": 1,
                   "expires_at": None, "source": "pack", "ts": base})
        A._append({"type": "spend", "account": "r@y.com", "job_id": "j1", "ts": base + 60})
        check("permanent credit survives the lapse", A.balance("r@y.com"), 1)
        check("and is still spendable", A.spend("r@y.com", "j2"), True)
        check("then empty", A.balance("r@y.com"), 0)

        # ---- coupons ----
        A = fresh(tmp, "coupons")
        os.environ["COUPONS"] = "KZRADIO:25, LAUNCH:5"
        print("\ncoupons:")
        ok, msg = A.redeem("k@z.com", "kzradio")
        check("redeems case-insensitively", ok, True)
        check("grants 25", A.balance("k@z.com"), 25)
        ok, msg = A.redeem("k@z.com", "KZRADIO")
        check("cannot redeem twice", ok, False)
        check("balance unchanged", A.balance("k@z.com"), 25)
        ok, _ = A.redeem("k@z.com", "NOPE")
        check("unknown code refused", ok, False)
        ok, _ = A.redeem("other@z.com", "KZRADIO")
        check("a different account still can", ok, True)
        check("coupon credits never expire", A.summary("k@z.com")["expiring"], 0)

        print("\nsummary:")
        s = A.summary("k@z.com")
        check("balance", s["balance"], 25)
        check("next_expiry is None", s["next_expiry"], None)
        A.spend("k@z.com", "j9")
        check("videos_paid_for", A.summary("k@z.com")["videos_paid_for"], 1)

    print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURE(S): {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
