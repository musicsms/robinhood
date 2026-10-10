"""Base scraping utilities, streaming body capping, charset decoding, and HTML text extraction."""

import codecs
import re
from typing import Dict, List, Optional
from bs4 import BeautifulSoup
from requests.compat import chardet

BODY_CHUNK_BYTES = 8192
MAX_DOWNLOAD_BYTES = 1_000_000
MAX_EXTRACTED_TEXT_CHARS = 50_000
MAX_RETURN_CHARS = 8_000
MIN_STRIPPED_RATIO = 0.25
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
BOILERPLATE_TAGS = ["nav", "header", "footer", "aside"]

_DETECT_SAMPLE_BYTES = 64 * 1024
_HEADER_CHARSET_RE = re.compile(r"""charset\s*=\s*["']?\s*([^"';\s]+)""", re.IGNORECASE)
_META_CHARSET_RE = re.compile(
    rb"""<meta[^>]*?charset\s*=\s*["']?\s*([A-Za-z0-9._:-]+)""", re.IGNORECASE
)
_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def read_capped(response, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
    """Read at most `max_bytes` of a streamed response body."""
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


def _codec(name: str) -> Optional[str]:
    try:
        return codecs.lookup(name).name
    except (LookupError, TypeError):
        return None


def decode_capped(response, body: bytes) -> str:
    """Decode a capped response body with robust charset detection."""
    header = _HEADER_CHARSET_RE.search(response.headers.get("Content-Type") or "")
    encoding = _codec(header.group(1)) if header else None
    if not encoding:
        encoding = next((name for bom, name in _BOMS if body.startswith(bom)), None)
    if not encoding:
        meta = _META_CHARSET_RE.search(body[:_DETECT_SAMPLE_BYTES])
        encoding = _codec(meta.group(1).decode("ascii")) if meta else None
    if not encoding:
        try:
            return codecs.getincrementaldecoder("utf-8")().decode(body)
        except UnicodeDecodeError:
            pass
        if chardet is not None:
            encoding = (chardet.detect(body[:_DETECT_SAMPLE_BYTES]) or {}).get("encoding")
    try:
        return body.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def extract_page_text(html: str) -> str:
    """Return a page's readable text with boilerplate furniture (nav/header/footer) removed."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style"]):
        tag.extract()
    full_text = " ".join(soup.get_text(separator=" ").split())
    for tag in soup(BOILERPLATE_TAGS):
        tag.extract()
    text = " ".join(soup.get_text(separator=" ").split())
    if len(text) < MIN_STRIPPED_RATIO * len(full_text):
        return full_text
    return text


def make_scrape_record(
    status: str,
    title: str,
    text: str = "",
    http_status: Optional[int] = None,
    detail: str = "",
    links: Optional[List[str]] = None,
    url: str = "",
) -> Dict:
    """Build standardized scrape outcome dict."""
    return {
        "status": status,
        "title": title,
        "text": text,
        "http_status": http_status,
        "detail": detail,
        "links": list(links or []),
        "url": url,
    }
