"""Generate MC_LogChannel analytics HTML from Discord logs.

Reads MC_CHANGELOG messages posted by the bot, aggregates stats, and writes
an English HTML report. Output path defaults to the repo root (d:/xerobot).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import discord

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None


ROOT_DIR = Path(__file__).resolve().parents[2]
XERO_DIR = Path(__file__).resolve().parents[1]
MAPS_REVIEWER_DIR = ROOT_DIR / "maps-reviewer-desktop"
CATEGORIES_TS = MAPS_REVIEWER_DIR / "src" / "app" / "categories.ts"
DEFAULT_OUTPUT = ROOT_DIR / "mc_log_analytics.html"

if str(XERO_DIR) not in sys.path:
    sys.path.insert(0, str(XERO_DIR))

from resources.channels import MC_CHANGELOG  # noqa: E402
from resources.disc_type_list import DISC_TYPE_LIST  # noqa: E402
from resources.status_list import STATUS_LIST  # noqa: E402


PERM_STATUSES = {"PERM", "PERM MONTH", "PERM CONTEST", "CONTEST", "KEEP"}
DEPERM_STATUSES = {"DEPERM"}
REJECT_STATUSES = {"REJECT"}


@dataclass(frozen=True)
class LogEntry:
    code: str
    timestamp: float
    disc_status: str
    category: Optional[str]
    original_category: Optional[str]
    target_category: Optional[str]


def _normalize_category(value: Optional[str]) -> Optional[str]:
    raw = (value or "").strip().upper()
    if not raw:
        return None
    if raw.isdigit():
        return f"P{raw}"
    if not raw.startswith("P") and raw[0].isdigit():
        return f"P{raw}"
    return raw


def _parse_categories_ts(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    block_re = re.compile(
        r"\{\s*code:\s*'(?P<code>P\d+)'.*?description:\s*'(?P<desc>[^']+)'.*?(?:picture:\s*'(?P<pic>[^']+)')?",
        re.S,
    )
    out: list[dict[str, str]] = []
    for match in block_re.finditer(raw):
        code = match.group("code").strip()
        desc = match.group("desc").strip()
        pic = (match.group("pic") or "").strip()
        out.append({"code": code, "description": desc, "picture": pic})
    return out


def _safe_json_loads(text: str) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _entry_from_payload(payload: dict[str, Any], *, ts: float) -> Optional[LogEntry]:
    code = str(payload.get("code") or "").strip()
    disc_status = str(payload.get("disc_status") or "").strip().upper()
    if not code or not disc_status:
        return None
    category = _normalize_category(payload.get("category"))
    original_category = _normalize_category(payload.get("original_category"))
    target_category = _normalize_category(payload.get("target_category"))
    return LogEntry(
        code=code,
        timestamp=ts,
        disc_status=disc_status,
        category=category,
        original_category=original_category,
        target_category=target_category,
    )


def _category_for_entry(entry: LogEntry) -> Optional[str]:
    if entry.disc_status == "MOVE":
        return entry.target_category or entry.category or entry.original_category
    return entry.category or entry.original_category or entry.target_category


def _as_percent(value: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((value / total) * 100.0, 2)


def _sort_category_key(code: str) -> tuple[int, str]:
    if code.upper().startswith("P"):
        try:
            return (int(code[1:]), code)
        except Exception:
            pass
    return (999, code)


def _build_html(
    *,
    categories: list[dict[str, str]],
    first_by_code: dict[str, LogEntry],
    last_by_code: dict[str, LogEntry],
    discussion_type_counts: dict[str, int],
    status_counts: dict[str, int],
    range_start: Optional[datetime],
    range_end: Optional[datetime],
) -> str:
    total_maps = len(first_by_code)

    permed = sum(
        1
        for entry in last_by_code.values()
        if entry.disc_status in PERM_STATUSES
    )
    depermed = sum(
        1
        for entry in last_by_code.values()
        if entry.disc_status in DEPERM_STATUSES
    )
    rejected = sum(
        1
        for entry in last_by_code.values()
        if entry.disc_status in REJECT_STATUSES
    )

    counts_by_category: dict[str, int] = {}
    for entry in first_by_code.values():
        cat = _category_for_entry(entry)
        if not cat:
            continue
        counts_by_category[cat] = counts_by_category.get(cat, 0) + 1

    outcomes_by_category: dict[str, dict[str, int]] = {}
    for entry in last_by_code.values():
        cat = _category_for_entry(entry)
        if not cat:
            continue
        bucket = outcomes_by_category.setdefault(cat, {"permed": 0, "depermed": 0, "rejected": 0})
        if entry.disc_status in PERM_STATUSES:
            bucket["permed"] += 1
        elif entry.disc_status in DEPERM_STATUSES:
            bucket["depermed"] += 1
        elif entry.disc_status in REJECT_STATUSES:
            bucket["rejected"] += 1

    categories_by_code = {c["code"]: c for c in categories}
    sorted_categories = sorted(counts_by_category.items(), key=lambda it: _sort_category_key(it[0]))

    range_text = "Unknown range"
    if range_start and range_end:
        range_text = f"{range_start.date().isoformat()} → {range_end.date().isoformat()}"

    def _category_label(code: str) -> str:
        meta = categories_by_code.get(code, {})
        return meta.get("description") or code

    def _category_pic(code: str) -> str:
        meta = categories_by_code.get(code, {})
        return meta.get("picture", "")

    def _row_bar(value: int, total: int) -> str:
        pct = _as_percent(value, total)
        return f'<div class="bar"><div class="bar-fill" style="width: {pct}%;"></div></div>'

    def _format_status_label(status: str) -> str:
        for item in STATUS_LIST:
            if item.get("name") == status:
                return f'{item["description"]} ({status})'
        return status

    discussion_type_rows = []
    for dtype in DISC_TYPE_LIST:
        name = dtype["name"]
        count = discussion_type_counts.get(name, 0)
        discussion_type_rows.append(
            f"<tr><td>{name}</td><td>{dtype['description']}</td><td>{count}</td><td>{_row_bar(count, total_maps)}</td></tr>"
        )
    other_discussion = discussion_type_counts.get("OTHER", 0)
    if other_discussion:
        discussion_type_rows.append(
            f"<tr><td>OTHER</td><td>Other / Unknown</td><td>{other_discussion}</td><td>{_row_bar(other_discussion, total_maps)}</td></tr>"
        )

    status_rows = []
    for status, count in sorted(status_counts.items(), key=lambda it: (-it[1], it[0])):
        status_rows.append(
            f"<tr><td>{_format_status_label(status)}</td><td>{count}</td><td>{_row_bar(count, total_maps)}</td></tr>"
        )

    category_rows = []
    for code, count in sorted_categories:
        label = _category_label(code)
        pic = _category_pic(code)
        icon = f'<img src="{pic}" alt="{code}" />' if pic else ""
        category_rows.append(
            f"<tr><td class='cat-cell'>{icon}<div><strong>{label}</strong><div class='muted'>{code}</div></div></td>"
            f"<td>{count}</td><td>{_row_bar(count, total_maps)}</td></tr>"
        )

    outcomes_rows = []
    for code in sorted(outcomes_by_category.keys(), key=_sort_category_key):
        label = _category_label(code)
        pic = _category_pic(code)
        icon = f'<img src="{pic}" alt="{code}" />' if pic else ""
        bucket = outcomes_by_category.get(code, {})
        outcomes_rows.append(
            f"<tr><td class='cat-cell'>{icon}<div><strong>{label}</strong><div class='muted'>{code}</div></div></td>"
            f"<td>{bucket.get('permed', 0)}</td><td>{bucket.get('depermed', 0)}</td><td>{bucket.get('rejected', 0)}</td></tr>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MC Log Analytics</title>
  <style>
    :root {{
      color-scheme: dark;
      font-family: "Segoe UI", Arial, sans-serif;
      background: #0f1115;
      color: #e9edf1;
    }}
    body {{ margin: 0; padding: 24px; }}
    h1 {{ margin: 0 0 6px; font-size: 28px; }}
    h2 {{ margin: 28px 0 12px; font-size: 20px; }}
    p {{ margin: 6px 0; color: #b7c0ca; }}
    .muted {{ color: #9aa4af; font-size: 12px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-top: 16px; }}
    .card {{ background: #151924; border: 1px solid #222734; border-radius: 10px; padding: 14px; }}
    .card .value {{ font-size: 22px; font-weight: 600; }}
    .card .label {{ font-size: 12px; color: #9aa4af; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
    th, td {{ padding: 10px 8px; border-bottom: 1px solid #202633; text-align: left; }}
    th {{ color: #c9d2dc; font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }}
    .cat-cell {{ display: flex; gap: 10px; align-items: center; }}
    .cat-cell img {{ width: 36px; height: 36px; border-radius: 8px; background: #0b0e13; }}
    .bar {{ width: 100%; background: #202633; border-radius: 8px; height: 8px; overflow: hidden; }}
    .bar-fill {{ height: 100%; background: linear-gradient(90deg, #57a4ff, #8cd6ff); }}
    .section {{ margin-top: 18px; padding: 12px; border: 1px solid #1c2230; border-radius: 10px; background: #121620; }}
    .legend {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 8px; }}
    .legend span {{ font-size: 12px; color: #aab4be; }}
  </style>
</head>
<body>
  <h1>MC Log Analytics</h1>
  <p>Source: MC_CHANGELOG channel logs · Range: {range_text}</p>
  <p class="muted">Counts are based on bot changelog payloads. Moves are counted on target category.</p>

  <div class="cards">
    <div class="card"><div class="value">{total_maps}</div><div class="label">Total maps submitted</div></div>
    <div class="card"><div class="value">{permed}</div><div class="label">Permed (incl. perm month/contest/keep)</div></div>
    <div class="card"><div class="value">{depermed}</div><div class="label">Depermed</div></div>
    <div class="card"><div class="value">{rejected}</div><div class="label">Rejected</div></div>
  </div>

  <div class="section">
    <h2>Maps submitted by category</h2>
    <table>
      <thead><tr><th>Category</th><th>Maps</th><th>Share</th></tr></thead>
      <tbody>
        {''.join(category_rows) if category_rows else '<tr><td colspan="3">No data</td></tr>'}
      </tbody>
    </table>
  </div>

  <div class="section">
    <h2>Discussion types (from logs)</h2>
    <table>
      <thead><tr><th>Type</th><th>Description</th><th>Maps</th><th>Share</th></tr></thead>
      <tbody>
        {''.join(discussion_type_rows) if discussion_type_rows else '<tr><td colspan="4">No data</td></tr>'}
      </tbody>
    </table>
  </div>

  <div class="section">
    <h2>Status outcomes</h2>
    <table>
      <thead><tr><th>Status</th><th>Maps</th><th>Share</th></tr></thead>
      <tbody>
        {''.join(status_rows) if status_rows else '<tr><td colspan="3">No data</td></tr>'}
      </tbody>
    </table>
  </div>

  <div class="section">
    <h2>Outcomes by category</h2>
    <table>
      <thead><tr><th>Category</th><th>Permed</th><th>Depermed</th><th>Rejected</th></tr></thead>
      <tbody>
        {''.join(outcomes_rows) if outcomes_rows else '<tr><td colspan="4">No data</td></tr>'}
      </tbody>
    </table>
  </div>
</body>
</html>
"""


async def _collect_log_entries() -> list[LogEntry]:
    if load_dotenv:
        load_dotenv()

    token = (os.getenv("DISCORD_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("DISCORD_TOKEN is missing. Set it in your environment/.env.")

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    entries: list[LogEntry] = []
    range_start: Optional[datetime] = None
    range_end: Optional[datetime] = None

    async def _run() -> None:
        nonlocal range_start, range_end
        await client.login(token)
        channel = await client.fetch_channel(int(MC_CHANGELOG))
        if not isinstance(channel, discord.abc.Messageable):
            raise RuntimeError("MC_CHANGELOG is not messageable.")

        async for message in channel.history(limit=None, oldest_first=True):
            if not message.content:
                continue
            payload = _safe_json_loads(message.content)
            if not payload:
                continue
            ts = message.created_at.replace(tzinfo=timezone.utc).timestamp()
            entry = _entry_from_payload(payload, ts=ts)
            if not entry:
                continue
            entries.append(entry)
            msg_dt = message.created_at.replace(tzinfo=timezone.utc)
            if not range_start or msg_dt < range_start:
                range_start = msg_dt
            if not range_end or msg_dt > range_end:
                range_end = msg_dt

        await client.close()

    await _run()
    return entries


def _aggregate(entries: Iterable[LogEntry]) -> tuple[
    dict[str, LogEntry],
    dict[str, LogEntry],
    dict[str, int],
    dict[str, int],
]:
    first_by_code: dict[str, LogEntry] = {}
    last_by_code: dict[str, LogEntry] = {}
    for entry in entries:
        if entry.code not in first_by_code and _category_for_entry(entry):
            first_by_code[entry.code] = entry
        prev = last_by_code.get(entry.code)
        if not prev or entry.timestamp > prev.timestamp:
            last_by_code[entry.code] = entry

    discussion_type_counts: dict[str, int] = {d["name"]: 0 for d in DISC_TYPE_LIST}
    discussion_type_counts["OTHER"] = 0
    status_counts: dict[str, int] = {}
    for entry in last_by_code.values():
        status_counts[entry.disc_status] = status_counts.get(entry.disc_status, 0) + 1
        if entry.disc_status in discussion_type_counts:
            discussion_type_counts[entry.disc_status] += 1
        else:
            discussion_type_counts["OTHER"] += 1
    return first_by_code, last_by_code, discussion_type_counts, status_counts


async def main() -> None:
    entries = await _collect_log_entries()
    first_by_code, last_by_code, discussion_type_counts, status_counts = _aggregate(entries)
    categories = _parse_categories_ts(CATEGORIES_TS)

    if entries:
        range_start = datetime.fromtimestamp(min(e.timestamp for e in entries), tz=timezone.utc)
        range_end = datetime.fromtimestamp(max(e.timestamp for e in entries), tz=timezone.utc)
    else:
        range_start = None
        range_end = None

    html = _build_html(
        categories=categories,
        first_by_code=first_by_code,
        last_by_code=last_by_code,
        discussion_type_counts=discussion_type_counts,
        status_counts=status_counts,
        range_start=range_start,
        range_end=range_end,
    )
    DEFAULT_OUTPUT.write_text(html, encoding="utf-8")
    print(f"Report written to: {DEFAULT_OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
