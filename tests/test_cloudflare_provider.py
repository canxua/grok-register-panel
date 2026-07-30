# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from email_providers import cloudflare


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200
        self.text = str(payload)

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


def test_admin_create_keeps_exact_domain_by_default():
    calls = []

    def http_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse({"address": "alice@relay742.de5.net", "jwt": "mail-jwt"})

    result = cloudflare.create_temp_address(
        http_post,
        "https://mail.example",
        accounts_path="/admin/new_address",
        domain="relay742.de5.net",
        api_key="admin-secret",
        auth_mode="x-admin-auth",
        name="alice",
    )

    assert result == ("alice@relay742.de5.net", "mail-jwt")
    url, kwargs = calls[0]
    assert url == "https://mail.example/admin/new_address"
    assert kwargs["headers"]["x-admin-auth"] == "admin-secret"
    assert kwargs["json"] == {
        "name": "alice",
        "enablePrefix": False,
        "domain": "relay742.de5.net",
    }


def test_subdomain_randomization_is_opt_in():
    calls = []

    def http_post(url, **kwargs):
        calls.append(kwargs["json"])
        return FakeResponse({"address": "alice@example", "jwt": "mail-jwt"})

    cloudflare.create_temp_address(
        http_post,
        "https://mail.example",
        accounts_path="/admin/new_address",
        domain="relay742.de5.net",
        randomize_subdomain=True,
    )

    generated = calls[0]["domain"]
    assert generated != "relay742.de5.net"
    assert generated.endswith(".de5.net")


def test_message_list_and_detail_protocol():
    calls = []

    def http_get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/api/mails"):
            return FakeResponse({"results": [{"id": "message-id"}]})
        if url.endswith("/api/mail/message-id"):
            return FakeResponse({"data": {"id": "message-id", "subject": "xAI"}})
        raise AssertionError(url)

    messages = cloudflare.get_messages(
        http_get,
        "https://mail.example",
        "mail-jwt",
        messages_path="/api/mails",
    )
    detail = cloudflare.get_message_detail(
        http_get,
        "https://mail.example",
        "mail-jwt",
        "message-id",
        messages_path="/api/mails",
    )

    assert messages == [{"id": "message-id"}]
    assert detail == {"id": "message-id", "subject": "xAI"}
    assert calls[0][1]["headers"]["Authorization"] == "Bearer mail-jwt"
    assert calls[0][1]["params"] == {"limit": 20, "offset": 0}
    assert calls[1][0] == "https://mail.example/api/mail/message-id"


if __name__ == "__main__":
    test_admin_create_keeps_exact_domain_by_default()
    test_subdomain_randomization_is_opt_in()
    test_message_list_and_detail_protocol()
    print("OK cloudflare provider")
