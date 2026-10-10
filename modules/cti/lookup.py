"""Lookup service for CTI knowledge base."""

import sqlite3
from typing import Any, Dict, List, Optional

from core.security import scrub_untrusted_text
from modules.cti.indexer import get_db_connection


def search_cti_records(
    keyword: str,
    category: Optional[str] = None,
    online_only: bool = False,
    limit: int = 20,
    conn: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """Search CTI knowledge base by keyword with relevance ranking.

    Prioritizes:
    1. Exact name match
    2. Name prefix match
    3. Name substring match
    4. Online links over offline/expired ones
    """
    cleaned_kw = scrub_untrusted_text(keyword).strip()
    if not cleaned_kw:
        return []

    db = conn or get_db_connection()

    sql = """
    SELECT id, name, category, status, is_online, url, extra_channels, description, source_file,
        CASE
            WHEN LOWER(name) = LOWER(?) THEN 1
            WHEN LOWER(name) LIKE LOWER(?) || '%' THEN 2
            WHEN LOWER(name) LIKE '%' || LOWER(?) || '%' THEN 3
            ELSE 4
        END AS match_rank
    FROM cti_records
    WHERE (
        name LIKE '%' || ? || '%'
        OR description LIKE '%' || ? || '%'
        OR extra_channels LIKE '%' || ? || '%'
        OR url LIKE '%' || ? || '%'
    )
    """
    params: List[Any] = [
        cleaned_kw,
        cleaned_kw,
        cleaned_kw,
        cleaned_kw,
        cleaned_kw,
        cleaned_kw,
        cleaned_kw,
    ]

    if category:
        sql += " AND category = ?"
        params.append(category.strip().lower())

    if online_only:
        sql += " AND is_online = 1"

    sql += " ORDER BY match_rank ASC, is_online DESC, name ASC LIMIT ?"
    params.append(int(limit))

    cur = db.execute(sql, params)
    rows = cur.fetchall()

    if not rows and " " in cleaned_kw:
        # Fallback to key meaningful tokens (e.g. 'LockBit' from 'LockBit ransomware leak site')
        tokens = [
            t for t in cleaned_kw.split()
            if len(t) >= 3 and t.lower() not in {"onion", "site", "leak", "links", "dark", "http", "https", "find", "threat", "gang", "group"}
        ]
        for tok in tokens:
            tok_params: List[Any] = [tok] * 7
            if category:
                tok_params.append(category.strip().lower())
            tok_params.append(int(limit))
            cur = db.execute(sql, tok_params)
            rows = cur.fetchall()
            if rows:
                break

    results = []
    for row in rows:
        d = dict(row)
        d["is_online"] = bool(d.get("is_online", 0))
        d.pop("match_rank", None)
        results.append(d)

    return results


def lookup_threat_actor(
    name: str,
    online_only: bool = False,
    limit: int = 15,
    conn: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """Convenience lookup for ransomware gangs and threat actors."""
    cleaned = scrub_untrusted_text(name).strip()
    if not cleaned:
        return []

    db = conn or get_db_connection()
    sql = """
    SELECT id, name, category, status, is_online, url, extra_channels, description, source_file,
        CASE
            WHEN LOWER(name) = LOWER(?) THEN 1
            WHEN LOWER(name) LIKE LOWER(?) || '%' THEN 2
            WHEN LOWER(name) LIKE '%' || LOWER(?) || '%' THEN 3
            ELSE 4
        END AS match_rank
    FROM cti_records
    WHERE (
        category IN ('ransomware', 'telegram_threat_actor', 'twitter_threat_actor')
        OR description LIKE '%ransomware%'
        OR description LIKE '%threat actor%'
    )
    AND (
        name LIKE '%' || ? || '%'
        OR description LIKE '%' || ? || '%'
        OR url LIKE '%' || ? || '%'
    )
    """
    params: List[Any] = [
        cleaned,
        cleaned,
        cleaned,
        cleaned,
        cleaned,
        cleaned,
    ]

    if online_only:
        sql += " AND is_online = 1"

    sql += " ORDER BY match_rank ASC, is_online DESC, name ASC LIMIT ?"
    params.append(int(limit))

    cur = db.execute(sql, params)
    results = []
    for row in cur.fetchall():
        d = dict(row)
        d["is_online"] = bool(d.get("is_online", 0))
        d.pop("match_rank", None)
        results.append(d)

    return results


def lookup_by_cve(
    cve_id: str,
    limit: int = 10,
    conn: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """Lookup exploited CVE entries and PoC links."""
    cleaned = scrub_untrusted_text(cve_id).strip()
    if not cleaned:
        return []

    db = conn or get_db_connection()
    sql = """
    SELECT id, name, category, status, is_online, url, extra_channels, description, source_file
    FROM cti_records
    WHERE category = 'cve'
      AND (name LIKE '%' || ? || '%' OR description LIKE '%' || ? || '%')
    ORDER BY name ASC LIMIT ?
    """
    cur = db.execute(sql, (cleaned, cleaned, int(limit)))
    results = []
    for row in cur.fetchall():
        d = dict(row)
        d["is_online"] = bool(d.get("is_online", 0))
        results.append(d)
    return results


def get_cti_stats(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Return summary statistics of the indexed CTI database."""
    db = conn or get_db_connection()

    total = db.execute("SELECT COUNT(*) FROM cti_records").fetchone()[0]
    online = db.execute("SELECT COUNT(*) FROM cti_records WHERE is_online = 1").fetchone()[0]
    offline = total - online

    cur = db.execute("SELECT category, COUNT(*) FROM cti_records GROUP BY category ORDER BY COUNT(*) DESC")
    by_category = {row[0]: row[1] for row in cur.fetchall()}

    return {
        "total_records": total,
        "online_records": online,
        "offline_records": offline,
        "categories": by_category,
    }


def format_cti_summary(records: List[Dict[str, Any]], max_chars: int = 4000) -> str:
    """Format CTI records into a readable Markdown block for LLM prompts or reports."""
    if not records:
        return "No CTI directory records found for this query."

    lines = ["### CTI Knowledge Base Directory Matches:"]
    for r in records:
        name = r.get("name") or "Unknown"
        cat = r.get("category") or "general"
        status = r.get("status") or "UNKNOWN"
        url = r.get("url") or ""
        desc = r.get("description") or ""
        extra = r.get("extra_channels") or ""

        status_tag = f"🟢 {status}" if r.get("is_online") else f"🔴 {status}"
        entry = [f"- **{name}** `[{cat}]` ({status_tag})"]
        if url:
            entry.append(f"  - URL: `{url}`")
        if desc:
            entry.append(f"  - Details: {desc}")
        if extra:
            entry.append(f"  - Channels: {extra}")

        block = "\n".join(entry)
        if sum(len(l) for l in lines) + len(block) + 50 > max_chars:
            lines.append(f"- *...and {len(records) - len(lines) + 1} more records omitted.*")
            break
        lines.append(block)

    return "\n".join(lines)
