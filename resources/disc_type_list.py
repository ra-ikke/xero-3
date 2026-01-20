"""Discussion type metadata (ported from the legacy JS resources)."""

DISC_TYPE_LIST: list[dict[str, str]] = [
    {"name": "PERM", "description": "Perm Map", "color": "#30BA76"},
    {"name": "DEPERM", "description": "Deperm Map", "color": "#CB546B"},
    {"name": "PERM MONTH", "description": "Perm for a month", "color": "#92CF91"},
    {"name": "EDIT", "description": "Ask for edits", "color": "#F0A78E"},
    {"name": "OTHER", "description": "Other", "color": "#F0A78E"},
]

DISC_TYPES_BY_NAME: dict[str, dict[str, str]] = {d["name"]: d for d in DISC_TYPE_LIST}

