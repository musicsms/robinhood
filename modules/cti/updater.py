"""Auto-sync updater for deepdarkCTI dataset from upstream GitHub repository."""

import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional
import urllib.request
import urllib.error

from modules.cti.indexer import DEFAULT_DATA_DIR, get_db_connection

logger = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com/repos/fastfire/deepdarkCTI/contents"
RAW_BASE_URL = "https://raw.githubusercontent.com/fastfire/deepdarkCTI/main"

KNOWN_CTI_FILES = [
    "commercial_services.md",
    "counterfeit_goods.md",
    "cve_most_exploited.md",
    "defacement.md",
    "discord.md",
    "exploits.md",
    "forum.md",
    "maas.md",
    "malware_samples.md",
    "markets.md",
    "methods.md",
    "others.md",
    "phishing.md",
    "ransomware_gang.md",
    "rat.md",
    "search_engines.md",
    "telegram_infostealer.md",
    "telegram_threat_actors.md",
    "twitter.md",
    "twitter_threat_actors.md",
]


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def fetch_file_content(
    filename: str,
    raw_base_url: str = RAW_BASE_URL,
    timeout: int = 15,
) -> Optional[bytes]:
    """Fetch raw markdown file content from GitHub raw endpoint."""
    url = f"{raw_base_url.rstrip('/')}/{filename}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Robin-CTI-Updater/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return resp.read()
    except Exception as e:
        logger.warning("Failed to fetch %s from %s: %s", filename, url, e)
    return None


def list_upstream_files(timeout: int = 15) -> List[str]:
    """List markdown files in upstream repo via GitHub API, falling back to known list."""
    req = urllib.request.Request(
        GITHUB_API_URL,
        headers={
            "User-Agent": "Robin-CTI-Updater/1.0",
            "Accept": "application/vnd.github.v3+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                files = [
                    item["name"]
                    for item in data
                    if isinstance(item, dict)
                    and item.get("name", "").endswith(".md")
                    and item.get("type") == "file"
                ]
                if files:
                    return files
    except Exception as e:
        logger.info("GitHub API contents listing failed (%s), using known list", e)
    return list(KNOWN_CTI_FILES)


def sync_cti_dataset(
    target_dir: Optional[str] = None,
    files_to_sync: Optional[List[str]] = None,
    timeout: int = 15,
    fetch_fn=None,
) -> Dict[str, Any]:
    """Sync CTI markdown files from upstream repository and rebuild the search index.

    Args:
        target_dir: Destination folder for markdown files. Defaults to data/deepdarkCTI.
        files_to_sync: Optional list of filenames. If omitted, queries upstream or uses KNOWN_CTI_FILES.
        timeout: Network timeout in seconds.
        fetch_fn: Custom fetch callable(filename) -> bytes for testing/injection.

    Returns:
        Summary dict containing updated, unchanged, and failed filenames along with record count.
    """
    out_dir = os.path.abspath(target_dir or DEFAULT_DATA_DIR)
    os.makedirs(out_dir, exist_ok=True)

    fetcher = fetch_fn or (lambda fn: fetch_file_content(fn, timeout=timeout))

    if files_to_sync is None:
        files = list_upstream_files(timeout=timeout)
    else:
        files = files_to_sync

    updated: List[str] = []
    unchanged: List[str] = []
    failed: List[str] = []

    for fname in files:
        if not fname.endswith(".md"):
            continue

        local_path = os.path.join(out_dir, fname)
        remote_data = fetcher(fname)

        if remote_data is None:
            failed.append(fname)
            continue

        local_hash = ""
        if os.path.exists(local_path):
            try:
                with open(local_path, "rb") as f:
                    local_hash = _sha256(f.read())
            except OSError:
                pass

        remote_hash = _sha256(remote_data)

        if local_hash == remote_hash:
            unchanged.append(fname)
        else:
            try:
                with open(local_path, "wb") as f:
                    f.write(remote_data)
                updated.append(fname)
            except OSError as e:
                logger.error("Failed to write %s: %s", local_path, e)
                failed.append(fname)

    # Rebuild in-memory FTS5 index if any files changed
    conn = get_db_connection(data_dir=out_dir, force_rebuild=bool(updated))
    cur = conn.execute("SELECT COUNT(*) FROM cti_records")
    total_records = cur.fetchone()[0]

    return {
        "status": "ok",
        "updated_files": updated,
        "unchanged_files": unchanged,
        "failed_files": failed,
        "total_records": total_records,
        "target_dir": out_dir,
    }


if __name__ == "__main__":
    import sys
    print("Syncing CTI Knowledge Base from fastfire/deepdarkCTI upstream...")
    result = sync_cti_dataset()
    print(f"Status: {result['status']}")
    print(f"Updated: {len(result['updated_files'])} files {result['updated_files']}")
    print(f"Unchanged: {len(result['unchanged_files'])} files")
    print(f"Failed: {len(result['failed_files'])} files {result['failed_files']}")
    print(f"Total CTI Records Indexed: {result['total_records']}")
