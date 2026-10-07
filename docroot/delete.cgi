#!/usr/local/bin/python3
"""Trueno — delete endpoint.

POST /delete  (application/x-www-form-urlencoded or multipart/form-data)
    bucket=<1h|1d|1w|keep>
    id=<id>.<ext>
    key=<delete key>             (the one given at upload time)
  or
    Authorization: Bearer <key>  (any configured token: the operator's override)

Removes files/<bucket>/<id>.<ext> and its sidecar. 403 when neither the
delete key nor a bearer token checks out; 404 when the file is already gone.
"""

import hmac
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _trueno import (  # noqa: E402
    BUCKETS,
    KEY_FILE_DEFAULT,
    META_SUFFIX,
    hash_delete_key,
    read_meta,
)

SCRIPT_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = SCRIPT_DIR / "files"
_ID_RE = re.compile(r"^[A-Za-z0-9]{4,8}\.[A-Za-z0-9]{2,5}$")


def _respond(status: str, body: dict) -> None:
    import json

    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    sys.stdout.write(f"Status: {status}\r\n")
    sys.stdout.write("Content-Type: application/json; charset=utf-8\r\n")
    sys.stdout.write(f"Content-Length: {len(payload)}\r\n\r\n")
    sys.stdout.flush()
    sys.stdout.buffer.write(payload)


def _error(code: int, message: str) -> None:
    _respond(f"{code} {message}", {"error": message, "status": code})


def _bearer_tokens():  # type: () -> set
    """Every configured token (same sources as upload.cgi). Any of them may
    delete anything: this is the operator's override, not a per-user right."""
    tokens = set()
    env_key = os.environ.get("ASSET_API_KEY", "").strip()
    if env_key:
        tokens.add(env_key)
    key_path = Path(os.environ.get("ASSET_KEY_FILE", str(KEY_FILE_DEFAULT)))
    try:
        for raw in key_path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                tokens.add(line.split()[0])
    except OSError:
        pass
    return tokens


def _has_bearer() -> bool:
    auth = os.environ.get("HTTP_AUTHORIZATION", "")
    if not auth.startswith("Bearer "):
        return False
    return auth[len("Bearer ") :].strip() in _bearer_tokens()


def _fields():  # type: () -> dict
    """Form fields from a urlencoded or multipart body (small text parts only)."""
    try:
        length = int(os.environ.get("CONTENT_LENGTH", "0"))
    except ValueError:
        length = 0
    if length <= 0 or length > 65536:
        return {}
    body = sys.stdin.buffer.read(length)
    ctype = os.environ.get("CONTENT_TYPE", "")
    if ctype.startswith("multipart/form-data"):
        m = re.match(r'multipart/form-data;\s*boundary=("?)([^";]+)\1', ctype)
        if not m:
            return {}
        fields = {}
        for chunk in body.split(b"--" + m.group(2).encode("ascii"))[1:]:
            if chunk.startswith(b"--"):
                break
            head_end = chunk.find(b"\r\n\r\n")
            if head_end < 0:
                continue
            name_m = re.search(rb'name="([^"]*)"', chunk[:head_end])
            if name_m:
                fields[name_m.group(1).decode()] = chunk[head_end + 4 :].strip(b"\r\n").decode("utf-8", "replace")
        return fields
    return {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}


def main() -> None:
    method = os.environ.get("REQUEST_METHOD", "GET").upper()
    if method != "POST":
        _error(405, "POST only")
        return
    fields = _fields()
    bucket = fields.get("bucket", "")
    name = fields.get("id", "")
    if bucket not in BUCKETS or not _ID_RE.match(name):
        _error(400, "bucket and id are required")
        return
    path = UPLOAD_DIR / bucket / name
    if not path.is_file():
        _error(404, "not found")
        return

    allowed = _has_bearer()
    if not allowed:
        stored = read_meta(path).get("delete_key", "")
        given = fields.get("key", "")
        allowed = bool(stored) and bool(given) and hmac.compare_digest(stored, hash_delete_key(given))
    if not allowed:
        _error(403, "delete key does not match")
        return

    for victim in (path, Path(str(path) + META_SUFFIX)):
        try:
            victim.unlink()
        except FileNotFoundError:
            pass
    _respond("200 OK", {"deleted": name, "bucket": bucket})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _error(500, f"unexpected: {type(e).__name__}: {e}")
