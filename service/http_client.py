"""Shared aiohttp client utilities.

This module provides a single reusable aiohttp.ClientSession to avoid creating
one session per request across the bot. It exposes small helpers for common
HTTP patterns (JSON/text/bytes) with consistent timeouts and logging.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable, Mapping, MutableMapping, Optional

import aiohttp

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=15)

_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


async def get_session() -> aiohttp.ClientSession:
    """Returns a shared aiohttp session (created lazily)."""
    global _session
    if _session is not None and not _session.closed:
        return _session

    async with _session_lock:
        if _session is not None and not _session.closed:
            return _session
        _session = aiohttp.ClientSession(timeout=_DEFAULT_TIMEOUT)
        return _session


async def close_session() -> None:
    """Closes the shared aiohttp session if it exists."""
    global _session
    if _session is not None and not _session.closed:
        try:
            await _session.close()
        finally:
            _session = None
    else:
        _session = None


def _expected_statuses(value: int | Iterable[int] | None) -> set[int]:
    if value is None:
        return {200}
    if isinstance(value, int):
        return {value}
    return set(value)


async def get_json(
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    params: Optional[Mapping[str, str]] = None,
    expected_status: int | Iterable[int] | None = None,
) -> Any | None:
    session = await get_session()
    expected = _expected_statuses(expected_status)

    try:
        async with session.get(url, headers=headers, params=params) as response:
            if response.status not in expected:
                body_text = await response.text()
                logger.error("GET %s returned %s: %s", url, response.status, body_text)
                return None
            return await response.json()
    except Exception:
        logger.exception("GET %s failed", url)
        return None


async def get_text(
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    params: Optional[Mapping[str, str]] = None,
    expected_status: int | Iterable[int] | None = None,
) -> str | None:
    session = await get_session()
    expected = _expected_statuses(expected_status)

    try:
        async with session.get(url, headers=headers, params=params) as response:
            text = await response.text()
            if response.status not in expected:
                logger.error("GET %s returned %s: %s", url, response.status, text)
                return None
            return text
    except Exception:
        logger.exception("GET %s failed", url)
        return None


async def post_json(
    url: str,
    *,
    json_payload: Any,
    headers: Optional[MutableMapping[str, str]] = None,
    expected_status: int | Iterable[int] | None = None,
) -> Any | None:
    session = await get_session()
    expected = _expected_statuses(expected_status)

    request_headers: dict[str, str] = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)

    try:
        async with session.post(url, json=json_payload, headers=request_headers) as response:
            if response.status not in expected:
                body_text = await response.text()
                logger.error("POST %s returned %s: %s", url, response.status, body_text)
                return None
            return await response.json()
    except Exception:
        logger.exception("POST %s failed", url)
        return None


async def post_text(
    url: str,
    *,
    json_payload: Any,
    headers: Optional[MutableMapping[str, str]] = None,
    expected_status: int | Iterable[int] | None = None,
) -> str | None:
    session = await get_session()
    expected = _expected_statuses(expected_status)

    request_headers: dict[str, str] = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)

    try:
        async with session.post(url, json=json_payload, headers=request_headers) as response:
            text = await response.text()
            if response.status not in expected:
                logger.error("POST %s returned %s: %s", url, response.status, text)
                return None
            return text
    except Exception:
        logger.exception("POST %s failed", url)
        return None


async def download_bytes(
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    expected_status: int | Iterable[int] | None = None,
) -> bytes | None:
    session = await get_session()
    expected = _expected_statuses(expected_status)

    try:
        async with session.get(url, headers=headers) as response:
            if response.status not in expected:
                body_text = await response.text()
                logger.error("GET %s returned %s: %s", url, response.status, body_text)
                return None
            return await response.read()
    except Exception:
        logger.exception("Download %s failed", url)
        return None
