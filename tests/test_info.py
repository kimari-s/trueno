"""Smoke test for info.cgi.

The info endpoint exists so the LP / Swagger UI can fetch the live limits
without a redeploy. The test pins the JSON shape and a couple of values.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

INFO_CGI = Path(__file__).resolve().parent.parent / "docroot" / "info.cgi"
PYTHON = sys.executable


def _run(env: dict[str, str]) -> tuple[int, dict]:
    proc = subprocess.run(
        [PYTHON, str(INFO_CGI)],
        env={**os.environ, **env},
        capture_output=True,
        timeout=10,
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    status_match = re.search(r"^Status: (\d+)", out, re.MULTILINE)
    status = int(status_match.group(1)) if status_match else 0
    body_text = out.split("\r\n\r\n", 1)[-1]
    return status, json.loads(body_text)


def test_get_returns_runtime_info():
    status, payload = _run({
        "REQUEST_METHOD": "GET",
        "ASSET_PUBLIC_URL": "https://example.org",
    })
    assert status == 200
    assert payload["public_url"] == "https://example.org"
    assert isinstance(payload["web_ui_url"], str)
    assert payload["web_ui_url"].startswith("http")
    assert payload["max_bytes"] >= 1024 * 1024
    assert ".jpg" in payload["allowed_exts"]
    assert payload["allowed_exts"] == sorted(payload["allowed_exts"])
    assert payload["rate_window_sec"] > 0
    assert payload["rate_max_items"] > 0
    # TTL buckets: 1h/1d/1w have integer seconds, keep is null.
    assert payload["ttl_choices"] == {
        "1h": 3600,
        "1d": 86400,
        "1w": 604800,
        "keep": None,
    }
    assert payload["default_time"] in payload["ttl_choices"]
    # Per-bucket ID length: short-lived = 4, keep = 6 (URL length = bucket signal).
    assert payload["id_len_by_bucket"] == {
        "1h":   4,
        "1d":   4,
        "1w":   4,
        "keep": 6,
    }


def test_rejects_post():
    status, payload = _run({"REQUEST_METHOD": "POST"})
    assert status == 405
    assert payload["error"] == "GET only"


def test_rate_limit_env_passthrough():
    _, payload = _run({
        "REQUEST_METHOD": "GET",
        "ASSET_RATE_WINDOW_SEC": "120",
        "ASSET_RATE_MAX_ITEMS": "7",
    })
    assert payload["rate_window_sec"] == 120
    assert payload["rate_max_items"] == 7
