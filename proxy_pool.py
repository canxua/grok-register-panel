"""Validated proxy-pool loading shared by runtime and connectivity checks."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from urllib.parse import urlsplit


ALLOWED_PROXY_SCHEMES = {"http", "socks5", "socks5h"}
MAX_PROXY_FILE_BYTES = 8 * 1024 * 1024
MAX_PROXY_ENTRIES = 50_000


class ProxyPoolError(ValueError):
    pass


def _resolve_proxy_file(raw_path: str, app_dir: str | Path) -> Path:
    root = Path(app_dir).expanduser().resolve()
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path

    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ProxyPoolError("relative proxy_file must stay inside the project root") from exc
    return resolved


def _validate_proxy_url(value: str, line_number: int | None = None) -> str:
    context = f" on line {line_number}" if line_number is not None else ""
    if any(char.isspace() for char in value):
        raise ProxyPoolError(f"proxy URL contains whitespace{context}")

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ProxyPoolError(f"invalid proxy URL{context}: {exc}") from exc

    if parsed.scheme.lower() not in ALLOWED_PROXY_SCHEMES:
        raise ProxyPoolError(f"unsupported proxy scheme{context}")
    if not parsed.hostname or port is None or not (1 <= port <= 65535):
        raise ProxyPoolError(f"proxy URL must include a valid host and port{context}")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ProxyPoolError(f"proxy URL must not include a path, query, or fragment{context}")
    return value


def _read_proxy_file(path: Path) -> list[str]:
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise ProxyPoolError(f"proxy_file is not readable: {path}") from exc
    try:
        if not stat.S_ISREG(metadata.st_mode):
            raise ProxyPoolError(f"proxy_file is not a regular file: {path}")
        if metadata.st_mode & 0o022:
            raise ProxyPoolError(f"proxy_file must not be group/world writable: {path}")
        if metadata.st_size > MAX_PROXY_FILE_BYTES:
            raise ProxyPoolError(
                f"proxy_file exceeds {MAX_PROXY_FILE_BYTES // (1024 * 1024)} MiB: {path}"
            )
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            lines = handle.read().splitlines()
    except (OSError, UnicodeError) as exc:
        raise ProxyPoolError(f"proxy_file must be readable UTF-8 text: {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    pool: list[str] = []
    seen: set[str] = set()
    for line_number, line in enumerate(lines, 1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        value = _validate_proxy_url(value, line_number)
        if value in seen:
            continue
        seen.add(value)
        pool.append(value)
        if len(pool) > MAX_PROXY_ENTRIES:
            raise ProxyPoolError(f"proxy_file exceeds {MAX_PROXY_ENTRIES} entries: {path}")

    if not pool:
        raise ProxyPoolError(f"proxy_file contains no usable proxy URLs: {path}")
    return pool


def load_proxy_urls(
    config: dict,
    app_dir: str | Path,
    explicit_path: str = "",
) -> list[str]:
    """Load a configured pool, the conventional proxies.txt, or config.proxy."""
    configured_path = str(explicit_path or config.get("proxy_file", "") or "").strip()
    if configured_path:
        return _read_proxy_file(_resolve_proxy_file(configured_path, app_dir))

    default_path = Path(app_dir).resolve() / "proxies.txt"
    if default_path.is_file():
        return _read_proxy_file(default_path)

    single = str(config.get("proxy", "") or "").strip()
    return [_validate_proxy_url(single)] if single else []
