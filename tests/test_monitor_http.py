# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webui import monitor
from webui import proxy_store


def test_control_defaults_are_single_account_batch():
    previous = monitor.CONTROL_FILE
    try:
        with tempfile.TemporaryDirectory() as tmp:
            monitor.CONTROL_FILE = Path(tmp) / "monitor_control.json"
            control = monitor.load_control()
            assert control["workers"] == 1
            assert control["batch_count"] == 1
            assert control["add_count"] == 1
            assert control["mode"] == "batch"

            control = monitor.save_control(
                {
                    "workers": "invalid",
                    "batch_count": "invalid",
                    "add_count": "invalid",
                    "mode": "invalid",
                }
            )
            assert control["workers"] == 1
            assert control["batch_count"] == 1
            assert control["add_count"] == 1
            assert control["mode"] == "batch"
    finally:
        monitor.CONTROL_FILE = previous


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

        status, _, body = request(base + "/api/proxies")
        assert status == 401

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


def test_proxy_api_auth_mutations_and_redaction():
    token = "test-proxy-token-123456"
    secret = "proxy-secret-value-99"
    previous_token = os.environ.get("MONITOR_TOKEN")
    previous_paths = (
        proxy_store.STATE_PATH,
        proxy_store.LOCK_PATH,
        proxy_store.LEGACY_PATH,
    )
    with tempfile.TemporaryDirectory() as temp:
        base_path = Path(temp)
        proxy_store.STATE_PATH = base_path / "log" / "proxy_pool.json"
        proxy_store.LOCK_PATH = base_path / "log" / "proxy_pool.json.lock"
        proxy_store.LEGACY_PATH = base_path / "proxies.txt"
        os.environ["MONITOR_TOKEN"] = token
        server = monitor.ThreadingHTTPServer(("127.0.0.1", 0), monitor.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            payload = json.dumps(
                {"proxies": f"proxy.example:8080:worker:{secret}"}
            ).encode("utf-8")
            status, _, _ = request(
                base + "/api/proxies/import",
                method="POST",
                body=payload,
            )
            assert status == 401

            status, _, body = request(
                base + "/api/proxies/import",
                token=token,
                method="POST",
                body=payload,
            )
            assert status == 200
            imported = json.loads(body)
            assert imported["imported_count"] == 1
            assert secret not in body.decode("utf-8")
            proxy_id = imported["items"][0]["id"]

            status, _, body = request(base + "/api/proxies", token=token)
            assert status == 200
            assert secret not in body.decode("utf-8")
            assert json.loads(body)["items"][0]["has_auth"] is True

            status, _, body = request(
                base + f"/api/proxies/{proxy_id}",
                token=token,
                method="PATCH",
                body=b'{"enabled":false}',
            )
            assert status == 200
            assert json.loads(body)["items"][0]["enabled"] is False

            status, _, _ = request(
                base + f"/api/proxies/{proxy_id}",
                method="DELETE",
            )
            assert status == 401
            status, _, body = request(
                base + f"/api/proxies/{proxy_id}",
                token=token,
                method="DELETE",
            )
            assert status == 200
            assert json.loads(body)["summary"]["total"] == 0
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            (
                proxy_store.STATE_PATH,
                proxy_store.LOCK_PATH,
                proxy_store.LEGACY_PATH,
            ) = previous_paths
            if previous_token is None:
                os.environ.pop("MONITOR_TOKEN", None)
            else:
                os.environ["MONITOR_TOKEN"] = previous_token

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
    test_control_defaults_are_single_account_batch()
    test_monitor_http_auth_and_headers()
    test_proxy_api_auth_mutations_and_redaction()
    test_non_loopback_requires_token()
    print("OK monitor http")
