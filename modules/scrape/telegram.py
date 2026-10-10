"""Scraper for public Telegram channel web previews (https://t.me/s/<channel>).

Enables OSINT and Threat Intelligence gathering from public Telegram channels
(e.g., threat actor announcements, leak announcements, malware release logs)
without requiring a Telegram API ID, Bot token, or phone number.
"""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from core.security import (
    fence_untrusted,
    is_abuse_material,
    scrub_untrusted_text,
)

logger = logging.getLogger(__name__)

CHANNEL_HANDLE_RE = re.compile(r"^[a-zA-Z0-9_]{4,64}$")
TELEGRAM_HOSTS = frozenset({"t.me", "telegram.me"})
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def extract_telegram_channel_name(target: str) -> Optional[str]:
    """Extract a clean Telegram channel username from a URL or handle.

    Examples:
        - "@threat_actor" -> "threat_actor"
        - "https://t.me/threat_actor" -> "threat_actor"
        - "https://t.me/s/threat_actor" -> "threat_actor"
        - "https://telegram.me/threat_actor" -> "threat_actor"
    """
    if not target or not isinstance(target, str):
        return None

    cleaned = target.strip()

    # Handle direct @handle notation
    if cleaned.startswith("@"):
        candidate = cleaned[1:].strip()
        return candidate if CHANNEL_HANDLE_RE.match(candidate) else None

    # Parse URL
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        if cleaned.startswith("t.me/") or cleaned.startswith("telegram.me/"):
            cleaned = "https://" + cleaned
        else:
            # Check if it's a bare username
            if CHANNEL_HANDLE_RE.match(cleaned):
                return cleaned
            return None

    try:
        parsed = urlparse(cleaned)
    except Exception:
        return None

    host = (parsed.hostname or "").lower()
    if host not in TELEGRAM_HOSTS and not host.endswith(".t.me"):
        return None

    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    if not path_parts:
        return None

    # If format is /s/channel_name
    if path_parts[0].lower() == "s" and len(path_parts) > 1:
        candidate = path_parts[1]
    else:
        candidate = path_parts[0]

    # Ignore private invite links or internal paths
    if candidate.lower() in {"joinchat", "c", "addstickers", "proxy", "share", "login", "contact"}:
        return None

    if CHANNEL_HANDLE_RE.match(candidate):
        return candidate

    return None


def is_telegram_channel(target: str) -> bool:
    """Return True if the target represents a Telegram channel."""
    return extract_telegram_channel_name(target) is not None


def scrape_telegram_channel(
    channel_or_url: str,
    max_posts: int = 15,
    max_return_chars: int = 8000,
    session: Optional[requests.Session] = None,
    timeout: int = 15,
) -> Dict[str, Any]:
    """Fetch public web preview posts for a Telegram channel and return sanitized text."""
    channel_name = extract_telegram_channel_name(channel_or_url)
    if not channel_name:
        return {
            "status": "error",
            "url": channel_or_url,
            "error": "Invalid Telegram channel identifier",
            "posts_count": 0,
            "text": "",
        }

    preview_url = f"https://t.me/s/{channel_name}"
    s = session or requests.Session()

    try:
        resp = s.get(
            preview_url,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
    except Exception as e:
        logger.warning("Failed to fetch Telegram channel %s: %s", preview_url, e)
        return {
            "status": "failed",
            "url": preview_url,
            "error": str(e),
            "posts_count": 0,
            "text": "",
        }

    if resp.status_code != 200:
        return {
            "status": f"http_{resp.status_code}",
            "url": preview_url,
            "posts_count": 0,
            "text": "",
        }

    soup = BeautifulSoup(resp.text, "html.parser")

    # Channel metadata
    title_el = soup.select_one(".tgme_channel_info_header_title")
    title = clean_text = title_el.get_text(strip=True) if title_el else channel_name

    desc_el = soup.select_one(".tgme_channel_info_description")
    description = desc_el.get_text(strip=True) if desc_el else ""

    counter_el = soup.select_one(".tgme_header_counter")
    subscribers = counter_el.get_text(strip=True) if counter_el else ""

    # Messages
    posts: List[Dict[str, str]] = []
    message_divs = soup.select(".tgme_widget_message_wrap")

    for wrap in reversed(message_divs):  # newest first
        if len(posts) >= max_posts:
            break

        msg_body = wrap.select_one(".tgme_widget_message_text")
        if not msg_body:
            continue

        raw_text = msg_body.get_text(separator="\n", strip=True)
        if not raw_text:
            continue

        # CSAM abuse filtering
        if is_abuse_material({"title": raw_text, "link": preview_url}):
            continue

        date_el = wrap.select_one(".tgme_widget_message_date time")
        date_str = date_el.get("datetime", "") if date_el else ""

        views_el = wrap.select_one(".tgme_widget_message_views")
        views_str = views_el.get_text(strip=True) if views_el else ""

        posts.append({
            "date": scrub_untrusted_text(date_str),
            "views": scrub_untrusted_text(views_str),
            "text": scrub_untrusted_text(raw_text),
        })

    # Build readable formatted body
    body_lines = [
        f"TELEGRAM CHANNEL OSINT: @{channel_name}",
        f"Title: {scrub_untrusted_text(title)}",
        f"Subscribers: {scrub_untrusted_text(subscribers)}",
        f"Description: {scrub_untrusted_text(description)}",
        "--- RECENT CHANNEL POSTS ---",
    ]

    for idx, p in enumerate(posts, 1):
        dt = f" [{p['date']}]" if p["date"] else ""
        vw = f" ({p['views']} views)" if p["views"] else ""
        body_lines.append(f"\n[Post #{idx}{dt}{vw}]\n{p['text']}")

    combined_text = "\n".join(body_lines)
    if len(combined_text) > max_return_chars:
        combined_text = combined_text[:max_return_chars]

    # Mandatory security invariant: wrap in untrusted delimiter
    fenced_content = fence_untrusted(combined_text)

    return {
        "status": "ok",
        "url": preview_url,
        "channel_name": channel_name,
        "title": title,
        "posts_count": len(posts),
        "text": fenced_content,
    }
