"""Guards the operator's way in.

Plain script, no pytest -- run it directly:

    .venv/bin/python tests/test_admin_door.py

What it protects:

  1. /admin does not exist for anyone who isn't the operator. It answers 404,
     not 403 -- a stranger should not learn the route is there.
  2. The Google door only ever grants operator to an allowlisted address. A
     verified Google account that isn't on the list is answered like a
     stranger, not told it picked the wrong account.
  3. An operator gets Analytics, and the header carries the way back out. The
     bug this file exists for: with ADMIN_TOKEN unset in production, /o/<...>
     404'd, so nothing on the whole site led to operator mode or admitted it
     existed.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
os.environ.setdefault("FLASK_SECRET_KEY", "test-only-not-a-real-key")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="admin-door-test-")
# The door needs an allowlist and Google credentials to be open at all; both
# are what production has (INITIAL_ADMIN_EMAIL was already set there, read by
# nothing, before this route existed).
os.environ["ADMIN_EMAIL"] = "operator@example.com"
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-client-secret"
os.environ.pop("ADMIN_TOKEN", None)

import accounts          # noqa: E402
import app as A          # noqa: E402

FAILURES = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILURES.append(label)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + ("" if ok else f"  got={got!r} want={want!r}"))


def client():
    A.app.config["TESTING"] = True
    return A.app.test_client()


def as_operator(c):
    with c.session_transaction() as s:
        s["operator"] = True


print("allowlist")
check("ADMIN_EMAIL is read", "operator@example.com" in A.ADMIN_EMAILS, True)
check("door is open", A.admin_sign_in_available(), True)
# The whole point of the separation: accounts are gated on PAYWALL, operator is not.
check("account sign-in still off with paywall off", A.sign_in_available(), False)

print("a stranger")
c = client()
# Sent to Google, because the route cannot know who this is until Google says so.
# That is the one thing the Google door concedes over the token door: it admits an
# admin login exists. What it never concedes is access -- see "the callback" below,
# where a verified non-allowlisted account is answered 404 and granted nothing.
check("/admin starts the Google flow", c.get("/admin").status_code, 302)
check("/analytics is 404", c.get("/analytics").status_code, 404)
# Unset token must never be a skeleton key.
check("/o/<empty token> is 404", c.get("/o/x").status_code, 404)

# With no allowlist there is no door at all, and it goes back to being invisible.
_saved, A.ADMIN_EMAILS = A.ADMIN_EMAILS, set()
try:
    check("/admin is 404 with no allowlist", client().get("/admin").status_code, 404)
finally:
    A.ADMIN_EMAILS = _saved

print("the operator")
c = client()
as_operator(c)
r = c.get("/admin")
check("/admin redirects", r.status_code, 302)
check("...to analytics", r.headers["Location"].endswith("/analytics"), True)
check("/analytics opens", c.get("/analytics").status_code, 200)
page = c.get("/").get_data(as_text=True)
check("header offers Analytics", "/analytics" in page, True)
check("header offers the way out", "Exit operator mode" in page, True)
check("sign out revokes", c.post("/o/revoke").status_code, 302)
check("...and analytics closes again", c.get("/analytics").status_code, 404)

print("the callback")
# Stand in for Google returning a verified identity, which is the only part of
# the flow that decides anything.
class _FakeGoogle:
    def __init__(self, email, verified=True):
        self._email = email
        self._verified = verified

    def authorize_access_token(self):
        return {"userinfo": {"email": self._email, "email_verified": self._verified}}


def arrive_as(email, verified=True, admin_login=True):
    c = client()
    with c.session_transaction() as s:
        if admin_login:
            s["admin_login"] = True
            s["post_login_redirect"] = "/analytics"
    real = A.oauth.google
    A.oauth.google = _FakeGoogle(email, verified)
    try:
        return c, c.get("/auth/google/callback")
    finally:
        A.oauth.google = real


c, r = arrive_as("operator@example.com")
check("allowlisted email lands on analytics", r.headers.get("Location", "").endswith("/analytics"), True)
check("...and is the operator", c.get("/analytics").status_code, 200)

c, r = arrive_as("OPERATOR@Example.COM")
check("allowlist ignores case", c.get("/analytics").status_code, 200)

c, r = arrive_as("someone-else@example.com")
check("other Google accounts get 404", r.status_code, 404)
check("...and are granted nothing", c.get("/analytics").status_code, 404)

c, r = arrive_as("operator@example.com", verified=False)
check("unverified email grants nothing", c.get("/analytics").status_code, 404)

# Without the session marker this is the ordinary account flow, which is off.
c, r = arrive_as("operator@example.com", admin_login=False)
check("callback outside the admin flow grants nothing", c.get("/analytics").status_code, 404)

print()
print("FAILED: " + ", ".join(FAILURES) if FAILURES else "ALL PASS")
sys.exit(1 if FAILURES else 0)
