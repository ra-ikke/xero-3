"""Status metadata used by review/discussion flows (ported from legacy JS resources)."""

STATUS_LIST: list[dict[str, str]] = [
    {"name": "PERM", "description": "Permed", "color": "#30BA76"},
    {"name": "EDIT", "description": "Edited", "color": "#009D9D"},
    {"name": "MOVE", "description": "Moved to another category", "color": "#009D9D"},
    {"name": "AWAIT", "description": "Awaiting author edits", "color": "#F0A78E"},
    {"name": "DEPERM", "description": "Depermed", "color": "#6C77C1"},
    {"name": "REJECT", "description": "Rejected", "color": "#CB546B"},
    {"name": "IN DISCUSSION", "description": "In discussion", "color": "#009D9D"},
    {"name": "PERM MONTH", "description": "Permed for a month", "color": "#92CF91"},
    {"name": "PERM CONTEST", "description": "Permed for a month from a contest", "color": "#92CF91"},
    {"name": "CONTEST", "description": "Permed from a contest", "color": "#92CF91"},
    {"name": "KEEP", "description": "Kept with or without edits", "color": "#BABD2F"},
]

STATUSES_BY_NAME: dict[str, dict[str, str]] = {s["name"]: s for s in STATUS_LIST}

