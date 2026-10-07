"""Shared constants for the Trueno CGI scripts.

upload.cgi enforces the limits; info.cgi reports them. Both import from
here so the values can never drift between enforcement and what the LP /
Swagger UI advertises.

Apache is configured to refuse direct GETs of `_*.py` (see .htaccess), so
this module stays internal even though it sits inside the document root.
"""

import hashlib
import json
import os
import string
from pathlib import Path

ALLOWED_EXTS = frozenset({
    # images
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".heic", ".avif", ".bmp", ".ico",
    # documents / text
    ".pdf", ".txt", ".md", ".csv", ".json", ".yml", ".yaml", ".html", ".xml", ".log",
    # video
    ".mp4", ".mov", ".webm", ".mkv",
    # audio
    ".mp3", ".m4a", ".wav", ".ogg", ".flac",
    # archives  (Path.suffix only catches the last segment, so foo.tar.gz → .gz)
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z",
})
MAX_BYTES = 50 * 1024 * 1024

# TTL buckets. Each upload lands under files/<bucket>/<id>.<ext>; cron sweeps
# 1h/1d/1w by mtime, leaving `keep` untouched. The `keep` bucket also gets
# a privileged URL form (no /<bucket>/ prefix) so the short URL acts as a
# soft "this one is long-lived" signal — see .htaccess rewrites.
BUCKETS = {
    "1h":   3600,
    "1d":   86400,
    "1w":   604800,
    "keep": None,   # no auto-expire
}
DEFAULT_TIME = "1h"

PUBLIC_URL_BASE = os.environ.get("ASSET_PUBLIC_URL", "https://a.example.test").rstrip("/")
WEB_UI_URL = os.environ.get("WEB_UI_URL", "https://ui.example.test").rstrip("/")
KEY_FILE_DEFAULT = Path.home() / ".trueno-key"

# Per-bucket ID length. The URL length itself signals the bucket category:
#   /1h/AbCd.jpg     (4-char + bucket prefix) = short-lived, expect URL reuse
#   /AbCdEf.jpg      (6-char short form)      = `keep` bucket, URL is stable
#
# Sizing rationale:
#   - 4-char (62^4 ≈ 14.8M) is plenty for short-lived buckets where TTL
#     reclaims slots and URL reuse is part of the contract anyway.
#   - 6-char (62^6 ≈ 5.7e10) gives the keep bucket enormous headroom for
#     decades of accumulation without practical risk of operator-deletion
#     slot reuse.
# Collisions are handled by O_EXCL atomic create + retry inside each bucket.
_ID_ALPHABET = string.ascii_letters + string.digits
_ID_LEN_BY_BUCKET = {
    "1h":   4,
    "1d":   4,
    "1w":   4,
    "keep": 6,
}
_ID_RETRIES = 10

# Rate limit reuses files/ as the only state — count items whose mtime falls
# inside the window. Cron expiry naturally drains the count over time. It's
# global (not per-key); good enough for small private deployments where
# abuse would come from a leaked key, not a hostile internet.
RATE_WINDOW_SEC = int(os.environ.get("ASSET_RATE_WINDOW_SEC", "600"))   # 10 min
RATE_MAX_ITEMS = int(os.environ.get("ASSET_RATE_MAX_ITEMS", "30"))

# Anonymous uploads (the classic "anyone can post" uploader). Off unless the
# operator lists buckets, e.g. "1h,1d,1w": `make deploy` bakes ANON_BUCKETS from
# make.local into the default below (some shared hosts ignore SetEnv in
# .htaccess), and the env var still overrides it. A bearer token keeps working
# and is still what decides `keep`.
ANON_BUCKETS_DEFAULT = ""
ANON_BUCKETS = frozenset(
    b.strip() for b in os.environ.get("ASSET_ANON_BUCKETS", ANON_BUCKETS_DEFAULT).split(",") if b.strip() in BUCKETS
)

# Each upload may carry a sidecar `<id>.<ext>.meta` (JSON: original name,
# comment, hashed delete key, upload time). Apache never serves it (see
# .htaccess); list.cgi reads it, delete.cgi checks it, cron sweeps it along
# with the file since both share an mtime.
META_SUFFIX = ".meta"
COMMENT_MAX_CHARS = 200
DELETE_KEY_MAX_CHARS = 64
LIST_PAGE_SIZE = 50


def public_url(bucket, name):  # type: (str, str) -> str
    """`keep` gets the privileged short form `<base>/<id>.<ext>`; the other
    buckets expose the bucket as a path segment so the URL itself tells the
    recipient how long it lives."""
    if bucket == "keep":
        return f"{PUBLIC_URL_BASE}/{name}"
    return f"{PUBLIC_URL_BASE}/{bucket}/{name}"


def hash_delete_key(key):  # type: (str) -> str
    """Delete keys are the classic uploader's per-file password. Only the
    hash is stored; a leaked .meta does not give the key away."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def read_meta(path):  # type: (Path) -> dict
    """The sidecar next to an upload, or {} when it has none (pre-meta files)."""
    try:
        with open(str(path) + META_SUFFIX, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_meta(path, meta):  # type: (Path, dict) -> None
    tmp = str(path) + META_SUFFIX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False)
    os.replace(tmp, str(path) + META_SUFFIX)
