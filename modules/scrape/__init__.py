"""Clearweb and base scraping package."""

from modules.scrape.base import (
    ALLOWED_CONTENT_TYPES,
    BOILERPLATE_TAGS,
    MAX_DOWNLOAD_BYTES,
    MAX_EXTRACTED_TEXT_CHARS,
    MAX_RETURN_CHARS,
    decode_capped,
    extract_page_text,
    read_capped,
)
from modules.scrape.clearweb import scrape_clearweb_multiple, scrape_clearweb_single

__all__ = [
    "MAX_DOWNLOAD_BYTES",
    "MAX_EXTRACTED_TEXT_CHARS",
    "MAX_RETURN_CHARS",
    "ALLOWED_CONTENT_TYPES",
    "BOILERPLATE_TAGS",
    "read_capped",
    "decode_capped",
    "extract_page_text",
    "scrape_clearweb_single",
    "scrape_clearweb_multiple",
]
