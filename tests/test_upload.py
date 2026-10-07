"""Smoke test for upload.cgi by driving it as a subprocess with a CGI env.

The CGI is plain stdlib so we can invoke it directly without spinning up
Apache. Each test crafts the multipart body, sets the env vars Apache would
inject, and reads stdout for the JSON response.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

CGI = Path(__file__).resolve().parent.parent / "docroot" / "upload.cgi"
PYTHON = sys.executable


def _multipart(
    filename: str,
    content: bytes,
    content_type: str = "image/jpeg",
    time: str | None = None,
) -> tuple[bytes, str]:
    boundary = "----testbnd"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode() + content + b"\r\n"
    if time is not None:
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="time"\r\n\r\n'
            f"{time}\r\n"
        ).encode()
    body += f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def _run(env: dict[str, str], body: bytes) -> tuple[int, dict]:
    proc = subprocess.run(
        [PYTHON, str(CGI)],
        input=body,
        env={**os.environ, **env},
        capture_output=True,
        timeout=10,
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    status_match = re.search(r"^Status: (\d+)", out, re.MULTILINE)
    status = int(status_match.group(1)) if status_match else 0
    body_text = out.split("\r\n\r\n", 1)[-1]
    try:
        payload = json.loads(body_text)
    except json.JSONDecodeError:
        payload = {"_raw": body_text}
    return status, payload


def _base_env(api_key: str = "test-key") -> dict[str, str]:
    return {
        "ASSET_API_KEY": api_key,
        "ASSET_PUBLIC_URL": "https://a.example.test",
        "REQUEST_METHOD": "POST",
        "PYTHONIOENCODING": "utf-8",
    }


def test_rejects_get():
    status, payload = _run({**_base_env(), "REQUEST_METHOD": "GET"}, b"")
    assert status == 405
    assert payload["error"] == "POST only"


def test_rejects_missing_auth():
    body, ctype = _multipart("a.jpg", b"hello")
    env = {**_base_env(), "CONTENT_TYPE": ctype, "CONTENT_LENGTH": str(len(body))}
    status, _ = _run(env, body)
    assert status == 401


def test_rejects_bad_extension():
    body, ctype = _multipart("a.exe", b"MZ\x90\x00")
    env = {
        **_base_env(),
        "HTTP_AUTHORIZATION": "Bearer test-key",
        "CONTENT_TYPE": ctype,
        "CONTENT_LENGTH": str(len(body)),
    }
    status, payload = _run(env, body)
    assert status == 415, payload


def _seed_docroot(docroot: Path) -> None:
    """Copy upload.cgi plus its sibling helper modules into a tmp docroot.

    upload.cgi imports `_trueno` from its own directory, so the helper has
    to travel with it whenever a test runs the CGI from a tmp location."""
    docroot.mkdir(exist_ok=True)
    (docroot / "upload.cgi").write_bytes(CGI.read_bytes())
    for helper in CGI.parent.glob("_*.py"):
        (docroot / helper.name).write_bytes(helper.read_bytes())


def _run_in_tmp(tmp_path, body, env, key_file_lines=None):
    """Copy the CGI into a tmp docroot and invoke it there, optionally
    seeding a key file in the tmp HOME so file-based auth can be exercised."""
    docroot = tmp_path / "docroot"
    _seed_docroot(docroot)
    home = tmp_path / "home"
    home.mkdir()
    if key_file_lines is not None:
        (home / ".trueno-key").write_text("\n".join(key_file_lines) + "\n")
    full_env = {**os.environ, **env, "HOME": str(home)}
    proc = subprocess.run(
        [PYTHON, str(docroot / "upload.cgi")],
        input=body,
        env=full_env,
        capture_output=True,
        timeout=10,
        cwd=docroot,
    )
    return docroot, proc


def _payload(proc) -> dict:
    return json.loads(proc.stdout.split(b"\r\n\r\n", 1)[1])


def test_default_bucket_is_1h(tmp_path):
    """No `time` field → bucket=1h, URL contains /1h/ with 4-char ID, expires_at is set."""
    body, ctype = _multipart("photo.jpg", b"\xff\xd8\xff\xd9")  # min-JPEG
    env = {
        **_base_env(),
        "HTTP_AUTHORIZATION": "Bearer test-key",
        "CONTENT_TYPE": ctype,
        "CONTENT_LENGTH": str(len(body)),
    }
    docroot, proc = _run_in_tmp(tmp_path, body, env)
    assert b"Status: 200" in proc.stdout, proc.stdout
    payload = _payload(proc)
    assert re.match(r"^https://a\.example\.test/1h/[A-Za-z0-9]{4}\.jpg$", payload["url"]), payload["url"]
    assert payload["time"] == "1h"
    assert isinstance(payload["expires_at"], int)
    assert payload["size"] == 4
    files = [f for f in (docroot / "files" / "1h").iterdir() if f.suffix != ".meta"]
    assert len(files) == 1
    # 4-char stem + .jpg
    assert re.match(r"^[A-Za-z0-9]{4}\.jpg$", files[0].name), files[0].name
    assert files[0].read_bytes() == b"\xff\xd8\xff\xd9"


def test_keep_bucket_uses_6char_short_url_and_null_expires(tmp_path):
    """`keep` gets a 6-char ID and the privileged URL form (no bucket prefix)."""
    body, ctype = _multipart("photo.jpg", b"\xff\xd8", time="keep")
    env = {
        **_base_env(),
        "HTTP_AUTHORIZATION": "Bearer test-key",
        "CONTENT_TYPE": ctype,
        "CONTENT_LENGTH": str(len(body)),
    }
    docroot, proc = _run_in_tmp(tmp_path, body, env)
    assert b"Status: 200" in proc.stdout, proc.stdout
    payload = _payload(proc)
    # privileged short URL — no /<bucket>/ segment, 6-char ID
    assert re.match(r"^https://a\.example\.test/[A-Za-z0-9]{6}\.jpg$", payload["url"]), payload["url"]
    assert payload["time"] == "keep"
    assert payload["expires_at"] is None
    assert (docroot / "files" / "keep").is_dir()
    files = [f for f in (docroot / "files" / "keep").iterdir() if f.suffix != ".meta"]
    assert len(files) == 1
    assert re.match(r"^[A-Za-z0-9]{6}\.jpg$", files[0].name), files[0].name


def test_1d_bucket_uses_prefixed_url_with_4char_id(tmp_path):
    body, ctype = _multipart("photo.jpg", b"\xff\xd8", time="1d")
    env = {
        **_base_env(),
        "HTTP_AUTHORIZATION": "Bearer test-key",
        "CONTENT_TYPE": ctype,
        "CONTENT_LENGTH": str(len(body)),
    }
    docroot, proc = _run_in_tmp(tmp_path, body, env)
    assert b"Status: 200" in proc.stdout, proc.stdout
    payload = _payload(proc)
    assert re.match(r"^https://a\.example\.test/1d/[A-Za-z0-9]{4}\.jpg$", payload["url"]), payload["url"]
    assert payload["time"] == "1d"
    assert isinstance(payload["expires_at"], int)
    assert (docroot / "files" / "1d").is_dir()


def test_invalid_time_rejected(tmp_path):
    body, ctype = _multipart("photo.jpg", b"\xff\xd8", time="forever")
    env = {
        **_base_env(),
        "HTTP_AUTHORIZATION": "Bearer test-key",
        "CONTENT_TYPE": ctype,
        "CONTENT_LENGTH": str(len(body)),
    }
    _, proc = _run_in_tmp(tmp_path, body, env)
    assert b"Status: 400" in proc.stdout, proc.stdout
    payload = _payload(proc)
    assert "invalid time" in payload["error"]


def test_multi_key_from_file(tmp_path):
    """Bearer = any of the keys listed in ~/.trueno-key (one per line, with
    inline `# comment` allowed)."""
    body, ctype = _multipart("photo.jpg", b"\xff\xd8")
    env = {
        **{k: v for k, v in _base_env().items() if k != "ASSET_API_KEY"},
        "HTTP_AUTHORIZATION": "Bearer second-key",
        "CONTENT_TYPE": ctype,
        "CONTENT_LENGTH": str(len(body)),
    }
    _, proc = _run_in_tmp(
        tmp_path, body, env,
        key_file_lines=["# bot", "first-key", "# personal", "second-key  # leaked-test-token"],
    )
    assert b"Status: 200" in proc.stdout, proc.stdout


def test_unknown_key_rejected(tmp_path):
    body, ctype = _multipart("photo.jpg", b"\xff\xd8")
    env = {
        **{k: v for k, v in _base_env().items() if k != "ASSET_API_KEY"},
        "HTTP_AUTHORIZATION": "Bearer wrong-key",
        "CONTENT_TYPE": ctype,
        "CONTENT_LENGTH": str(len(body)),
    }
    _, proc = _run_in_tmp(tmp_path, body, env, key_file_lines=["valid-key"])
    assert b"Status: 401" in proc.stdout, proc.stdout


def test_key_with_no_keep_flag_allows_1h(tmp_path):
    """Key tagged `no-keep` still accepts time-limited buckets."""
    body, ctype = _multipart("photo.jpg", b"\xff\xd8", time="1h")
    env = {
        **{k: v for k, v in _base_env().items() if k != "ASSET_API_KEY"},
        "HTTP_AUTHORIZATION": "Bearer pub",
        "CONTENT_TYPE": ctype,
        "CONTENT_LENGTH": str(len(body)),
    }
    _, proc = _run_in_tmp(tmp_path, body, env, key_file_lines=["pub no-keep"])
    assert b"Status: 200" in proc.stdout, proc.stdout


def test_key_with_no_keep_flag_blocks_keep_bucket(tmp_path):
    """Key tagged `no-keep` 403s on `keep`."""
    body, ctype = _multipart("photo.jpg", b"\xff\xd8", time="keep")
    env = {
        **{k: v for k, v in _base_env().items() if k != "ASSET_API_KEY"},
        "HTTP_AUTHORIZATION": "Bearer pub",
        "CONTENT_TYPE": ctype,
        "CONTENT_LENGTH": str(len(body)),
    }
    _, proc = _run_in_tmp(tmp_path, body, env, key_file_lines=["pub no-keep"])
    assert b"Status: 403" in proc.stdout, proc.stdout
    payload = _payload(proc)
    assert "key not authorized" in payload["error"]
    assert "keep" in payload["error"]


def test_key_with_explicit_buckets_flag_allows_listed(tmp_path):
    body, ctype = _multipart("photo.jpg", b"\xff\xd8", time="1h")
    env = {
        **{k: v for k, v in _base_env().items() if k != "ASSET_API_KEY"},
        "HTTP_AUTHORIZATION": "Bearer narrow",
        "CONTENT_TYPE": ctype,
        "CONTENT_LENGTH": str(len(body)),
    }
    _, proc = _run_in_tmp(tmp_path, body, env, key_file_lines=["narrow buckets:1h"])
    assert b"Status: 200" in proc.stdout, proc.stdout


def test_key_with_explicit_buckets_flag_rejects_others(tmp_path):
    body, ctype = _multipart("photo.jpg", b"\xff\xd8", time="1d")
    env = {
        **{k: v for k, v in _base_env().items() if k != "ASSET_API_KEY"},
        "HTTP_AUTHORIZATION": "Bearer narrow",
        "CONTENT_TYPE": ctype,
        "CONTENT_LENGTH": str(len(body)),
    }
    _, proc = _run_in_tmp(tmp_path, body, env, key_file_lines=["narrow buckets:1h"])
    assert b"Status: 403" in proc.stdout, proc.stdout


def test_rate_limit_blocks_after_threshold(tmp_path):
    """Pre-seed bucket dirs with N recent files; the next upload should 429.

    The limit sums across all buckets, so seeding `keep` (which the user
    can't trigger via cron expiry) still counts toward the cap."""
    body, ctype = _multipart("photo.jpg", b"\xff\xd8")
    env = {
        **_base_env(),
        "HTTP_AUTHORIZATION": "Bearer test-key",
        "CONTENT_TYPE": ctype,
        "CONTENT_LENGTH": str(len(body)),
        "ASSET_RATE_WINDOW_SEC": "60",
        "ASSET_RATE_MAX_ITEMS": "3",
    }
    docroot = tmp_path / "docroot"
    _seed_docroot(docroot)
    bucket_dir = docroot / "files" / "1h"
    bucket_dir.mkdir(parents=True)
    for stem in ("aaaa", "bbbb", "cccc"):
        (bucket_dir / f"{stem}.jpg").write_bytes(b"x")
    home = tmp_path / "home"
    home.mkdir()
    proc = subprocess.run(
        [PYTHON, str(docroot / "upload.cgi")],
        input=body,
        env={**os.environ, **env, "HOME": str(home)},
        capture_output=True,
        timeout=10,
        cwd=docroot,
    )
    assert b"Status: 429" in proc.stdout, proc.stdout
