"""Anyone who completes Google sign-in is allowed to use the app -- there's no
account allowlist. Access is controlled by not publicizing the URL, not by a
technical gate. The only thing tracked here is:
  - admin status (Pop Lock curation personalization, web-search access,
    custom/saved themes, /analytics) -- a small explicit list managed via
    manage_users.py.
  - each signed-in user's own uploaded YouTube cookies, keyed by email.
"""
import json
import os
import re
import threading

_DATA_DIR = os.environ.get("DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
ADMINS_PATH = os.path.join(_DATA_DIR, "admins.json")
USER_DATA_DIR = os.path.join(_DATA_DIR, "user_data")
_lock = threading.Lock()


def _normalize(email: str) -> str:
    return (email or "").strip().lower()


def _load_admins() -> list[str]:
    if not os.path.exists(ADMINS_PATH):
        return []
    try:
        with open(ADMINS_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def _save_admins(admins: list[str]):
    with _lock:
        with open(ADMINS_PATH, "w") as f:
            json.dump(sorted(set(admins)), f, indent=2)


def add_admin(email: str) -> None:
    email = _normalize(email)
    if not email:
        raise ValueError("email is required")
    admins = _load_admins()
    if email not in admins:
        admins.append(email)
        _save_admins(admins)


def remove_admin(email: str) -> None:
    email = _normalize(email)
    _save_admins([a for a in _load_admins() if a != email])


def list_admins() -> list[str]:
    return _load_admins()


def is_admin(email: str | None) -> bool:
    email = _normalize(email)
    return bool(email) and email in _load_admins()


def _safe_dir(email: str) -> str:
    return re.sub(r"[^a-zA-Z0-9@._-]", "_", _normalize(email)) or "unknown"


def cookie_path(email: str) -> str:
    return os.path.join(USER_DATA_DIR, _safe_dir(email), "cookies.txt")


def has_cookies(email: str) -> bool:
    return os.path.exists(cookie_path(email))


def save_cookies(email: str, file_bytes: bytes) -> None:
    path = cookie_path(email)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(file_bytes)
    os.chmod(path, 0o600)
