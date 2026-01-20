"""Forum scraping logic (ported from legacy forum/forum-review.js)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

import aiohttp
from bs4 import BeautifulSoup

from resources.category_list import CATEGORY_LIST

logger = logging.getLogger(__name__)


CATEGORY_THREAD_URLS: dict[str, str] = {
    "P3": "https://atelier801.com/topic?f=6&t=48030",
    "P4": "https://atelier801.com/topic?f=6&t=48016",
    "P5": "https://atelier801.com/topic?f=6&t=48019",
    "P6": "https://atelier801.com/topic?f=6&t=48020",
    "P7": "https://atelier801.com/topic?f=6&t=48021",
    "P8": "https://atelier801.com/topic?f=6&t=48022",
    "P9": "https://atelier801.com/topic?f=6&t=48023",
    "P10": "https://atelier801.com/topic?f=6&t=98668",
    "P11": "https://atelier801.com/topic?f=6&t=98669",
    "P17": "https://atelier801.com/topic?f=6&t=212241",
    "P18": "https://atelier801.com/topic?f=6&t=218925",
    "P24": "https://atelier801.com/topic?f=6&t=795787",
}


def _find_category(code: str) -> Optional[dict[str, Any]]:
    return next((c for c in CATEGORY_LIST if c.get("name") == code), None)


def _build_thread_url(base: str, *, n: int = 100, p: Optional[int] = None) -> str:
    parsed = urlparse(base)
    qs = parse_qs(parsed.query)
    qs["n"] = [str(n)]
    if p is not None:
        qs["p"] = [str(p)]
    query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=query))


async def _fetch_html(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url) as resp:
        resp.raise_for_status()
        return await resp.text()


def _get_last_page_from_html(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    # Try to find a pagination label like "1 / 32"
    pagination = soup.select_one("div.cadre-pagination.btn-group.ltr a.btn.btn-inverse[href='#']")
    if pagination and pagination.get_text(strip=True):
        text = pagination.get_text(strip=True)
        match = re.search(r"/\s*(\d+)", text)
        if match:
            return int(match.group(1))
    # Fallback: find numeric page links and take max.
    nums = []
    for a in soup.select("div.cadre-pagination.btn-group.ltr a.btn"):
        t = a.get_text(strip=True)
        if t.isdigit():
            nums.append(int(t))
    return max(nums) if nums else 1


def _extract_message_ids_from_page(html: str, category_code: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    message_ids: list[str] = []

    if category_code not in ("P3",):
        valid_content = {"Left as is", "Ped", "Will be discussed", "Ignored"}
        for msg in soup.select(".cadre-message-message"):
            bold = msg.select_one("span[style='font-weight:bold;']")
            if not bold:
                continue
            underlined = bold.select_one("span[style='text-decoration:underline;']")
            content = underlined.get_text(strip=True) if underlined else ""
            if not content:
                continue
            parts = [p.strip() for p in content.split(":") if p.strip()]
            if any(p in valid_content for p in parts):
                div = msg.select_one("div[id^='message_']")
                if div and div.get("id", "").startswith("message_"):
                    message_ids.append(div["id"][8:])
    else:
        # P3 uses a "CHECKPOINT!" marker.
        for msg in soup.select(".cadre-message-message"):
            checkpoint = msg.select_one("span[style='color:#BABD2F;'] > span[style='font-size:16px;']")
            if checkpoint and checkpoint.get_text(strip=True) == "CHECKPOINT!":
                div = msg.select_one("div[id^='message_']")
                if div and div.get("id", "").startswith("message_"):
                    message_ids.append(div["id"][8:])

    return message_ids


def _format_datetime_br(dt: datetime) -> str:
    return dt.strftime("%d/%m/%Y, %H:%M:%S")


def _process_section(section_title: str, section_html: str, category_code: str) -> dict[str, Any]:
    # If content doesn't contain any map codes, return empty.
    if "@" not in section_html:
        return {"title": section_title, "category": category_code, "content": [], "quantity": 0}

    text = section_html.replace("<br>", " ")
    regex = re.compile(r"@(\d+)\s*[–-]\s*([^\s#]+#\d{4})")
    maps = [{"code": f"@{code}", "author": author.strip()} for code, author in regex.findall(text)]
    return {"title": section_title, "category": category_code, "content": maps, "quantity": len(maps)}


def _extract_message_info(html: str, message_id: str, category_code: str) -> Optional[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("div", id=f"cadre_message_sujet_{message_id}")
    if not container:
        return None

    avatar = None
    avatar_img = container.select_one("img.element-composant-auteur.bouton-profil-avatar")
    if avatar_img:
        avatar = avatar_img.get("src")
    avatar = avatar or "https://i.imgur.com/5sXT96W.png"

    profile = None
    profile_a = container.select_one("a[href^='profile?pr=']")
    if profile_a:
        profile = profile_a.get("href")
    author_id = (profile or "profile?pr=unknown")[11:]

    message_number = (container.select_one("a.numero-message") or {}).get_text(strip=True) if container.select_one("a.numero-message") else None
    message_url = container.select_one("a.numero-message").get("href") if container.select_one("a.numero-message") else None
    raw_ts = container.select_one("div.element-composant-auteur.cadre-auteur-message-date span")
    ts_text = raw_ts.get_text(strip=True) if raw_ts else "0"
    try:
        dt = datetime.fromtimestamp(int(ts_text) / 1000, tz=timezone.utc)
    except Exception:
        dt = datetime.now(timezone.utc)

    quantity = 0
    review: list[dict[str, Any]] = []

    if category_code == "P3":
        # Attempt to parse "Will be discussed" list.
        # The legacy code reads from tab_2_message_XXXX.
        msg_num = (message_number or "").replace("#", "")
        discuss = container.select_one(f"#tab_2_message_{msg_num}")
        if discuss:
            span = discuss.select_one("span[style='color:#009D9D;']")
            if span:
                text = span.get_text(" ", strip=True)
                maps = []
                for author, code in re.findall(r"(\w+(?:#\d{4})?)\s*-\s*@(\d+)", text):
                    if "#" not in author:
                        author = f"{author}#0000"
                    maps.append({"author": author, "code": f"@{code}"})
                review.append({"title": "Will be discussed", "category": category_code, "content": maps, "quantity": len(maps)})
    else:
        valid_titles = {"Left as is", "Ped", "Will be discussed", "Ignored"}
        for bold in container.select("span[style='font-weight:bold;']"):
            inner_span = bold.find("span")
            section_title = (inner_span.get_text(strip=True) if inner_span else bold.get_text(strip=True)).replace(":", "")
            # Normalize perm-like title
            if re.search(r"P\d+'ed", section_title):
                section_title = "Ped"
            if section_title not in valid_titles:
                continue

            # Next sibling span contains the section content.
            next_span = bold.find_next_sibling("span")
            section = _process_section(section_title, str(next_span) if next_span else "", category_code)
            quantity += section["quantity"]
            review.append(section)

    return {
        "author": author_id,
        "avatar": avatar,
        "message": message_number or "N/A",
        "url": message_url or "",
        "dateTime": _format_datetime_br(dt),
        "review": review,
        "quantity": quantity,
    }


async def get_info(category_code: str) -> Optional[dict[str, Any]]:
    """
    Scrapes the atelier801 thread for the category and returns a review payload.
    """
    cat = _find_category(category_code)
    if not cat:
        logger.error("Category %s not found in CATEGORY_LIST", category_code)
        return None

    thread_url = CATEGORY_THREAD_URLS.get(category_code)
    if not thread_url:
        logger.error("Thread URL not configured for category %s", category_code)
        return None

    base_url = _build_thread_url(thread_url, n=100)
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        html = await _fetch_html(session, base_url)
        last_page = _get_last_page_from_html(html)

        found_message_id = None
        found_page = None

        # Optimization: scan only a few pages backwards.
        for page in range(last_page, max(last_page - 10, 0), -1):
            page_url = _build_thread_url(thread_url, n=100, p=page)
            page_html = await _fetch_html(session, page_url)
            message_ids = _extract_message_ids_from_page(page_html, category_code)
            if message_ids:
                found_message_id = message_ids[-1]
                found_page = page
                html_for_message = page_html
                break

        if not found_message_id or not found_page:
            return None

        message_info = _extract_message_info(html_for_message, found_message_id, category_code)
        if not message_info:
            return None

        message_num = int(str(message_info["message"]).replace("#", "") or "0")
        review_page = max(1, (message_num + 19) // 20)

        # Normalize URL to point to the review page.
        review_url = message_info["url"]
        if review_url:
            # Replace p in url to the computed review page.
            parsed = urlparse(review_url)
            qs = parse_qs(parsed.query)
            qs.pop("n", None)
            qs["p"] = [str(review_page)]
            review_url = urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))

        return {
            "category": category_code,
            "categoryDescription": cat.get("description", category_code),
            "picture": cat.get("picture", ""),
            "emoji": cat.get("emoji", ""),
            "color": cat.get("color", "#009D9D"),
            "author": message_info["author"],
            "avatar": message_info["avatar"],
            "message": message_info["message"],
            "page": str(review_page),
            "url": review_url,
            "dateTime": message_info["dateTime"],
            "review": message_info["review"],
            "quantity": message_info["quantity"],
        }

