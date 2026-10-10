"""Clear Web search module using DuckDuckGo search client."""

import logging
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from core.security import is_abuse_material, scrub_untrusted_text

logger = logging.getLogger(__name__)

DEFAULT_PER_HOST_LIMIT = 3


def _dedup_key(link: str) -> str:
    parsed = urlparse(link or "")
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = f":{parsed.port}" if parsed.port and parsed.port not in (80, 443) else ""
    path = parsed.path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{scheme}://{host}{port}{path}{query}"


def _filter_and_cap_results(
    raw_results: List[Dict[str, Any]],
    per_host_limit: int = DEFAULT_PER_HOST_LIMIT,
) -> List[Dict[str, str]]:
    """Deduplicate, filter CSAM, and apply per-host limits."""
    seen_urls = set()
    host_counts: Dict[str, int] = {}
    kept: List[Dict[str, str]] = []

    for item in raw_results:
        link = str(item.get("href") or item.get("link") or item.get("url") or "").strip()
        title = scrub_untrusted_text(str(item.get("title") or "")).strip() or "Untitled"
        snippet = scrub_untrusted_text(
            str(item.get("body") or item.get("snippet") or item.get("description") or "")
        ).strip()

        if not link or not link.startswith(("http://", "https://")):
            continue

        # Dedup URL
        norm_key = _dedup_key(link)
        if norm_key in seen_urls:
            continue
        seen_urls.add(norm_key)

        # CSAM check
        if is_abuse_material({"title": title, "link": link}):
            logger.warning("Filtered CSAM material on clearweb search: %s", link)
            continue

        # Per-host cap
        host = (urlparse(link).hostname or "").lower()
        if host_counts.get(host, 0) >= per_host_limit:
            continue
        host_counts[host] = host_counts.get(host, 0) + 1

        kept.append(
            {
                "title": title,
                "link": link,
                "snippet": snippet,
                "engine": "duckduckgo",
                "source": "clearweb",
            }
        )

    return kept


def search_clearweb(
    query: str,
    max_results: int = 10,
    per_host_limit: int = DEFAULT_PER_HOST_LIMIT,
    search_fn: Optional[Callable[[str, int], List[Dict[str, Any]]]] = None,
) -> List[Dict[str, str]]:
    """Search Clear Web via DuckDuckGo and return normalized, sanitized results."""
    cleaned_query = scrub_untrusted_text(query).strip()
    if not cleaned_query:
        return []

    raw_items: List[Dict[str, Any]] = []

    # If a custom search function is injected (e.g. for testing)
    if search_fn is not None:
        try:
            raw_items = search_fn(cleaned_query, max_results)
        except Exception as e:
            logger.warning("Custom clearweb search failed: %s", e)
            return []
    else:
        try:
            from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                results_iter = ddgs.text(
                    keywords=cleaned_query,
                    max_results=max_results,
                )
                if results_iter:
                    raw_items = list(results_iter)
        except Exception as e:
            logger.warning("DuckDuckGo search encountered an error: %s", e)

        # Fallback: if DDGS returned fewer than 2 results or failed (e.g. rate-limited), query html.duckduckgo.com directly
        if len(raw_items) < 2:
            try:
                import urllib.parse
                import requests
                from bs4 import BeautifulSoup

                resp = requests.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": cleaned_query},
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for result_div in soup.select(".result"):
                        title_elem = result_div.select_one(".result__title a")
                        snippet_elem = result_div.select_one(".result__snippet")
                        if not title_elem:
                            continue
                        raw_href = title_elem.get("href", "")
                        target_url = raw_href
                        if "uddg=" in raw_href:
                            parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw_href).query)
                            if "uddg" in parsed_qs:
                                target_url = parsed_qs["uddg"][0]
                        elif raw_href.startswith("//"):
                            target_url = "https:" + raw_href

                        raw_items.append({
                            "title": title_elem.get_text(strip=True),
                            "href": target_url,
                            "body": snippet_elem.get_text(strip=True) if snippet_elem else "",
                        })
                        if len(raw_items) >= max_results:
                            break
            except Exception as e:
                logger.warning("HTML DuckDuckGo fallback error: %s", e)

    return _filter_and_cap_results(raw_items, per_host_limit=per_host_limit)
