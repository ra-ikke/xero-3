"""Runtime overrides for category metadata (persisted to JSON).

`category_list.py` is a static source of truth, but a few fields can be tuned by
mapcrews from the session-manager panel (map limit and public criteria). To keep
the "no DB, single process" approach, we store overrides in a JSON file and apply
them by mutating the in-memory `CATEGORY_LIST` dicts at import time, so every
consumer (`next((c for c in CATEGORY_LIST ...))`) transparently sees new values.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
OVERRIDES_PATH = os.path.join(_DIR, "category_overrides.json")

# Only these fields may be overridden from the UI.
ALLOWED_KEYS: frozenset[str] = frozenset({"submissionlimit", "submissionRules", "description"})


def load_overrides() -> dict[str, dict[str, Any]]:
    try:
        with open(OVERRIDES_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception:
        logger.exception("Failed to load category overrides from %s", OVERRIDES_PATH)
        return {}

    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for code, entry in data.items():
        if isinstance(entry, dict):
            out[str(code)] = {k: v for k, v in entry.items() if k in ALLOWED_KEYS}
    return out


def _save_overrides(overrides: dict[str, dict[str, Any]]) -> None:
    tmp_path = OVERRIDES_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(overrides, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, OVERRIDES_PATH)


def apply_overrides(category_list: list[dict[str, Any]]) -> None:
    """Mutates the given category list in place with any stored overrides."""
    overrides = load_overrides()
    if not overrides:
        return
    by_code = {str(cat.get("name")): cat for cat in category_list}
    for code, entry in overrides.items():
        cat = by_code.get(code)
        if not cat:
            continue
        for key, value in entry.items():
            if key in ALLOWED_KEYS:
                cat[key] = value


def set_category_override(
    category_list: list[dict[str, Any]], code: str, updates: dict[str, Any]
) -> bool:
    """Applies `updates` to the in-memory category and persists them to disk.

    Returns True on success. Only keys in ALLOWED_KEYS are considered.
    """
    filtered = {k: v for k, v in updates.items() if k in ALLOWED_KEYS}
    if not filtered:
        return False

    cat = next((c for c in category_list if str(c.get("name")) == str(code)), None)
    if not cat:
        return False

    cat.update(filtered)

    overrides = load_overrides()
    entry = overrides.get(str(code)) or {}
    entry.update(filtered)
    overrides[str(code)] = entry
    try:
        _save_overrides(overrides)
    except Exception:
        logger.exception("Failed to persist category override for %s", code)
        return False
    return True
