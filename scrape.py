import codecs
import functools
import random
import re
import sys
import unicodedata
import requests
import threading
import logging
from requests.adapters import HTTPAdapter
from requests.compat import chardet
from urllib3.util import parse_url
from urllib3.util.retry import Retry
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

import warnings
warnings.filterwarnings("ignore")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.7; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Mozilla/5.0 (X11; Linux i686; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.3179.54",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.3179.54"
]

# The one way out of this process. There is no direct session: a clearweb fetch
# is still a Tor fetch, only the host policy differs.
TOR_PROXIES = {
    "http": "socks5h://127.0.0.1:9050",
    "https": "socks5h://127.0.0.1:9050",
}

# Connect and read timeouts. One 30-second connect attempt, as for the search
# engines: a dead onion does not answer on a retry, and a slow one needs the time.
TOR_TIMEOUT = (30, 45)

MAX_DOWNLOAD_BYTES = 1_000_000
MAX_EXTRACTED_TEXT_CHARS = 50_000

# If stripping structural tags leaves less than this fraction of the page, the
# strip is assumed to have eaten the content and is discarded.
MIN_STRIPPED_RATIO = 0.25
# Default characters per scraped page handed to the LLM, which sets how much of
# a page the summarizer reads. A page is downloaded up to MAX_DOWNLOAD_BYTES,
# extracted up to MAX_EXTRACTED_TEXT_CHARS, then trimmed to this.
MAX_RETURN_CHARS = 8_000
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")

# Onion links kept from one page's anchors. A directory of leak sites is a list
# of names until its hrefs come with it: extraction throws anchors away, so a
# host reading the text sees "LockBit" and no way to reach it.
MAX_PAGE_LINKS = 20

from core.security import (
    BLOCKED_STATUS_CODES,
    MAX_LINK_CHARS,
    MAX_REDIRECTS,
    REDIRECT_CODES,
    STATUS_BLOCKED,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_REFUSED_NOT_ONION,
    TRUNCATION_MARK,
    UNTRUSTED_CLOSE,
    UNTRUSTED_HEADER,
    UNTRUSTED_OPEN_PREFIX,
    RedirectRefused,
    UrlRefused,
    _AUTHORITY_END_RE,
    _CHALLENGE_MARKERS,
    _CONTROL_RANGES,
    _DEFAULT_IGNORABLE_RANGES,
    _DEFANGED,
    _SENTINEL_RE,
    _defang_sentinels,
    _fence_parts,
    _fold_char,
    _looks_like_challenge,
    _outbound_host,
    _scrub_table,
    fence_overhead,
    fence_untrusted,
    is_onion,
    scrub_untrusted_text,
    url_policy_problem as _core_url_policy_problem,
)


def url_policy_problem(url, allow_clearweb=False):
    """Why Robin will not request `url`, as ``(status, detail)``, or None.

    Applies to the first URL and every redirect hop: http or https only, no
    ambiguous host even with `allow_clearweb`, and no clearweb host without it.
    """
    return _core_url_policy_problem(url, allow_clearweb=allow_clearweb, is_onion_fn=is_onion)


_thread_local = threading.local()
_logger = logging.getLogger(__name__)


def _build_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        read=3,
        connect=0,
        backoff_factor=0.3,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "HEAD"]),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.proxies = dict(TOR_PROXIES)
    # With trust_env on, requests merges HTTP_PROXY / HTTPS_PROXY / ALL_PROXY
    # over session.proxies, so a proxy variable (Docker's client proxy config
    # injects them) would route requests around Tor.
    session.trust_env = False
    session.get_redirect_target = no_redirect_target
    return session


def no_redirect_target(response):
    """Stands in for `Session.get_redirect_target` on every Tor session."""
    return None


def _get_session():
    """The calling thread's pooled Tor session, built on first use."""
    if not hasattr(_thread_local, "tor_session"):
        _thread_local.tor_session = _build_session()
    return _thread_local.tor_session


# URL policy, onion checking, and redirect exceptions are imported from core.security.


BODY_CHUNK_BYTES = 8192


def read_capped(response, max_bytes):
    """At most `max_bytes` of a streamed response's body, and no more read.

    Every body on a Tor path is read this way, BODY_CHUNK_BYTES at a time, so an
    endless response costs about the cap. The caller still closes the response.
    """
    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=BODY_CHUNK_BYTES):
        if not chunk:
            continue
        room = max_bytes - total
        if len(chunk) >= room:
            chunks.append(chunk[:room])
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


# How much of a capped body the charset search and detection look at.
_DETECT_SAMPLE_BYTES = 64 * 1024

_HEADER_CHARSET_RE = re.compile(r"""charset\s*=\s*["']?\s*([^"';\s]+)""", re.IGNORECASE)
_META_CHARSET_RE = re.compile(rb"""<meta[^>]*?charset\s*=\s*["']?\s*([A-Za-z0-9._:-]+)""",
                              re.IGNORECASE)
_BOMS = ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"),
         (codecs.BOM_UTF16_BE, "utf-16"))


def _codec(name):
    try:
        return codecs.lookup(name).name
    except (LookupError, TypeError):
        return None


def decode_capped(response, body):
    """Text of a body read with `read_capped`.

    Charset from Content-Type, else a BOM, a <meta> charset, valid UTF-8, then
    detection; requests would assume ISO-8859-1 and garble undeclared UTF-8.
    """
    header = _HEADER_CHARSET_RE.search(response.headers.get("Content-Type") or "")
    encoding = _codec(header.group(1)) if header else None
    if not encoding:
        encoding = next((name for bom, name in _BOMS if body.startswith(bom)), None)
    if not encoding:
        meta = _META_CHARSET_RE.search(body[:_DETECT_SAMPLE_BYTES])
        encoding = _codec(meta.group(1).decode("ascii")) if meta else None
    if not encoding:
        try:
            # Incremental, so a character the byte cap cut in half is dropped
            # rather than failing the whole page.
            return codecs.getincrementaldecoder("utf-8")().decode(body)
        except UnicodeDecodeError:
            pass
        if chardet is not None:
            encoding = (chardet.detect(body[:_DETECT_SAMPLE_BYTES]) or {}).get("encoding")
    try:
        return body.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _location(response):
    """The Location header as text. Like requests, undo the latin-1 decoding
    http.client applies, so a UTF-8 path survives."""
    location = (response.headers.get("Location") or "").strip()
    try:
        return location.encode("latin1").decode("utf8")
    except UnicodeError:
        return location


def get_over_tor(session, url, allow_clearweb=False, max_redirects=MAX_REDIRECTS, **kwargs):
    """GET `url`, following redirects by hand under the same host policy.

    Returns ``(response, final_url)`` with the body unread; raises UrlRefused
    when the URL, a hop or the length of the chain is refused.
    """
    # Every hop is streamed, whatever the caller asked for: without it,
    # Session.send reads each response body in full (`if not stream:
    # r.content`), redirects included.
    kwargs.pop("stream", None)
    # The first URL gets the same check as every hop, so an engine helper
    # cannot be handed a clearweb endpoint and request it.
    problem = url_policy_problem(url, allow_clearweb)
    if problem:
        status, detail = problem
        raise UrlRefused(status, "refused {}: {}".format(url, detail))
    current = url
    for _ in range(max_redirects + 1):
        response = session.get(current, allow_redirects=False, stream=True, **kwargs)
        is_redirect = response.status_code in REDIRECT_CODES
        location = _location(response) if is_redirect else ""
        if not location:
            return response, current
        target = urljoin(current, location)
        code = response.status_code
        response.close()
        problem = url_policy_problem(target, allow_clearweb)
        if problem:
            status, detail = problem
            raise RedirectRefused(
                status, "redirect from {} to {} refused: {}".format(current, target, detail),
                http_status=code)
        current = target
    raise RedirectRefused(
        STATUS_ERROR, "too many redirects (more than {}) starting at {}".format(max_redirects, url))


# Untrusted-data handling, scrubbing, and fencing are imported from core.security.


def _normalize_url_data(url_data):
    if not isinstance(url_data, dict):
        return "", "Untitled"
    url = str(url_data.get("link") or "").strip()
    title = scrub_untrusted_text(str(url_data.get("title") or "Untitled")).strip() or "Untitled"
    return url, title


# Challenge interstitial markers are imported from core.security.


# Menus, banners and footers are furniture that wastes context and invites the
# summarizer to describe a site's navigation. <form> is deliberately absent:
# phpBB and SMF boards wrap their whole topic listing in one.
BOILERPLATE_TAGS = ["nav", "header", "footer", "aside"]


def extract_page_text(html):
    """Return a page's readable text with structural furniture removed.

    Falls back to the unstripped text when the strip leaves too little of it
    (see MIN_STRIPPED_RATIO).
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.extract()
    full_text = ' '.join(soup.get_text(separator=' ').split())
    for tag in soup(BOILERPLATE_TAGS):
        tag.extract()
    text = ' '.join(soup.get_text(separator=' ').split())
    if len(text) < MIN_STRIPPED_RATIO * len(full_text):
        return full_text
    return text


# An onion address as a page writes it (56 base32 characters, or 16 in the
# retired format), with or without a scheme and path. Click-counting
# directories put a redirector in the href and the real address in the text.
ONION_IN_TEXT = re.compile(
    r"(?:https?://)?([a-z2-7]{56}|[a-z2-7]{16})\.onion(/[^\s\"'<>)\]]*)?",
    re.IGNORECASE)


def extract_onion_links(html, base_url=""):
    """Onion URLs this page's anchors point at, other hosts before its own."""
    soup = BeautifulSoup(html or "", "html.parser")
    page_host = (urlparse(base_url or "").hostname or "").lower()
    seen, off_host, same_host = set(), [], []

    def keep(url):
        url = scrub_untrusted_text(url).split("#", 1)[0][:MAX_LINK_CHARS]
        if not is_onion(url):
            return
        key = url.rstrip("/").lower()
        if key in seen:
            return
        seen.add(key)
        host = (urlparse(url).hostname or "").lower()
        (same_host if host == page_host else off_host).append(url)

    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if href and not href.startswith("#"):
            try:
                keep(urljoin(base_url or "", href))
            except ValueError:
                pass
        for match in ONION_IN_TEXT.finditer(" ".join(anchor.get_text().split())):
            keep("http://{}.onion{}".format(match.group(1).lower(),
                                            match.group(2) or ""))

    return (off_host + same_host)[:MAX_PAGE_LINKS]


def _record(status, title, text="", http_status=None, detail="", links=None):
    return {
        "status": status,
        "title": title,
        "text": text,
        "http_status": http_status,
        "detail": detail,
        "links": list(links or []),
    }


def scrape_single_detailed(url_data, allow_clearweb=False):
    """Scrape one URL over Tor and return ``(url, record)``.

    record has ``status`` (a STATUS_* constant), ``title``, ``text``,
    ``http_status``, ``detail`` and ``links``; ``text`` is empty unless STATUS_OK.
    """
    url, title = _normalize_url_data(url_data)
    if not url:
        return "", _record(STATUS_ERROR, title, detail="missing link")

    problem = url_policy_problem(url, allow_clearweb)
    if problem:
        status, detail = problem
        _logger.warning("Refused %s (%s)", url, detail)
        return url, _record(status, title, detail=detail)

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
    }

    response = None
    try:
        session = _get_session()
        try:
            response, final_url = get_over_tor(
                session, url, allow_clearweb=allow_clearweb,
                headers=headers, timeout=TOR_TIMEOUT, stream=True)
        except UrlRefused as refused:
            _logger.warning("Stopped following redirects for %s: %s", url, refused.detail)
            return url, _record(refused.status, title, http_status=refused.http_status,
                                detail=refused.detail)
        clearweb = not is_onion(final_url)

        if response.status_code in BLOCKED_STATUS_CODES:
            _logger.info("Blocked by target url=%s http=%s", url, response.status_code)
            return url, _record(
                STATUS_BLOCKED, title, http_status=response.status_code,
                detail="http {}".format(response.status_code),
            )

        if response.status_code != 200:
            return url, _record(
                STATUS_ERROR, title, http_status=response.status_code,
                detail="http {}".format(response.status_code),
            )

        content_type = (response.headers.get("Content-Type") or "").lower()
        if content_type and not any(t in content_type for t in ALLOWED_CONTENT_TYPES):
            return url, _record(
                STATUS_ERROR, title, http_status=200,
                detail="unsupported content type: {}".format(content_type),
            )

        html = decode_capped(response, read_capped(response, MAX_DOWNLOAD_BYTES))

        if clearweb and _looks_like_challenge(html):
            _logger.info("Challenge interstitial instead of a page url=%s", url)
            return url, _record(STATUS_BLOCKED, title, http_status=200, detail="challenge")

        # Collapse whitespace again after scrubbing: str.split does not treat a
        # zero-width space as whitespace, so a word made only of them survives
        # extract_page_text's collapse and scrubs down to a run of spaces.
        text = " ".join(scrub_untrusted_text(extract_page_text(html)).split())
        text = text[:MAX_EXTRACTED_TEXT_CHARS]
        if not text:
            return url, _record(STATUS_ERROR, title, http_status=200, detail="empty page")

        # Links come from the page as served, so a relative href resolves
        # against where it was actually read from rather than where the
        # request started.
        links = extract_onion_links(html, final_url or url)
        return url, _record(STATUS_OK, title, text="{} - {}".format(title, text),
                            http_status=200, links=links)
    except Exception as exc:
        _logger.debug("Failed to scrape url=%s: %s", url, exc)
        return url, _record(STATUS_ERROR, title, detail=str(exc)[:200])
    finally:
        if response is not None:
            response.close()


def _truncate(content, max_return_chars):
    if len(content) <= max_return_chars:
        return content
    suffix = TRUNCATION_MARK
    if len(suffix) >= max_return_chars:
        return suffix[:max_return_chars]
    return content[:max_return_chars - len(suffix)] + suffix


def scrape_multiple_detailed(urls_data, max_workers=5, max_return_chars=None,
                             allow_clearweb=False):
    """Scrape many URLs concurrently and report the outcome of each.

    Returns ``{url: record}`` for every URL passed in, refused and blocked ones
    included, so a caller can say what happened to each.
    """
    max_return_chars = MAX_RETURN_CHARS if max_return_chars is None else max(500, int(max_return_chars))
    results = {}
    max_workers = max(1, min(int(max_workers), 16))
    if not isinstance(urls_data, (list, tuple)):
        return results

    unique_urls_data = []
    seen_links = set()
    for item in urls_data:
        url, title = _normalize_url_data(item)
        if not url or url in seen_links:
            continue
        seen_links.add(url)
        unique_urls_data.append({"link": url, "title": title})

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {
            executor.submit(scrape_single_detailed, url_data, allow_clearweb): url_data
            for url_data in unique_urls_data
        }
        for future in as_completed(future_to_url):
            submitted = future_to_url[future]
            try:
                url, record = future.result()
                if not url:
                    continue
                if record["status"] == STATUS_OK:
                    record["text"] = _truncate(record["text"], max_return_chars)
                results[url] = record
            except Exception as exc:
                _logger.debug("Worker failed to scrape a URL: %s", exc)
                url, title = _normalize_url_data(submitted)
                if url:
                    results[url] = _record(STATUS_ERROR, title, detail=str(exc)[:200])
                continue

    return results


def scrape_multiple(urls_data, max_workers=5, max_return_chars=None, allow_clearweb=False):
    """Scrape many URLs concurrently: ``{url: text}`` for those with STATUS_OK."""
    detailed = scrape_multiple_detailed(
        urls_data, max_workers=max_workers, max_return_chars=max_return_chars,
        allow_clearweb=allow_clearweb,
    )
    return {
        url: record["text"]
        for url, record in detailed.items()
        if record["status"] == STATUS_OK
    }
