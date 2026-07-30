# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from proxy_pool import ProxyPoolError, load_proxy_urls
import connectivity


def test_absolute_proxy_file_is_loaded_and_deduplicated():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        pool_file = root / "shared-pool.txt"
        pool_file.write_text(
            "# pool\n"
            "http://USER:PASS@HOST:3129\n"
            "http://USER:PASS@HOST:3129\n"
            "socks5://HOST:1080\n",
            encoding="utf-8",
        )
        pool_file.chmod(0o600)
        result = load_proxy_urls(
            {"proxy_file": str(pool_file), "proxy": "http://FALLBACK:8080"},
            root,
        )
        assert result == ["http://USER:PASS@HOST:3129", "socks5://HOST:1080"]


def test_configured_proxy_file_errors_do_not_fall_back_silently():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        missing = root / "missing.txt"
        try:
            load_proxy_urls(
                {"proxy_file": str(missing), "proxy": "http://FALLBACK:8080"},
                root,
            )
        except ProxyPoolError as exc:
            assert "not readable" in str(exc)
        else:
            raise AssertionError("missing configured proxy_file must fail")


def test_relative_proxy_file_cannot_escape_project_root():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "project"
        root.mkdir()
        try:
            load_proxy_urls({"proxy_file": "../outside.txt"}, root)
        except ProxyPoolError as exc:
            assert "inside the project root" in str(exc)
        else:
            raise AssertionError("relative traversal must fail")


def test_proxy_file_must_not_be_group_or_world_writable():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        pool_file = root / "pool.txt"
        pool_file.write_text("http://HOST:3129\n", encoding="utf-8")
        pool_file.chmod(0o666)
        try:
            load_proxy_urls({"proxy_file": str(pool_file)}, root)
        except ProxyPoolError as exc:
            assert "must not be group/world writable" in str(exc)
        else:
            raise AssertionError("writable proxy_file must fail")


def test_connectivity_uses_first_proxy_from_configured_pool():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        pool_file = root / "pool.txt"
        pool_file.write_text(
            "http://FIRST:3129\nhttp://SECOND:3129\n",
            encoding="utf-8",
        )
        pool_file.chmod(0o600)
        seen = []
        original_proxy = connectivity.check_proxy
        original_signup = connectivity.check_xai_signup
        original_email = connectivity.check_email_api
        original_cpa = connectivity.check_cpa
        try:
            connectivity.check_proxy = lambda proxy, _get: (
                seen.append(("proxy", proxy)) or ("代理", True, "OK")
            )
            connectivity.check_xai_signup = lambda proxy, _get: (
                seen.append(("signup", proxy))
                or (connectivity.XAI_SIGNUP_CHECK_NAME, True, "OK")
            )
            connectivity.check_email_api = lambda *_args: ("邮箱API", True, "OK")
            connectivity.check_cpa = lambda *_args: ("CPA", True, "OK")
            results = connectivity.run_connectivity_checks(
                {"proxy_file": str(pool_file)},
                lambda *_args, **_kwargs: None,
                lambda *_args, **_kwargs: None,
            )
        finally:
            connectivity.check_proxy = original_proxy
            connectivity.check_xai_signup = original_signup
            connectivity.check_email_api = original_email
            connectivity.check_cpa = original_cpa

        assert seen == [
            ("proxy", "http://FIRST:3129"),
            ("signup", "http://FIRST:3129"),
        ]
        assert "代理池 2 条" in results[0][2]


if __name__ == "__main__":
    test_absolute_proxy_file_is_loaded_and_deduplicated()
    test_configured_proxy_file_errors_do_not_fall_back_silently()
    test_relative_proxy_file_cannot_escape_project_root()
    test_proxy_file_must_not_be_group_or_world_writable()
    test_connectivity_uses_first_proxy_from_configured_pool()
    print("OK proxy pool")
