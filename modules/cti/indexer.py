"""Indexer for deepdarkCTI markdown tables into SQLite with FTS5."""

import glob
import logging
import os
import re
import sqlite3
import threading
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "deepdarkCTI")
)

CATEGORY_MAP = {
    "commercial_services": "commercial_service",
    "counterfeit_goods": "counterfeit_goods",
    "cve_most_exploited": "cve",
    "defacement": "defacement",
    "discord": "discord",
    "exploits": "exploit",
    "forum": "forum",
    "maas": "maas",
    "malware_samples": "malware",
    "markets": "market",
    "others": "other",
    "phishing": "phishing",
    "ransomware_gang": "ransomware",
    "rat": "rat",
    "search_engines": "search_engine",
    "telegram_infostealer": "telegram_infostealer",
    "telegram_threat_actors": "telegram_threat_actor",
    "twitter": "twitter",
    "twitter_threat_actors": "twitter_threat_actor",
}

MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
URL_RE = re.compile(r"(?:https?|http)://[^\s<>\"\'|)]+")

_LOCK = threading.Lock()
_CACHED_CONN: Optional[sqlite3.Connection] = None


def clean_cell(val: Optional[str]) -> str:
    """Strip whitespace and backticks from cell content."""
    if not val:
        return ""
    return val.strip().strip("`")


def extract_name_and_url(name_val: str, link_val: str = "") -> Tuple[str, str]:
    """Extract display name and URL from table cells (markdown links or raw URLs)."""
    name_val = clean_cell(name_val)
    link_val = clean_cell(link_val)

    # 1. Try markdown link in name
    m = MD_LINK_RE.search(name_val)
    if m:
        name = m.group(1).strip()
        url = m.group(2).strip()
        rest = MD_LINK_RE.sub("", name_val).strip()
        if rest:
            name = f"{name} {rest}".strip()
        return name, url

    # 2. Try markdown link in link_val
    m = MD_LINK_RE.search(link_val)
    if m:
        url = m.group(2).strip()
        name = name_val or m.group(1).strip()
        return name, url

    # 3. Raw URL in link_val
    m = URL_RE.search(link_val)
    if m:
        url = m.group(0).strip()
        return name_val or url, url

    # 4. Raw URL in name_val
    m = URL_RE.search(name_val)
    if m:
        url = m.group(0).strip()
        name = name_val.replace(url, "").strip(" ()[]")
        return name or url, url

    return name_val, link_val


def normalize_status(raw_status: str, has_status_col: bool = True) -> Tuple[str, int]:
    """Normalize status string and return (status_string, is_online_flag)."""
    status_upper = (raw_status or "").upper().strip()
    if not has_status_col:
        return "ACTIVE", 1

    if "ONLINE" in status_upper or "VALID" in status_upper:
        return "ONLINE", 1
    if "OFFLINE" in status_upper:
        return "OFFLINE", 0
    if "EXPIRED" in status_upper:
        return "EXPIRED", 0
    if "REBRANDED" in status_upper or "SEIZED" in status_upper:
        return "SEIZED/REBRANDED", 0

    if not status_upper:
        return "UNKNOWN", 0

    return status_upper, 0


def parse_markdown_file(filepath: str) -> List[Dict[str, str]]:
    """Parse table rows from a deepdarkCTI markdown file into normalized record dicts."""
    filename = os.path.basename(filepath)
    stem = os.path.splitext(filename)[0]
    category = CATEGORY_MAP.get(stem, stem)

    if not os.path.exists(filepath):
        return []

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        lines = [line.strip() for line in f if line.strip()]

    table_lines = [l for l in lines if l.startswith("|")]
    if len(table_lines) < 2:
        return []

    # Parse headers from the first pipe line
    raw_headers = [c.strip().lower() for c in table_lines[0].strip("|").split("|")]

    start_idx = 1
    if start_idx < len(table_lines) and all(c in " -:|" for c in table_lines[1]):
        start_idx = 2

    records: List[Dict[str, str]] = []

    for row in table_lines[start_idx:]:
        raw_row = row.strip("|")
        cols = [c.strip() for c in raw_row.split("|")]
        if len(cols) < len(raw_headers):
            cols += [""] * (len(raw_headers) - len(cols))
        elif len(cols) > len(raw_headers):
            cols = cols[: len(raw_headers) - 1] + [" | ".join(cols[len(raw_headers) - 1 :])]

        row_dict = dict(zip(raw_headers, cols))

        # Determine field mappings based on header names
        name_candidate = (
            row_dict.get("name")
            or row_dict.get("threat actor name")
            or row_dict.get("vendor")
            or row_dict.get("cve")
            or ""
        )
        link_candidate = (
            row_dict.get("link")
            or row_dict.get("telegram")
            or row_dict.get("poc")
            or row_dict.get("url")
            or ""
        )

        # In case of CVE tables, combine Vendor + CVE
        if "cve" in row_dict and "vendor" in row_dict:
            vendor = row_dict.get("vendor", "")
            cve = row_dict.get("cve", "")
            name_candidate = f"{cve} ({vendor})".strip()

        name, url = extract_name_and_url(name_candidate, link_candidate)

        if not name and not url:
            continue

        raw_status = row_dict.get("status", "")
        has_status_col = "status" in row_dict
        status, is_online = normalize_status(raw_status, has_status_col=has_status_col)

        # Extra channels & descriptions
        extra_parts = []
        for key in ("tox id or other channel", "user:password", "rss feed", "extra"):
            val = row_dict.get(key)
            if val:
                extra_parts.append(f"{key}: {val}")
        extra_channels = " | ".join(extra_parts)

        desc_parts = []
        for key in ("description", "type", "type of attacks", "category"):
            val = row_dict.get(key)
            if val and val != name:
                desc_parts.append(val)
        description = " - ".join(desc_parts)

        records.append(
            {
                "name": name,
                "category": category,
                "status": status,
                "is_online": is_online,
                "url": url,
                "extra_channels": extra_channels,
                "description": description,
                "source_file": filename,
            }
        )

    return records


def init_schema(conn: sqlite3.Connection):
    """Create cti_records and FTS5 tables with indices."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cti_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            status TEXT NOT NULL,
            is_online INTEGER NOT NULL DEFAULT 0,
            url TEXT NOT NULL,
            extra_channels TEXT,
            description TEXT,
            source_file TEXT
        );
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cti_name ON cti_records(name COLLATE NOCASE);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cti_category ON cti_records(category);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cti_is_online ON cti_records(is_online);")

    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS cti_fts USING fts5(
            name, category, description, extra_channels,
            content='cti_records',
            content_rowid='id'
        );
        """
    )
    conn.commit()


def build_cti_database(
    data_dir: Optional[str] = None,
    db_path: str = ":memory:",
    force_rebuild: bool = False,
) -> sqlite3.Connection:
    """Scan data_dir, parse all markdown files, and populate the SQLite database."""
    target_dir = data_dir or DEFAULT_DATA_DIR
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    init_schema(conn)

    # Check if already populated
    cur = conn.execute("SELECT COUNT(*) FROM cti_records")
    count = cur.fetchone()[0]
    if count > 0 and not force_rebuild:
        return conn

    if force_rebuild and count > 0:
        conn.execute("DELETE FROM cti_records")
        conn.execute("DELETE FROM cti_fts")
        conn.commit()

    all_records: List[Dict[str, str]] = []
    md_files = sorted(glob.glob(os.path.join(target_dir, "*.md")))

    for md_path in md_files:
        try:
            recs = parse_markdown_file(md_path)
            all_records.extend(recs)
        except Exception as e:
            logger.warning("Failed to parse %s: %s", md_path, e)

    rows = [
        (
            r["name"],
            r["category"],
            r["status"],
            r["is_online"],
            r["url"],
            r["extra_channels"],
            r["description"],
            r["source_file"],
        )
        for r in all_records
    ]

    conn.executemany(
        """
        INSERT INTO cti_records (name, category, status, is_online, url, extra_channels, description, source_file)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.execute("INSERT INTO cti_fts(cti_fts) VALUES('rebuild')")
    conn.commit()

    logger.info("Indexed %d CTI records into %s", len(rows), db_path)
    return conn


def get_db_connection(
    data_dir: Optional[str] = None,
    force_rebuild: bool = False,
) -> sqlite3.Connection:
    """Return a thread-safe singleton connection to the in-memory CTI database."""
    global _CACHED_CONN
    with _LOCK:
        if _CACHED_CONN is None or force_rebuild:
            _CACHED_CONN = build_cti_database(
                data_dir=data_dir,
                db_path=":memory:",
                force_rebuild=force_rebuild,
            )
        return _CACHED_CONN
