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
    "P18": "defilante",
    "P3": "bootcamp",
    "P13": "bootcamp",
}


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

