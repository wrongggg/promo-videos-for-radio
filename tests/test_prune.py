"""Render-pruning tests. Plain script, no pytest:

    .venv/bin/python tests/test_prune.py

Deleting the wrong directory is the failure mode that matters here, so most of
these check what SURVIVES rather than what goes.
"""
import os
import sys
import tempfile
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
os.environ.setdefault("FLASK_SECRET_KEY", "test-only-not-a-real-key")
_DATA = tempfile.mkdtemp(prefix="rotation-prune-")
os.environ["DATA_DIR"] = _DATA
os.environ["RENDER_RETENTION_DAYS"] = "7"

import app as A          # noqa: E402

FAILURES = []
DAY = 86400


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILURES.append(label)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + ("" if ok else f": got {got!r}, expected {want!r}"))


def make_dir(name, age_days):
    path = os.path.join(A.RENDERS_DIR, name)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "output.mp4"), "wb") as f:
        f.write(b"x" * 32)
    when = time.time() - age_days * DAY
    os.utime(path, (when, when))
    return path


def exists(name):
    return os.path.isdir(os.path.join(A.RENDERS_DIR, name))


def main():
    A.JOBS.clear()

    old_job = str(uuid.uuid4())
    fresh_job = str(uuid.uuid4())
    active_job = str(uuid.uuid4())
    done_old_job = str(uuid.uuid4())

    make_dir(old_job, 30)
    make_dir(fresh_job, 1)
    make_dir(active_job, 30)
    make_dir(done_old_job, 30)
    make_dir("themetest", 400)          # hand-made, not a uuid
    make_dir("keep-this-one", 400)

    # Still rendering -- must survive regardless of how old its mtime looks.
    A.JOBS[active_job] = {"status": "rendering", "log": []}
    # Finished but from this same process: JOBS never evicts, so this is the
    # case that would leak forever if the check were "is it in JOBS at all".
    A.JOBS[done_old_job] = {"status": "done", "log": []}

    print("pruning:")
    removed = A.prune_old_renders()
    check("removed 2 directories", removed, 2)

    print("\ngone:")
    check("old untracked job", exists(old_job), False)
    check("old job finished this process", exists(done_old_job), False)
    check("its JOBS entry dropped too", done_old_job in A.JOBS, False)

    print("\nsurvived:")
    check("recent job", exists(fresh_job), True)
    check("in-flight job despite old mtime", exists(active_job), True)
    check("its JOBS entry kept", active_job in A.JOBS, True)
    check("hand-made 'themetest'", exists("themetest"), True)
    check("hand-made 'keep-this-one'", exists("keep-this-one"), True)

    print("\nretention disabled (RENDER_RETENTION_DAYS=0):")
    ancient = str(uuid.uuid4())
    make_dir(ancient, 999)
    original = A.RENDER_RETENTION_DAYS
    A.RENDER_RETENTION_DAYS = 0
    try:
        check("prunes nothing", A.prune_old_renders(), 0)
        check("ancient job still there", exists(ancient), True)
    finally:
        A.RENDER_RETENTION_DAYS = original

    print("\nrunning twice is harmless:")
    check("second pass removes nothing new", A.prune_old_renders(), 1)
    check("third pass removes nothing", A.prune_old_renders(), 0)

    # A job parked at a gate is exempt from pruning by design, which is exactly why it
    # needs a deadline: without one it pins its directory on the billed volume for the
    # life of the process.
    print("\nabandoned gates expire and become prunable:")
    waiting = str(uuid.uuid4())
    make_dir(waiting, 30)
    A.JOBS[waiting] = {"status": "awaiting_uploads", "log": [],
                       "gate_deadline": time.time() + 3600}
    check("nothing expired while inside the deadline", A._expire_stale_gates(), 0)
    check("still awaiting", A.JOBS[waiting]["status"], "awaiting_uploads")
    check("old dir survives while job is parked", A.prune_old_renders(), 0)
    check("directory kept", exists(waiting), True)

    A.JOBS[waiting]["gate_deadline"] = time.time() - 1
    check("expired once past the deadline", A._expire_stale_gates(), 1)
    check("marked failed", A.JOBS[waiting]["status"], "failed")
    check("has an explanation", bool(A.JOBS[waiting].get("error")), True)
    check("expiring twice is harmless", A._expire_stale_gates(), 0)
    check("now prunable", A.prune_old_renders(), 1)
    check("directory gone", exists(waiting), False)

    # A gate with no deadline at all must not be swept -- an unrecognised state should
    # stall visibly, not delete someone's job out from under them.
    print("\na gate without a deadline is left alone:")
    no_deadline = str(uuid.uuid4())
    make_dir(no_deadline, 30)
    A.JOBS[no_deadline] = {"status": "awaiting_uploads", "log": []}
    check("not expired", A._expire_stale_gates(), 0)
    check("still awaiting", A.JOBS[no_deadline]["status"], "awaiting_uploads")
    check("directory kept", exists(no_deadline), True)

    print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURE(S): {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
