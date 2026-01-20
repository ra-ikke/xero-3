"""Facade for map fetching + rendering.

This layer returns typed models (MapData) and centralizes "fetch then draw"
logic so callers don't need to know about the underlying services.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from domain.models import MapData
from service import api_service
from service.http_client import download_bytes

logger = logging.getLogger(__name__)


async def fetch_map(code: str) -> MapData | None:
    """Fetches and normalizes a map by code into MapData."""
    raw = await api_service.fetch_map(code)
    if not raw:
        return None
    return MapData.from_api_dict(code=code, data=raw)


async def draw_map_url(payload: Mapping[str, Any]) -> str | None:
    """Returns the mapdraw URL (legacy behavior)."""
    return await api_service.draw_map(dict(payload))


async def draw_map_png(payload: Mapping[str, Any]) -> bytes | None:
    """Returns the rendered map PNG bytes (downloads the mapdraw URL)."""
    url = await draw_map_url(payload)
    if not url or not isinstance(url, str) or not url.startswith("http"):
        if url:
            logger.warning("Mapdraw returned a non-http URL/text: %s", url)
        return None
    return await download_bytes(url, expected_status=200)
