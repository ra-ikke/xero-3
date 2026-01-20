"""Poll option mapping (ported from legacy JS resources)."""

from __future__ import annotations

from typing import Optional

OPTIONS_MAPPING: dict[str, dict[str, list[dict[str, str]]]] = {
    "rotation": {
        "PERM": [
            {"option": "Perm", "type": "PERM"},
            {"option": "Perm with edits", "type": "PERM"},
            {"option": "Low-Perm (P1)", "type": "REJECT"},
            {"option": "Reject (P22)", "type": "REJECT"},
        ],
        "DEPERM": [
            {"option": "Keep as is", "type": "KEEP"},
            {"option": "Keep with edits", "type": "KEEP"},
            {"option": "Deperm", "type": "DEPERM"},
        ],
        "EDIT": [
            {"option": "Keep as is", "type": "KEEP"},
            {"option": "Keep with edits", "type": "KEEP"},
            {"option": "Deperm", "type": "DEPERM"},
        ],
    },
    "defilante": {
        "PERM": [
            {"option": "Perm", "type": "PERM"},
            {"option": "Perm with edits", "type": "PERM"},
            {"option": "Reject", "type": "REJECT"},
        ],
        "DEPERM": [
            {"option": "Keep as is", "type": "KEEP"},
            {"option": "Keep with edits", "type": "KEEP"},
            {"option": "Deperm", "type": "DEPERM"},
        ],
        "EDIT": [
            {"option": "Keep as is", "type": "KEEP"},
            {"option": "Keep with edits", "type": "KEEP"},
            {"option": "Deperm", "type": "DEPERM"},
        ],
    },
    "survivor": {
        "PERM": [
            {"option": "Perm", "type": "PERM"},
            {"option": "Perm with edits", "type": "PERM"},
            {"option": "Reject", "type": "REJECT"},
        ],
        "DEPERM": [
            {"option": "Keep as is", "type": "KEEP"},
            {"option": "Keep with edits", "type": "KEEP"},
            {"option": "Deperm", "type": "DEPERM"},
        ],
        "EDIT": [
            {"option": "Keep as is", "type": "KEEP"},
            {"option": "Keep with edits", "type": "KEEP"},
            {"option": "Deperm", "type": "DEPERM"},
        ],
    },
    "bootcamp": {
        "PERM": [
            {"option": "Perm as P3", "type": "PERM"},
            {"option": "Perm as P13", "type": "PERM"},
            {"option": "Perm as P3 with edits", "type": "PERM"},
            {"option": "Perm as P13 with edits", "type": "PERM"},
            {"option": "Reject", "type": "REJECT"},
        ],
        "DEPERM": [
            {"option": "Keep as is", "type": "KEEP"},
            {"option": "Keep with edits", "type": "KEEP"},
            {"option": "Deperm", "type": "DEPERM"},
        ],
        "EDIT": [
            {"option": "Keep as is", "type": "KEEP"},
            {"option": "Keep with edits", "type": "KEEP"},
            {"option": "Deperm", "type": "DEPERM"},
        ],
    },
}

CATEGORY_MAPPING: dict[str, str] = {
    "P4": "rotation",
    "P5": "rotation",
    "P6": "rotation",
    "P7": "rotation",
    "P8": "rotation",
    "P9": "rotation",
    "P12": "rotation",
    "P17": "rotation",  # P17 (racing) uses the same options as rotation in the legacy mapping
    "P18": "defilante",
    "P10": "survivor",
    "P11": "survivor",
    "P24": "survivor",
    "P3": "bootcamp",
}


def get_poll_options(category_code: str, disc_type: str) -> Optional[list[dict[str, str]]]:
    """Returns the poll options for the given category and discussion type."""
    mapped_category = CATEGORY_MAPPING.get(category_code)
    if not mapped_category:
        return None
    return OPTIONS_MAPPING.get(mapped_category, {}).get(disc_type) or None

