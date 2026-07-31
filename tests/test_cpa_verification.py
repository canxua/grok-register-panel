# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import grok_register_ttk as panel
import sso_to_auth_json as cpa


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)
        self.reason = "fake"

    def json(self):
        return self._payload


def test_semantic_payload_validation_rejects_generic_ok():
    assert cpa.is_responses_success_payload({"ok": True}) is False
    assert cpa.is_chat_completion_success_payload({"success": True}) is False
    assert cpa.is_responses_success_payload(
        {"id": "resp_1", "status": "completed", "output": []}
    ) is True
    assert cpa.is_responses_success_payload(
        {
            "id": "resp_2",
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [{"type": "reasoning"}],
        }
    ) is False
    assert cpa.is_chat_completion_success_payload(
        {"id": "chatcmpl_1", "choices": [{"index": 0}]}
    ) is True


def test_management_upload_and_hotload_contract():
    record = {"email": "person@example.com", "access_token": "token"}
    expected_size = len(cpa.cpa_auth_payload(record))

    class FakeRequests:
        post_kwargs = None
        get_kwargs = None

        @classmethod
        def post(cls, _url, **kwargs):
            cls.post_kwargs = kwargs
            return FakeResponse(200, {"status": "ok"})

        @classmethod
        def get(cls, _url, **kwargs):
            cls.get_kwargs = kwargs
            return FakeResponse(
                200,
                {
                    "files": [
                        {
                            "name": cpa.cpa_auth_filename(record),
                            "size": expected_size,
                            "source": "file",
                            "disabled": False,
                            "unavailable": False,
                        }
                    ]
                },
            )

    original = cpa.requests
    cpa.requests = FakeRequests
    try:
        name = cpa.upload_cpa_auth_remote(
            "http://127.0.0.1:8317/v0/management",
            "management-key",
            record,
            proxy="http://proxy.example:8080",
        )
        loaded = cpa.wait_cpa_auth_remote(
            "http://127.0.0.1:8317/v0/management",
            "management-key",
            name,
            expected_size=expected_size,
            timeout=1,
        )
    finally:
        cpa.requests = original

    assert loaded["ok"] is True
    assert FakeRequests.post_kwargs["proxies"] == {}
    assert FakeRequests.get_kwargs["proxies"] == {}


def test_provider_probe_rejects_generic_http_200():
    class FakeRequests:
        @staticmethod
        def post(_url, **_kwargs):
            return FakeResponse(200, {"ok": True})

    original = cpa.requests
    cpa.requests = FakeRequests
    try:
        result = cpa.probe_cpa_record_verified(
            {"access_token": "token", "headers": {}},
            attempts=1,
        )
    finally:
        cpa.requests = original

    assert result["ok"] is False
    assert result["failure_kind"] == "invalid_response"


def test_provider_probe_uses_completion_sized_token_budget():
    class FakeRequests:
        post_kwargs = None

        @classmethod
        def post(cls, _url, **kwargs):
            cls.post_kwargs = kwargs
            return FakeResponse(
                200,
                {"id": "resp_1", "status": "completed", "output": []},
            )

    original = cpa.requests
    cpa.requests = FakeRequests
    try:
        result = cpa.probe_cpa_record_verified(
            {"access_token": "token", "headers": {}},
            attempts=1,
        )
    finally:
        cpa.requests = original

    assert result["ok"] is True
    assert (
        FakeRequests.post_kwargs["json"]["max_output_tokens"]
        == cpa.CPA_PROBE_MAX_OUTPUT_TOKENS
        >= 16
    )
    assert FakeRequests.post_kwargs["json"]["stream"] is False


def test_data_plane_requires_chat_completion_shape():
    responses = [
        FakeResponse(200, {"success": True}),
        FakeResponse(200, {"id": "chatcmpl_1", "choices": [{"index": 0}]}),
    ]

    class FakeRequests:
        @staticmethod
        def post(_url, **_kwargs):
            return responses.pop(0)

    original = cpa.requests
    cpa.requests = FakeRequests
    try:
        rejected = cpa.probe_openai_data_plane(
            "https://api.example.test", "api-key", "model", attempts=1
        )
        accepted = cpa.probe_openai_data_plane(
            "https://api.example.test", "api-key", "model", attempts=1
        )
    finally:
        cpa.requests = original

    assert rejected["ok"] is False
    assert accepted["ok"] is True


def test_panel_state_machine_reaches_verified_only_after_all_gates():
    original_config = panel.config
    original_env = {name: os.environ.get(name) for name in ("CPA_AUTO_VERIFY",)}
    originals = {
        name: getattr(panel._s2cpa, name)
        for name in (
            "sso_to_token",
            "token_to_cpa_record",
            "decode_jwt_payload",
            "write_cpa_auth",
            "write_grok2api_auth",
            "probe_cpa_record_verified",
            "upload_cpa_auth_remote",
            "wait_cpa_auth_remote",
            "probe_openai_data_plane",
        )
    }
    original_record_state = panel.record_cpa_state
    original_pending = panel._append_sso_pending
    states = []

    with tempfile.TemporaryDirectory() as temp:
        try:
            os.environ.pop("CPA_AUTO_VERIFY", None)
            panel.config = {
                **panel.DEFAULT_CONFIG,
                "cpa_auto_add": True,
                "cpa_auto_verify": True,
                "cpa_auth_dir": temp,
                "cpa_remote_url": "http://127.0.0.1:8317",
                "cpa_management_key": "management-key",
                "cpa_data_plane_url": "https://api.example.test",
                "cpa_data_plane_key": "api-key",
                "cpa_data_plane_model": "model",
                "grok2api_auth_dir": "",
            }
            panel.record_cpa_state = lambda _email, state, **_kwargs: states.append(state)
            panel._append_sso_pending = lambda *_args, **_kwargs: None
            panel._s2cpa.sso_to_token = lambda *_args, **_kwargs: {"access_token": "token"}
            panel._s2cpa.token_to_cpa_record = lambda *_args, **_kwargs: {
                "email": "person@example.com",
                "access_token": "token",
                "headers": {},
            }
            panel._s2cpa.decode_jwt_payload = lambda *_args, **_kwargs: {}
            panel._s2cpa.write_cpa_auth = lambda path, _record: path / "xai-person.json"
            panel._s2cpa.write_grok2api_auth = lambda path, _token, email="": path / "g2a.json"
            panel._s2cpa.probe_cpa_record_verified = lambda *_args, **_kwargs: {
                "ok": True,
                "status_code": 200,
                "summary": "response",
                "attempts": 1,
            }
            panel._s2cpa.upload_cpa_auth_remote = lambda *_args, **_kwargs: "xai-person.json"
            panel._s2cpa.wait_cpa_auth_remote = lambda *_args, **_kwargs: {
                "ok": True,
                "status_code": 200,
            }
            panel._s2cpa.probe_openai_data_plane = lambda *_args, **_kwargs: {
                "ok": True,
                "status_code": 200,
                "attempts": 1,
            }

            result = panel.add_sso_to_cpa("s" * 80, email="person@example.com")
        finally:
            panel.config = original_config
            panel.record_cpa_state = original_record_state
            panel._append_sso_pending = original_pending
            for name, value in originals.items():
                setattr(panel._s2cpa, name, value)
            for name, value in original_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    assert result.verified is True
    assert states == [
        "token_written",
        "provider_verified",
        "pool_uploaded",
        "pool_loaded",
        "data_plane_verifying",
        "verified",
    ]


if __name__ == "__main__":
    test_semantic_payload_validation_rejects_generic_ok()
    test_management_upload_and_hotload_contract()
    test_provider_probe_rejects_generic_http_200()
    test_provider_probe_uses_completion_sized_token_budget()
    test_data_plane_requires_chat_completion_shape()
    test_panel_state_machine_reaches_verified_only_after_all_gates()
    print("OK CPA verification")
