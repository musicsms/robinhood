"""Direct HTTP scraper for Clear Web resources (bypasses Tor, fences untrusted content)."""

import logging
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple, Union

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.security import (
    BLOCKED_STATUS_CODES,
    STATUS_BLOCKED,
    STATUS_ERROR,
    STATUS_OK,
    _looks_like_challenge,
    fence_untrusted,
    is_onion,
    scrub_untrusted_text,
    url_policy_problem,
)
from modules.scrape.base import (
    ALLOWED_CONTENT_TYPES,
    MAX_DOWNLOAD_BYTES,
    MAX_RETURN_CHARS,
    decode_capped,
    extract_page_text,
    make_scrape_record,
    read_capped,
)

logger = logging.getLogger(__name__)

CLEARWEB_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.7; rv:137.0) Gecko/20100101 Firefox/137.0",
]

DEFAULT_TIMEOUT = (10, 20)


def build_clearweb_session() -> requests.Session:
    """Create a standard direct HTTP session with browser headers (no Tor proxy)."""
    session = requests.Session()
    retry = Retry(
        total=2,
        read=2,
        connect=1,
        backoff_factor=0.3,
        status_forcelist=[500, 502, 504],
        allowed_methods=frozenset(["GET", "HEAD"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": random.choice(CLEARWEB_USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Not/A)Brand";v="8", "Chromium";v="126"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Linux"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
    )
    return session


def _normalize_url_data(url_data: Union[str, Dict]) -> Tuple[str, str]:
    if isinstance(url_data, dict):
        url = str(url_data.get("link") or url_data.get("url") or "").strip()
        title = scrub_untrusted_text(str(url_data.get("title") or "Untitled")).strip() or "Untitled"
        return url, title
    if isinstance(url_data, str):
        return url_data.strip(), "Untitled"
    return "", "Untitled"


def scrape_clearweb_single(
    url_data: Union[str, Dict],
    max_return_chars: Optional[int] = None,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
    session: Optional[requests.Session] = None,
) -> Dict:
    """Fetch and scrape a single Clear Web page directly (no Tor)."""
    url, title = _normalize_url_data(url_data)
    if not url:
        return make_scrape_record(STATUS_ERROR, title, detail="empty url", url=url)

    # Do not scrape onion domains via clearweb scraper
    if is_onion(url):
        return make_scrape_record(
            STATUS_ERROR,
            title,
            detail="onion host passed to clearweb scraper (use Tor scraper instead)",
            url=url,
        )

    # Validate URL policy with clearweb allowed
    problem = url_policy_problem(url, allow_clearweb=True)
    if problem:
        status, detail = problem
        return make_scrape_record(status, title, detail=f"refused: {detail}", url=url)

    sess = session or build_clearweb_session()
    max_chars = max_return_chars if max_return_chars is not None else MAX_RETURN_CHARS

    try:
        response = sess.get(url, allow_redirects=True, stream=True, timeout=timeout)
    except requests.RequestException as e:
        return make_scrape_record(STATUS_ERROR, title, detail=f"request failed: {e}", url=url)

    code = response.status_code
    if code in BLOCKED_STATUS_CODES:
        response.close()
        return make_scrape_record(
            STATUS_BLOCKED, title, http_status=code, detail=f"target returned {code}", url=url
        )
    if code >= 400:
        response.close()
        return make_scrape_record(
            STATUS_ERROR, title, http_status=code, detail=f"http error {code}", url=url
        )

    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if content_type and not any(allowed in content_type for allowed in ALLOWED_CONTENT_TYPES):
        response.close()
        return make_scrape_record(
            STATUS_ERROR,
            title,
            http_status=code,
            detail=f"unsupported content-type: {content_type}",
            url=url,
        )

    try:
        body = read_capped(response, max_bytes=MAX_DOWNLOAD_BYTES)
    finally:
        response.close()

    html = decode_capped(response, body)

    # Challenge interstitial detection
    if _looks_like_challenge(html):
        return make_scrape_record(
            STATUS_BLOCKED,
            title,
            http_status=code,
            detail="challenge interstitial detected (Cloudflare/CAPTCHA)",
            url=url,
        )

    extracted = extract_page_text(html)
    scrubbed = scrub_untrusted_text(extracted).strip()

    # Truncate content before fencing
    if len(scrubbed) > max_chars:
        scrubbed = scrubbed[:max_chars]

    # Mandatory security fence around untrusted clearweb content
    fenced = fence_untrusted(scrubbed, source_url=url, max_chars=max_chars + 300)

    return make_scrape_record(
        STATUS_OK,
        title,
        text=fenced,
        http_status=code,
        url=url,
    )


def scrape_clearweb_multiple(
    urls_data: List[Union[str, Dict]],
    max_workers: int = 5,
    max_return_chars: Optional[int] = None,
) -> List[Dict]:
    """Scrape multiple Clear Web pages concurrently."""
    if not urls_data:
        return []

    results: List[Dict] = []
    session = build_clearweb_session()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                scrape_clearweb_single,
                u,
                max_return_chars=max_return_chars,
                session=session,
            ): u
            for u in urls_data
        }
        for future in as_completed(future_map):
            try:
                res = future.result()
                results.append(res)
            except Exception as e:
                u = future_map[future]
                url, title = _normalize_url_data(u)
                results.append(
                    make_scrape_record(
                        STATUS_ERROR, title, detail=f"scrape exception: {e}", url=url
                    )
                )

    return results
