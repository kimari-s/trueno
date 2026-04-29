#!/usr/local/bin/python3
"""Trueno — runtime info endpoint.

GET /info → JSON describing the live limits the uploader enforces. The LP
fetches it on load so Swagger UI can advertise the actual configured server
URL, max body size, allowed extensions, and rate limit without requiring a
redeploy when those knobs change.

Stdlib only, same execution model as upload.cgi.
"""

import json
import os
import sys
from pathlib import Path

# Apache invokes the CGI by absolute path, leaving sys.path without the
# script's own directory; insert it so `_trueno` is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _trueno import (  # noqa: E402
    ALLOWED_EXTS,
    BUCKETS,
    DEFAULT_TIME,
    MAX_BYTES,
    PUBLIC_URL_BASE,
    RATE_MAX_ITEMS,
    RATE_WINDOW_SEC,
    WEB_UI_URL,
    _ID_LEN_BY_BUCKET,
)


def _respond(status: str, body: dict) -> None:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    sys.stdout.write(f"Status: {status}\r\n")
    sys.stdout.write("Content-Type: application/json; charset=utf-8\r\n")
    sys.stdout.write("Cache-Control: no-cache\r\n")
    sys.stdout.write(f"Content-Length: {len(payload)}\r\n\r\n")
    sys.stdout.flush()
    sys.stdout.buffer.write(payload)


def main() -> None:
    method = os.environ.get("REQUEST_METHOD", "GET").upper()
    if method != "GET":
        _respond("405 Method Not Allowed", {"error": "GET only", "status": 405})
        return

    _respond(
        "200 OK",
        {
            "public_url": PUBLIC_URL_BASE,
            "web_ui_url": WEB_UI_URL,
            "max_bytes": MAX_BYTES,
            "allowed_exts": sorted(ALLOWED_EXTS),
            "ttl_choices": dict(BUCKETS),  # {bucket: ttl_seconds_or_null}
            "default_time": DEFAULT_TIME,
            "rate_window_sec": RATE_WINDOW_SEC,
            "rate_max_items": RATE_MAX_ITEMS,
            "id_len_by_bucket": dict(_ID_LEN_BY_BUCKET),
        },
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _respond(
            "500 Internal Server Error",
            {"error": f"unexpected: {type(e).__name__}: {e}", "status": 500},
        )
