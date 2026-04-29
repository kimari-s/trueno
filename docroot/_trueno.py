"""Shared constants for the Trueno CGI scripts.

upload.cgi enforces the limits; info.cgi reports them. Both import from
here so the values can never drift between enforcement and what the LP /
Swagger UI advertises.

Apache is configured to refuse direct GETs of `_*.py` (see .htaccess), so
this module stays internal even though it sits inside the document root.
"""

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
