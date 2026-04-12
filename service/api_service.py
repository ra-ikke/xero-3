"""Utilities to interact with external map services."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional
from urllib.parse import quote

import logging

from config import (
    CYPHER_GETMAP_URL,
    MAPDRAW_STATUS_URL,
    MAPDRAW_URL,
    STATUS_URL,
    WEBHOOK_URL,
)
from service.http_client import get_json, get_text, post_json, post_text

logger = logging.getLogger(__name__)


async def fetch_map_data_from_cypher801(map_code: str) -> Optional[Dict[str, Any]]:
    """Performs a GET request against the Cypher801 API and normalizes the response."""
    if not map_code:
        logger.error("fetch_map_data_from_cypher801 called without a valid map_code.")
        return None

    url = f"{CYPHER_GETMAP_URL}{quote(map_code)}"
    logger.debug("GET %s", url)

    data = await get_json(url, expected_status=200)
    if data is None:
        logger.error("Failed to query Cypher801 for %s", map_code)
        return None

    if not data or not data.get('success'):
        logger.warning("Cypher response was not successful or missing data: %s", data)
        return None

    game = data.get('game') or {}
    xml = game.get('xml')
    if not xml:
        logger.warning("Cypher response is missing a valid XML payload: %s", game)
        return None

    normalized_xml = xml.replace("'", '"')
    game_type = f'P{game.get("type", "")}'
    maker = game.get('maker', '')
    if '#' not in maker:
        maker = f'{maker}#0000'

    return {
        'xml': normalized_xml,
        'type': game_type,
        'maker': maker,
    }


async def draw_map(payload: Dict[str, Any]) -> Optional[str]:
    """Sends a payload to the Mapdraw service and returns the response text (usually an image URL)."""
    if not payload:
        logger.error("draw_map called without a payload.")
        return None

    xml = payload.get("xml")
    if isinstance(xml, str):
        length_match = re.search(r'<P .*L="(-?\d*\.?\d*)".*/><Z>', xml)
        if length_match:
            try:
                length_value = int(float(length_match.group(1)))
            except Exception:
                length_value = None
            if length_value is not None and length_value < 800:
                xml = re.sub(r'(<P .*L=")(-?\d*\.?\d*)(".*\/><Z>)', r"\g<1>800\g<3>", xml, count=1)
                payload["xml"] = xml
    logger.debug("POST %s payload=%s", MAPDRAW_URL, payload)
    text = await post_text(MAPDRAW_URL, json_payload=payload, expected_status=200)
    if text is None:
        logger.error("Failed to send payload to Mapdraw")
        return None
    return text


async def fetch_map_data_from_webhook(map_code: str) -> Optional[Dict[str, Any]]:
    """Queries the local webhook to obtain map XML and metadata."""
    if not map_code:
        logger.error("fetch_map_data_from_webhook called without a valid map_code.")
        return None

    payload = {
        'type': 'loadmap',
        'content': {'map': map_code},
    }

    logger.debug("POST %s payload=%s", WEBHOOK_URL, payload)
    data = await post_json(WEBHOOK_URL, json_payload=payload, expected_status=200)
    if data is None:
        logger.exception("Failed to query webhook for %s", map_code)
        return None

    if data.get('status') != 'received':
        logger.warning("Webhook returned an unexpected status: %s", data)
        return None

    content = data.get('content') or {}
    return {
        'xml': content.get('xml', ''),
        'type': content.get('category', ''),
        'maker': content.get('author', ''),
    }


async def fetch_map(map_code: str) -> Optional[Dict[str, Any]]:
    """Tries to fetch the map via webhook first, then falls back to Cypher801."""
    if not map_code:
        logger.error("fetch_map called without a valid map_code.")
        return None

    logger.debug("Trying webhook for %s", map_code)
    webhook_result = await fetch_map_data_from_webhook(map_code)
    if webhook_result:
        return webhook_result

    logger.debug("Webhook failed, trying Cypher801 for %s", map_code)
    return await fetch_map_data_from_cypher801(map_code)


async def _fetch_status(url: str) -> Optional[Dict[str, Any]]:
    """Fetches a status JSON payload from the given endpoint."""
    body = await get_text(url, expected_status=200)
    if body is None:
        logger.exception("Failed to fetch status from %s", url)
        return None

    try:
        data = json.loads(body)
    except ValueError:
        logger.error("Status %s returned invalid JSON: %s", url, body)
        return None

    if data.get('status') == 'error':
        logger.error("Status %s returned an error: %s", url, data)
        return None

    return data


async def fetch_game_bot_status() -> Optional[Dict[str, Any]]:
    """Returns the status payload for the game bot service."""
    return await _fetch_status(STATUS_URL)


async def fetch_mapdraw_status() -> Optional[Dict[str, Any]]:
    """Returns the status payload for the Mapdraw service."""
    return await _fetch_status(MAPDRAW_STATUS_URL)
