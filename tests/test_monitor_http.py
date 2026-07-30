# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webui import monitor


def request(url: str, *, token: str = "", method: str = "GET", body: bytes | None = None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        response = urllib.request.urlopen(req, timeout=5)
        return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_monitor_http_auth_and_headers():
    token = "test-monitor-token-123456"
    previous = os.environ.get("MONITOR_TOKEN")
    os.environ["MONITOR_TOKEN"] = token
    server = monitor.ThreadingHTTPServer(("127.0.0.1", 0), monitor.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        status, headers, _ = request(base + "/api/health")
        assert status == 200
        assert headers.get("X-Frame-Options") == "DENY"
        assert "frame-ancestors 'none'" in headers.get("Content-Security-Policy", "")

        status, _, body = request(base + "/api/status")
        assert status == 401
        assert json.loads(body)["ok"] is False

        status, _, body = request(base + "/api/status", token=token)
        assert status == 200
        assert "process" in json.loads(body)

        status, _, _ = request(base + "/api/recovery")
        assert status == 401
        status, _, body = request(base + "/api/recovery", token=token)
        assert status == 200
        assert "pending_count" in json.loads(body)

        status, _, _ = request(
            base + "/api/control",
            method="POST",
            body=b"not-json",
        )
        assert status == 401

        status, _, _ = request(
            base + "/api/control",
            token=token,
            method="POST",
            body=b"not-json",
        )
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if previous is None:
            os.environ.pop("MONITOR_TOKEN", None)
        else:
            os.environ["MONITOR_TOKEN"] = previous


def test_non_loopback_requires_token():
    env = dict(os.environ)
    env.pop("MONITOR_TOKEN", None)
    env["MONITOR_HOST"] = "192.0.2.10"
    env["MONITOR_PORT"] = "0"
    result = subprocess.run(
        [sys.executable, "-m", "webui.monitor"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "MONITOR_TOKEN is required" in (result.stdout + result.stderr)


if __name__ == "__main__":
    test_monitor_http_auth_and_headers()
    test_non_loopback_requires_token()
    print("OK monitor http")
