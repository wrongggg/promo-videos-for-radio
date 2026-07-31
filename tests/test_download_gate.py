"""Guards the watermark / clean-export split.

Plain script, no pytest -- the project has no test dependency and this needs
none. Run it directly:

    .venv/bin/python tests/test_download_gate.py

What it protects, in order of how bad the regression would be:

  1. /preview never serves the clean master. It's the one URL trivially
     readable from the page source; leak it and the whole gate is theatre.
  2. Unentitled downloads get the watermarked cut, entitled ones get the master.
  3. Job ownership survives a change in entitlement -- the paid flow is
     "generate, then pay, then download", so an id that changes on upgrade
     locks people out of the video they just bought.
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
os.environ.setdefault("FLASK_SECRET_KEY", "test-only-not-a-real-key")
# Point the app at a scratch DATA_DIR before importing it, so the test writes
# its renders and its credit ledger somewhere disposable instead of into the
# working copy. app.py resolves DATA_DIR at import time.
_DATA = tempfile.mkdtemp(prefix="rotation-test-")
os.environ["DATA_DIR"] = _DATA

import accounts         # noqa: E402
import app as A          # noqa: E402
import compose           # noqa: E402

FAILURES = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILURES.append(label)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + ("" if ok else f": got {got!r}, expected {want!r}"))


def make_clip(path):
    """A real 1s 1080x1920 clip with audio -- small, but exercises the actual
    overlay filter and the audio stream-copy branch."""
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=c=0x203040:s=1080x1920:d=1:r=30",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo:d=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        path,
    ], check=True, capture_output=True)


def main():
    A.app.config["TESTING"] = True
    jid = "test-job"

    with tempfile.TemporaryDirectory() as tmp:
        clean = os.path.join(tmp, "output.mp4")
        marked = os.path.join(tmp, "output-watermarked.mp4")
        make_clip(clean)

        print("watermarking:")
        compose.watermark_video(clean, marked)
        check("produces a watermarked file", os.path.exists(marked), True)
        check("it differs from the master", os.path.getsize(marked) != os.path.getsize(clean), True)

        clean_sz, marked_sz = os.path.getsize(clean), os.path.getsize(marked)

        def which(data):
            if len(data) == clean_sz:
                return "CLEAN"
            if len(data) == marked_sz:
                return "WATERMARKED"
            return f"UNKNOWN({len(data)})"

        def install(owner):
            A.JOBS[jid] = {
                "status": "done", "owner": owner, "output_path": clean,
                "watermarked_path": marked, "log": [], "error": None, "needs_upload": [],
            }

        print("\nunentitled visitor:")
        with A.app.test_client() as c:
            with c.session_transaction() as s:
                s["vid"] = "v1"
            install("v1")
            check("preview is watermarked", which(c.get(f"/preview/{jid}").data), "WATERMARKED")
            r = c.get(f"/download/{jid}")
            check("download is watermarked", which(r.data), "WATERMARKED")
            check("filename says so", "-watermarked.mp4" in r.headers.get("Content-Disposition", ""), True)

        print("\nentitled (operator):")
        with A.app.test_client() as c:
            with c.session_transaction() as s:
                s["vid"] = "v2"
                s["operator"] = True
            install("v2")
            check("preview STILL watermarked", which(c.get(f"/preview/{jid}").data), "WATERMARKED")
            r = c.get(f"/download/{jid}")
            check("download is the clean master", which(r.data), "CLEAN")
            check("filename is clean", "-watermarked" not in r.headers.get("Content-Disposition", ""), True)

        print("\nsigned in, no credits:")
        with A.app.test_client() as c:
            with c.session_transaction() as s:
                s["vid"] = "vc1"
                s["account"] = "broke@example.com"
            install("vc1")
            r = c.get(f"/download/{jid}")
            check("download is watermarked", which(r.data), "WATERMARKED")
            check("nothing charged", accounts.balance("broke@example.com"), 0)

        print("\nsigned in, with credits:")
        accounts.grant("payer@example.com", 2, source="test")
        with A.app.test_client() as c:
            with c.session_transaction() as s:
                s["vid"] = "vc2"
                s["account"] = "payer@example.com"
            install("vc2")
            r = c.get(f"/download/{jid}")
            check("download is the clean master", which(r.data), "CLEAN")
            check("one credit spent", accounts.balance("payer@example.com"), 1)

            print("\n  re-downloading the same job:")
            r = c.get(f"/download/{jid}")
            check("still clean", which(r.data), "CLEAN")
            check("but not charged twice", accounts.balance("payer@example.com"), 1)

            print("\n  a second, different job:")
            A.JOBS["other-job"] = dict(A.JOBS[jid])
            r = c.get("/download/other-job")
            check("clean", which(r.data), "CLEAN")
            check("charged again", accounts.balance("payer@example.com"), 0)

            print("\n  now out of credits:")
            A.JOBS["third-job"] = dict(A.JOBS[jid])
            r = c.get("/download/third-job")
            check("falls back to watermarked", which(r.data), "WATERMARKED")
            check("balance still 0", accounts.balance("payer@example.com"), 0)

        print("\npreview never spends a credit:")
        accounts.grant("watcher@example.com", 1, source="test")
        with A.app.test_client() as c:
            with c.session_transaction() as s:
                s["vid"] = "vc3"
                s["account"] = "watcher@example.com"
            install("vc3")
            check("preview watermarked", which(c.get(f"/preview/{jid}").data), "WATERMARKED")
            check("balance untouched", accounts.balance("watcher@example.com"), 1)

        print("\nanother visitor's job:")
        with A.app.test_client() as c:
            with c.session_transaction() as s:
                s["vid"] = "v3"
            install("someone-else")
            check("preview refused", c.get(f"/preview/{jid}").status_code, 403)
            check("download refused", c.get(f"/download/{jid}").status_code, 403)

        print("\nupgrade path (generate anonymously, then become entitled):")
        with A.app.test_client() as c:
            with c.session_transaction() as s:
                s["vid"] = "v4"
            install("v4")
            check("owns it before upgrade", c.get(f"/download/{jid}").status_code, 200)
            with c.session_transaction() as s:
                s["operator"] = True          # stands in for "paid"
            r = c.get(f"/download/{jid}")
            check("still owns it after upgrade", r.status_code, 200)
            check("and now gets the master", which(r.data), "CLEAN")

        print("\nsigning out keeps your jobs reachable:")
        accounts.grant("inout@example.com", 1, source="test")
        with A.app.test_client() as c:
            with c.session_transaction() as s:
                s["vid"] = "vc4"
            install("vc4")
            check("owns it anonymously", c.get(f"/download/{jid}").status_code, 200)
            with c.session_transaction() as s:      # stands in for the OAuth callback
                s["account"] = "inout@example.com"
            check("owns it signed in", c.get(f"/download/{jid}").status_code, 200)
            c.post("/logout")
            r = c.get(f"/download/{jid}")
            check("still owns it after signing out", r.status_code, 200)
            check("and is back to watermarked", which(r.data), "WATERMARKED")

        print("\nlegacy owner strings (jobs created before ownership was un-prefixed):")
        for stored in ("anon:v6", "operator:v6", "v6"):
            with A.app.test_client() as c:
                with c.session_transaction() as s:
                    s["vid"] = "v6"
                install(stored)
                check(f"owner={stored!r} still matches", c.get(f"/download/{jid}").status_code, 200)
        with A.app.test_client() as c:
            with c.session_transaction() as s:
                s["vid"] = "v7"
            install("anon:someone-else")
            check("but a different id still refused", c.get(f"/download/{jid}").status_code, 403)

        print("\nlegacy job with no watermarked copy:")
        with A.app.test_client() as c:
            with c.session_transaction() as s:
                s["vid"] = "v5"
            A.JOBS[jid] = {"status": "done", "owner": "v5", "output_path": clean,
                           "log": [], "error": None, "needs_upload": []}
            check("falls back to the master", which(c.get(f"/preview/{jid}").data), "CLEAN")

    print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURE(S): {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
