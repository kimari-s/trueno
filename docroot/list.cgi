#!/usr/local/bin/python3
"""Trueno — listing endpoint.

GET /list?bucket=<all|1h|1d|1w|keep>&page=<n>  → JSON

Scans files/<bucket>/ (no database) and merges each file's sidecar .meta
when present; files uploaded before sidecars existed are listed with their
id as the name. Newest first, LIST_PAGE_SIZE per page. Public: the listing
shows nothing the URLs do not already reveal, and the classic uploader
this mirrors always had a public index.
"""

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _trueno import (  # noqa: E402
    BUCKETS,
    LIST_PAGE_SIZE,
    META_SUFFIX,
    public_url,
    read_meta,
)

SCRIPT_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = SCRIPT_DIR / "files"


def _respond(status: str, body: dict) -> None:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    sys.stdout.write(f"Status: {status}\r\n")
    sys.stdout.write("Content-Type: application/json; charset=utf-8\r\n")
    sys.stdout.write("Cache-Control: no-cache\r\n")
    sys.stdout.write(f"Content-Length: {len(payload)}\r\n\r\n")
    sys.stdout.flush()
    sys.stdout.buffer.write(payload)


def _entries(buckets):  # type: (list) -> list
    items = []
    for bucket in buckets:
        bucket_dir = UPLOAD_DIR / bucket
        if not bucket_dir.is_dir():
            continue
        try:
            scan = list(os.scandir(str(bucket_dir)))
        except OSError:
            continue
        for entry in scan:
            try:
                if not entry.is_file() or entry.name.endswith(META_SUFFIX) or entry.name.startswith("."):
                    continue
                st = entry.stat()
            except OSError:
                continue
            meta = read_meta(Path(entry.path))
            mtime = int(st.st_mtime)
            ttl = BUCKETS[bucket]
            items.append(
                {
                    "bucket": bucket,
                    "id": entry.name,
                    "url": public_url(bucket, entry.name),
                    "name": meta.get("name") or entry.name,
                    "comment": meta.get("comment", ""),
                    "size": st.st_size,
                    "uploaded_at": int(meta.get("uploaded_at") or mtime),
                    "expires_at": mtime + ttl if ttl is not None else None,
                    "has_delete_key": bool(meta.get("delete_key")),
                }
            )
    items.sort(key=lambda it: (it["uploaded_at"], it["id"]), reverse=True)
    return items


def main() -> None:
    method = os.environ.get("REQUEST_METHOD", "GET").upper()
    if method != "GET":
        _respond("405 Method Not Allowed", {"error": "GET only", "status": 405})
        return
    query = parse_qs(os.environ.get("QUERY_STRING", ""))
    bucket = (query.get("bucket") or ["all"])[0]
    if bucket == "all":
        buckets = list(BUCKETS)
    elif bucket in BUCKETS:
        buckets = [bucket]
    else:
        _respond("400 Bad Request", {"error": f"invalid bucket={bucket!r}", "status": 400})
        return
    try:
        page = max(1, int((query.get("page") or ["1"])[0]))
    except ValueError:
        page = 1

    items = _entries(buckets)
    total = len(items)
    start = (page - 1) * LIST_PAGE_SIZE
    _respond(
        "200 OK",
        {
            "bucket": bucket,
            "page": page,
            "page_size": LIST_PAGE_SIZE,
            "total": total,
            "pages": max(1, (total + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE),
            "now": int(time.time()),
            "items": items[start : start + LIST_PAGE_SIZE],
        },
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _respond("500 Internal Server Error", {"error": f"unexpected: {type(e).__name__}: {e}", "status": 500})
