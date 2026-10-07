#!/usr/local/bin/python3
"""Trueno — minimal file host CGI for shared hosting.

POST a multipart/form-data body with:
    Authorization: Bearer <key>   (optional when ASSET_ANON_BUCKETS allows the bucket)
    file=@<binary>
    time=1h | 1d | 1w | keep      (optional; defaults to 1h)
    comment=<text>                (optional; shown in the listing)
    delete_key=<text>             (optional; lets the uploader delete the file later)

Saves the file to ./files/{bucket}/{id}.{ext} (relative to this script,
4-char base62 id) plus a sidecar {id}.{ext}.meta, and returns JSON
{url, name, size, time, expires_at, comment, has_delete_key}. The `keep`
bucket is exempt from cron expiry and gets a privileged URL form
(no /{bucket}/ prefix); other buckets are swept by cron.

Stdlib only and no `cgi` module — runs on shared hosting without pip install,
covers Python 3.6 through 3.13+ (where the cgi module was dropped).
"""

import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

# Apache invokes the CGI by absolute path, leaving sys.path without the
# script's own directory; insert it so `_trueno` is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _trueno import (  # noqa: E402
    ALLOWED_EXTS,
    ANON_BUCKETS,
    BUCKETS,
    COMMENT_MAX_CHARS,
    DEFAULT_TIME,
    DELETE_KEY_MAX_CHARS,
    KEY_FILE_DEFAULT,
    MAX_BYTES,
    META_SUFFIX,
    RATE_MAX_ITEMS,
    RATE_WINDOW_SEC,
    _ID_ALPHABET,
    _ID_LEN_BY_BUCKET,
    _ID_RETRIES,
    hash_delete_key,
    public_url,
    write_meta,
)

SCRIPT_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = SCRIPT_DIR / "files"


class _Reject(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _load_api_keys():  # type: () -> dict[str, frozenset[str]]
    """Read every accepted bearer token along with its allowed bucket set.

    Sources:
      1. ASSET_API_KEY env var — single token, full access (all buckets).
      2. The key file at $ASSET_KEY_FILE (default ~/.trueno-key), one
         entry per line. Each line:

             <token> [no-keep] [buckets:<csv>]

         - `no-keep`     shorthand for "everything except `keep`".
         - `buckets:<csv>` explicit allow-list (e.g., `buckets:1h,1d`).
                         Overrides `no-keep` if both are present.
         - No flags      full access (backward-compatible).

         Lines may carry trailing `# comment` (stripped before parsing);
         the file is expected to be chmod 600 and live outside docroot.

    Returns {token: frozenset(allowed_bucket_names)}. Empty dict means
    the uploader is unconfigured.
    """
    all_buckets = frozenset(BUCKETS.keys())
    keys: dict[str, frozenset[str]] = {}
    env_key = os.environ.get("ASSET_API_KEY", "").strip()
    if env_key:
        keys[env_key] = all_buckets
    key_path = Path(os.environ.get("ASSET_KEY_FILE", str(KEY_FILE_DEFAULT)))
    try:
        for raw in key_path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            token = parts[0]
            allowed: set[str] = set(all_buckets)
            buckets_override = None
            for flag in parts[1:]:
                if flag == "no-keep":
                    allowed.discard("keep")
                elif flag.startswith("buckets:"):
                    buckets_override = {b for b in flag[len("buckets:"):].split(",") if b in all_buckets}
            if buckets_override is not None:
                allowed = buckets_override
            keys[token] = frozenset(allowed)
    except OSError:
        pass
    return keys


def _respond(status: str, body: dict) -> None:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    sys.stdout.write(f"Status: {status}\r\n")
    sys.stdout.write("Content-Type: application/json; charset=utf-8\r\n")
    sys.stdout.write(f"Content-Length: {len(payload)}\r\n\r\n")
    sys.stdout.flush()
    sys.stdout.buffer.write(payload)


def _error(status: str, code: int, message: str) -> None:
    _respond(status, {"error": message, "status": code})


def _check_auth(api_keys):  # type: (dict[str, frozenset[str]]) -> frozenset[str] | None
    """Returns the allowed-bucket set for the presented bearer token, or
    None if the token is missing or unrecognised."""
    auth = os.environ.get("HTTP_AUTHORIZATION", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[len("Bearer "):].strip()
    return api_keys.get(token)


def _check_rate_limit() -> None:
    """Reject when bucket dirs collectively hold RATE_MAX_ITEMS files newer
    than the rate window. Reading dir entries (no counter file) keeps
    state-handling simple and makes the cron sweep a natural reset. The
    limit is host-wide — it sums across all buckets including `keep` —
    matching the original 'one shared counter' semantics."""
    cutoff = time.time() - RATE_WINDOW_SEC
    count = 0
    for bucket in BUCKETS:
        bucket_dir = UPLOAD_DIR / bucket
        if not bucket_dir.exists():
            continue
        try:
            for entry in os.scandir(str(bucket_dir)):
                try:
                    if not entry.is_file() or entry.name.endswith(META_SUFFIX):
                        continue
                    if entry.stat().st_mtime > cutoff:
                        count += 1
                        if count >= RATE_MAX_ITEMS:
                            raise _Reject(
                                429,
                                "rate limit: {} uploads in {}s".format(RATE_MAX_ITEMS, RATE_WINDOW_SEC),
                            )
                except OSError:
                    continue
        except OSError:
            continue  # bucket unreadable → skip; auth still gates the request


def _read_body() -> bytes:
    """Read the full request body. CONTENT_LENGTH-bounded; cap at MAX_BYTES + slack
    for multipart framing (boundary + headers ~1KB)."""
    try:
        length = int(os.environ.get("CONTENT_LENGTH", "0"))
    except ValueError:
        length = 0
    if length <= 0:
        raise _Reject(400, "missing or invalid Content-Length")
    if length > MAX_BYTES + 4096:
        raise _Reject(413, f"request body exceeds {MAX_BYTES} bytes")
    return sys.stdin.buffer.read(length)


def _parse_multipart(body, content_type):  # type: (bytes, str) -> Tuple[dict, str, bytes]
    """Parse a multipart/form-data body into form fields plus the file part.

    Returns (fields, file_filename, file_bytes), where `fields` is a dict
    of named text parts (decoded as UTF-8). Only the first part named
    'file' is consumed as the upload payload; everything else is treated
    as a small text field (`time`, etc.). 400 if no 'file' part is found.
    """
    m = re.match(r'multipart/form-data;\s*boundary=("?)([^";]+)\1', content_type or "")
    if not m:
        raise _Reject(400, "invalid Content-Type")
    boundary = m.group(2).encode("ascii")
    sep = b"--" + boundary

    fields: dict = {}
    file_filename: Optional[str] = None
    file_body: Optional[bytes] = None

    # body layout: [preamble] sep CRLF part CRLF sep CRLF part ... sep "--"
    # split on the separator and skip the preamble (idx 0) and the closing
    # marker (last chunk starts with b"--").
    chunks = body.split(sep)
    for chunk in chunks[1:]:
        if chunk.startswith(b"--"):
            break  # end marker
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        if chunk.endswith(b"\r\n"):
            chunk = chunk[:-2]
        head_end = chunk.find(b"\r\n\r\n")
        if head_end < 0:
            continue
        headers_raw = chunk[:head_end].decode("utf-8", errors="replace")
        part_body = chunk[head_end + 4:]
        disp_match = re.search(r'Content-Disposition:\s*form-data;([^\r\n]*)', headers_raw, re.IGNORECASE)
        if not disp_match:
            continue
        params = disp_match.group(1)
        name_m = re.search(r'name="([^"]*)"', params)
        if not name_m:
            continue
        part_name = name_m.group(1)
        if part_name == "file":
            if file_body is not None:
                continue  # second 'file' part — ignore, first wins
            fn_m = re.search(r'filename="([^"]*)"', params)
            filename = fn_m.group(1) if fn_m else ""
            if not filename:
                raise _Reject(400, "file part is missing filename")
            file_filename = filename
            file_body = part_body
        else:
            fields[part_name] = part_body.decode("utf-8", errors="replace").strip()

    if file_body is None or file_filename is None:
        raise _Reject(400, "no 'file' field in multipart body")
    return fields, file_filename, file_body


def _save_upload(raw_filename: str, data: bytes, bucket: str) -> str:
    if len(data) == 0:
        raise _Reject(400, "empty file")
    if len(data) > MAX_BYTES:
        raise _Reject(413, f"file exceeds {MAX_BYTES} bytes")
    safe = Path(raw_filename).name  # strip any path components
    ext = Path(safe).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise _Reject(415, f"extension {ext or '<none>'} not allowed")
    bucket_dir = UPLOAD_DIR / bucket
    bucket_dir.mkdir(parents=True, exist_ok=True)
    id_len = _ID_LEN_BY_BUCKET[bucket]
    for _ in range(_ID_RETRIES):
        stem = "".join(secrets.choice(_ID_ALPHABET) for _ in range(id_len))
        name = stem + ext
        path = bucket_dir / name
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return name
    raise _Reject(500, f"could not allocate filename after {_ID_RETRIES} retries")


def main() -> None:
    method = os.environ.get("REQUEST_METHOD", "GET").upper()
    if method != "POST":
        _error("405 Method Not Allowed", 405, "POST only")
        return

    api_keys = _load_api_keys()
    if not api_keys and not ANON_BUCKETS:
        _error("503 Service Unavailable", 503, "uploader not configured")
        return
    allowed_buckets = _check_auth(api_keys)
    if allowed_buckets is None:
        # No (valid) token: fall back to the operator's anonymous allowance.
        if not ANON_BUCKETS:
            _error("401 Unauthorized", 401, "invalid or missing bearer token")
            return
        allowed_buckets = ANON_BUCKETS

    try:
        _check_rate_limit()
        body = _read_body()
        fields, filename, data = _parse_multipart(body, os.environ.get("CONTENT_TYPE", ""))
        bucket = fields.get("time", DEFAULT_TIME) or DEFAULT_TIME
        if bucket not in BUCKETS:
            raise _Reject(400, f"invalid time={bucket!r}; expected one of {sorted(BUCKETS)}")
        if bucket not in allowed_buckets:
            raise _Reject(
                403,
                f"key not authorized for bucket {bucket!r}; allowed: {sorted(allowed_buckets)}",
            )
        comment = fields.get("comment", "")[:COMMENT_MAX_CHARS]
        delete_key = fields.get("delete_key", "")[:DELETE_KEY_MAX_CHARS]
        name = _save_upload(filename, data, bucket)
    except _Reject as e:
        _error(f"{e.code} {e.message}", e.code, e.message)
        return

    original = Path(filename).name
    now = int(time.time())
    write_meta(
        UPLOAD_DIR / bucket / name,
        {
            "name": original,
            "comment": comment,
            "delete_key": hash_delete_key(delete_key) if delete_key else "",
            "uploaded_at": now,
            "size": len(data),
        },
    )
    ttl = BUCKETS[bucket]
    expires_at = now + ttl if ttl is not None else None
    _respond(
        "200 OK",
        {
            "url": public_url(bucket, name),
            "name": original,
            "size": len(data),
            "time": bucket,
            "expires_at": expires_at,
            "comment": comment,
            "has_delete_key": bool(delete_key),
        },
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # last-resort fallback so the client gets JSON, not HTML
        _error("500 Internal Server Error", 500, f"unexpected: {type(e).__name__}: {e}")
