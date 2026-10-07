"""Classic uploader mode: sidecar metadata, anonymous uploads, listing, deletion.

Each CGI is run as a subprocess from a tmp docroot with a synthesized CGI
environment, like test_upload.py does.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

DOCROOT_SRC = Path(__file__).resolve().parent.parent / "docroot"
PYTHON = sys.executable
BASE = "https://a.example.test"


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    docroot = tmp_path / "docroot"
    docroot.mkdir()
    for name in ("upload.cgi", "list.cgi", "delete.cgi"):
        (docroot / name).write_bytes((DOCROOT_SRC / name).read_bytes())
    for helper in DOCROOT_SRC.glob("_*.py"):
        (docroot / helper.name).write_bytes(helper.read_bytes())
    home = tmp_path / "home"
    home.mkdir()
    return docroot, home


def _run(docroot: Path, home: Path, script: str, env: dict[str, str], body: bytes = b"") -> tuple[int, dict]:
    full_env = {
        **os.environ,
        "HOME": str(home),
        "ASSET_PUBLIC_URL": BASE,
        "PYTHONIOENCODING": "utf-8",
        **env,
    }
    proc = subprocess.run(
        [PYTHON, str(docroot / script)],
        input=body,
        env=full_env,
        capture_output=True,
        timeout=10,
        cwd=docroot,
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    status_match = re.search(r"^Status: (\d+)", out, re.MULTILINE)
    status = int(status_match.group(1)) if status_match else 0
    return status, json.loads(out.split("\r\n\r\n", 1)[-1])


def _multipart(fields: dict[str, str], filename: str | None = "a.jpg", content: bytes = b"\xff\xd8\xff\xd9"):
    boundary = "----testbnd"
    body = b""
    if filename is not None:
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + content + b"\r\n"
    for k, v in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    body += f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def _upload(docroot, home, fields, auth: str | None = "Bearer test-key", extra_env=None, filename="a.jpg"):
    body, ctype = _multipart(fields, filename=filename)
    env = {
        "ASSET_API_KEY": "test-key",
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": ctype,
        "CONTENT_LENGTH": str(len(body)),
        **(extra_env or {}),
    }
    if auth:
        env["HTTP_AUTHORIZATION"] = auth
    return _run(docroot, home, "upload.cgi", env, body)


def _delete(docroot, home, bucket, name, key=None, auth=None):
    body = f"bucket={bucket}&id={name}" + (f"&key={key}" if key is not None else "")
    env = {
        "ASSET_API_KEY": "test-key",
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "CONTENT_LENGTH": str(len(body)),
    }
    if auth:
        env["HTTP_AUTHORIZATION"] = auth
    return _run(docroot, home, "delete.cgi", env, body.encode())


def _list(docroot, home, query=""):
    return _run(docroot, home, "list.cgi", {"REQUEST_METHOD": "GET", "QUERY_STRING": query})


def test_upload_writes_sidecar_with_name_comment_and_hashed_key(tmp_path):
    docroot, home = _seed(tmp_path)
    status, payload = _upload(docroot, home, {"comment": "hello", "delete_key": "s3cret"}, filename="My Photo.jpg")
    assert status == 200, payload
    assert payload["name"] == "My Photo.jpg"
    assert payload["comment"] == "hello"
    assert payload["has_delete_key"] is True
    name = payload["url"].rsplit("/", 1)[-1]
    meta = json.loads((docroot / "files" / "1h" / (name + ".meta")).read_text())
    assert meta["name"] == "My Photo.jpg"
    assert meta["comment"] == "hello"
    assert meta["delete_key"] != "s3cret" and len(meta["delete_key"]) == 64
    assert meta["size"] == 4


def test_anonymous_upload_is_off_by_default(tmp_path):
    docroot, home = _seed(tmp_path)
    status, _ = _upload(docroot, home, {}, auth=None)
    assert status == 401


def test_anonymous_upload_allowed_buckets_only(tmp_path):
    docroot, home = _seed(tmp_path)
    anon = {"ASSET_ANON_BUCKETS": "1h,1d"}
    status, payload = _upload(docroot, home, {"time": "1d"}, auth=None, extra_env=anon)
    assert status == 200, payload
    status, payload = _upload(docroot, home, {"time": "keep"}, auth=None, extra_env=anon)
    assert status == 403, payload
    # A valid token still gets its own allowance.
    status, payload = _upload(docroot, home, {"time": "keep"}, extra_env=anon)
    assert status == 200, payload


def test_anonymous_mode_works_without_any_token_configured(tmp_path):
    docroot, home = _seed(tmp_path)
    body, ctype = _multipart({})
    env = {
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": ctype,
        "CONTENT_LENGTH": str(len(body)),
        "ASSET_ANON_BUCKETS": "1h",
        "ASSET_API_KEY": "",
    }
    status, payload = _run(docroot, home, "upload.cgi", env, body)
    assert status == 200, payload


def test_list_merges_meta_and_sorts_newest_first(tmp_path):
    docroot, home = _seed(tmp_path)
    _, first = _upload(docroot, home, {"comment": "one"})
    _, second = _upload(docroot, home, {"comment": "two", "time": "keep"}, filename="b.png")
    # A pre-sidecar file: no .meta at all.
    legacy = docroot / "files" / "1w"
    legacy.mkdir()
    (legacy / "AbCd.txt").write_bytes(b"old")
    os.utime(legacy / "AbCd.txt", (1_000_000, 1_000_000))

    status, payload = _list(docroot, home)
    assert status == 200, payload
    assert payload["total"] == 3
    ids = [it["id"] for it in payload["items"]]
    assert ids[-1] == "AbCd.txt"
    by_id = {it["id"]: it for it in payload["items"]}
    keep = by_id[second["url"].rsplit("/", 1)[-1]]
    assert keep["name"] == "b.png" and keep["comment"] == "two" and keep["expires_at"] is None
    assert keep["url"] == second["url"]
    hour = by_id[first["url"].rsplit("/", 1)[-1]]
    assert hour["expires_at"] is not None and hour["has_delete_key"] is False
    assert by_id["AbCd.txt"]["name"] == "AbCd.txt" and by_id["AbCd.txt"]["comment"] == ""
    assert not any(it["id"].endswith(".meta") for it in payload["items"])

    status, payload = _list(docroot, home, "bucket=keep")
    assert status == 200 and payload["total"] == 1
    status, payload = _list(docroot, home, "bucket=nope")
    assert status == 400


def test_delete_by_key_bearer_and_failures(tmp_path):
    docroot, home = _seed(tmp_path)
    _, with_key = _upload(docroot, home, {"delete_key": "pw"})
    _, no_key = _upload(docroot, home, {})
    a = with_key["url"].rsplit("/", 1)[-1]
    b = no_key["url"].rsplit("/", 1)[-1]

    status, _ = _delete(docroot, home, "1h", a, key="wrong")
    assert status == 403
    status, _ = _delete(docroot, home, "1h", b, key="pw")
    assert status == 403  # no key stored → key auth impossible
    status, payload = _delete(docroot, home, "1h", a, key="pw")
    assert status == 200, payload
    assert not (docroot / "files" / "1h" / a).exists()
    assert not (docroot / "files" / "1h" / (a + ".meta")).exists()
    status, _ = _delete(docroot, home, "1h", a, key="pw")
    assert status == 404
    status, payload = _delete(docroot, home, "1h", b, auth="Bearer test-key")
    assert status == 200, payload
    status, _ = _delete(docroot, home, "1h", "..", key="pw")
    assert status == 400
    status, _ = _delete(docroot, home, "zz", b, key="pw")
    assert status == 400
