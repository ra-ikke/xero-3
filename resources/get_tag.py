"""Tag resolution helper (ported from legacy JS resources/getTag.js)."""

from __future__ import annotations

from typing import Optional

from resources.tags_list import TAGS_BY_GROUP


CATEGORY_TO_GROUP: dict[str, str] = {
    "P4": "rotation",
    "P5": "rotation",
    "P6": "rotation",
    "P7": "rotation",
    "P8": "rotation",
    "P9": "rotation",
    "P12": "rotation",
    "P10": "survivor",
    "P11": "survivor",
    "P24": "survivor",
    "P17": "racing",
    "P27": "racing",
    "P37": "racing",
    "P18": "defilante",
    "P3": "bootcamp",
    "P13": "bootcamp",
}

# Sentinel for /create_discussion and POST /discussion (poll decides P17/P27/P37).
RACING_DISCUSSION_SENTINEL = "RACING"
RACING_DISCUSSION_CATEGORY_CODE = "P17"
RACING_DISCUSSION_CODES: frozenset[str] = frozenset({"P17", "P27", "P37"})


def resolve_discussion_category_code(category_code: str) -> Optional[str]:
    """Normalizes a discussion category code, mapping Racing to the umbrella P17 thread."""
    code = (category_code or "").strip().upper()
    if not code:
        return None
    if code == RACING_DISCUSSION_SENTINEL:
        return RACING_DISCUSSION_CATEGORY_CODE
    if code in CATEGORY_TO_GROUP:
        return code
    return None


def category_codes_for_group(group: str) -> list[str]:
    """Returns discussion category codes that belong to the given forum group."""
    return sorted(
        [code for code, mapped in CATEGORY_TO_GROUP.items() if mapped == group],
        key=lambda code: int(code[1:]) if len(code) > 1 and code[1:].isdigit() else 0,
    )


def get_tag_ids(
    category: dict,
    disc_type: str,
    disc_status: str,
) -> Optional[list[str]]:
    """
    Returns a list of Discord forum tag IDs based on category + status + type.

    Expected inputs:
    - category: an entry from resources/category_list.py (must contain name/description)
    - disc_type: e.g. "perm", "deperm", "edit", "other"
    - disc_status: e.g. "open", "closed"
    """
    category_code = (category.get("name") or "").strip()
    category_description = (category.get("description") or "").strip()
    group_name = CATEGORY_TO_GROUP.get(category_code)
    if not group_name:
        return None

    tags = TAGS_BY_GROUP.get(group_name, {})
    if not tags:
        return None

    resolved_ids: list[str] = []

    # Category tag: exact name match (legacy behavior)
    category_tag = tags.get(category_description.lower())
    if category_tag:
        resolved_ids.append(category_tag["id"])

    # Status tag: Open/Closed
    status_tag = tags.get(disc_status.lower())
    if status_tag:
        resolved_ids.append(status_tag["id"])

    # Discussion type tag: Perm/Deperm/Edit/Other (fallback to Other)
    disc_type_tag = tags.get(disc_type.lower()) or tags.get("other")
    if disc_type_tag:
        resolved_ids.append(disc_type_tag["id"])

    return resolved_ids or None

