"""Unit tests for modules/cti (indexer and lookup)."""

import os
import tempfile
import unittest

from modules.cti.indexer import (
    build_cti_database,
    clean_cell,
    extract_name_and_url,
    get_db_connection,
    normalize_status,
    parse_markdown_file,
)
from modules.cti.lookup import (
    format_cti_summary,
    get_cti_stats,
    lookup_by_cve,
    lookup_threat_actor,
    search_cti_records,
)


class TestCTIIndexerHelpers(unittest.TestCase):
    def test_clean_cell(self):
        self.assertEqual(clean_cell("  `hello`  "), "hello")
        self.assertEqual(clean_cell(None), "")
        self.assertEqual(clean_cell(""), "")

    def test_extract_name_and_url(self):
        # Markdown link in name
        name, url = extract_name_and_url("[LockBit 3.0](http://lockbit.onion)")
        self.assertEqual(name, "LockBit 3.0")
        self.assertEqual(url, "http://lockbit.onion")

        # Markdown link with extra text
        name, url = extract_name_and_url("[Akira](http://akira.onion) (mirror)")
        self.assertEqual(name, "Akira (mirror)")
        self.assertEqual(url, "http://akira.onion")

        # Separate Link column
        name, url = extract_name_and_url("BreachForums", "https://breachforums.is")
        self.assertEqual(name, "BreachForums")
        self.assertEqual(url, "https://breachforums.is")

        # Raw URL in name
        name, url = extract_name_and_url("http://raw.onion")
        self.assertEqual(url, "http://raw.onion")

    def test_normalize_status(self):
        self.assertEqual(normalize_status("ONLINE"), ("ONLINE", 1))
        self.assertEqual(normalize_status("VALID"), ("ONLINE", 1))
        self.assertEqual(normalize_status("OFFLINE"), ("OFFLINE", 0))
        self.assertEqual(normalize_status("EXPIRED"), ("EXPIRED", 0))
        self.assertEqual(normalize_status("REBRANDED TO X"), ("SEIZED/REBRANDED", 0))
        self.assertEqual(normalize_status("", has_status_col=False), ("ACTIVE", 1))
        self.assertEqual(normalize_status(""), ("UNKNOWN", 0))


class TestCTITableParser(unittest.TestCase):
    def test_parse_markdown_file(self):
        sample_md = """
|Name|Status|Description|
| --- | --- | --- |
|[TestGroup](http://testgroup.onion)|ONLINE|Ransomware group|
|[DeadGroup](http://deadgroup.onion)|OFFLINE|Seized forum|
        """.strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "ransomware_gang.md")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(sample_md)

            records = parse_markdown_file(file_path)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["name"], "TestGroup")
            self.assertEqual(records[0]["url"], "http://testgroup.onion")
            self.assertEqual(records[0]["status"], "ONLINE")
            self.assertEqual(records[0]["is_online"], 1)
            self.assertEqual(records[0]["category"], "ransomware")

            self.assertEqual(records[1]["name"], "DeadGroup")
            self.assertEqual(records[1]["is_online"], 0)


class TestCTILookup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Build in-memory DB from mock markdown files for deterministic tests
        cls.tmpdir = tempfile.TemporaryDirectory()
        mock_ransom = """
|Name|Status|User:Password|Tox ID or other channel|RSS Feed|
| --- | --- | --- | --- | --- |
|[AlphaRansom](http://alpha.onion)|ONLINE||Tox: 12345||
|[AlphaRansom Mirror](http://alpha2.onion)|OFFLINE||||
|[BetaGang](http://beta.onion)|ONLINE||||
        """.strip()
        with open(os.path.join(cls.tmpdir.name, "ransomware_gang.md"), "w") as f:
            f.write(mock_ransom)

        mock_cve = """
|Vendor|CVE|Type|PoC|
| --- | --- | --- | --- |
|Microsoft|CVE-2024-9999|RCE|https://github.com/poc/cve-2024-9999|
        """.strip()
        with open(os.path.join(cls.tmpdir.name, "cve_most_exploited.md"), "w") as f:
            f.write(mock_cve)

        cls.conn = build_cti_database(data_dir=cls.tmpdir.name, db_path=":memory:")

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmpdir.cleanup()

    def test_search_cti_records_exact_and_ranking(self):
        results = search_cti_records("AlphaRansom", conn=self.conn)
        self.assertGreaterEqual(len(results), 2)
        # Exact match AlphaRansom should be first
        self.assertEqual(results[0]["name"], "AlphaRansom")
        self.assertTrue(results[0]["is_online"])
        self.assertEqual(results[0]["url"], "http://alpha.onion")

    def test_search_cti_records_online_only(self):
        all_res = search_cti_records("AlphaRansom", online_only=False, conn=self.conn)
        online_res = search_cti_records("AlphaRansom", online_only=True, conn=self.conn)
        self.assertEqual(len(all_res), 2)
        self.assertEqual(len(online_res), 1)
        self.assertEqual(online_res[0]["name"], "AlphaRansom")

    def test_search_cti_records_category_filter(self):
        res = search_cti_records("AlphaRansom", category="ransomware", conn=self.conn)
        self.assertEqual(len(res), 2)
        no_res = search_cti_records("AlphaRansom", category="forum", conn=self.conn)
        self.assertEqual(len(no_res), 0)

    def test_lookup_threat_actor(self):
        res = lookup_threat_actor("BetaGang", conn=self.conn)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["name"], "BetaGang")

    def test_lookup_by_cve(self):
        res = lookup_by_cve("CVE-2024-9999", conn=self.conn)
        self.assertEqual(len(res), 1)
        self.assertIn("CVE-2024-9999", res[0]["name"])
        self.assertEqual(res[0]["url"], "https://github.com/poc/cve-2024-9999")

    def test_get_cti_stats(self):
        stats = get_cti_stats(conn=self.conn)
        self.assertEqual(stats["total_records"], 4)
        self.assertEqual(stats["online_records"], 3)
        self.assertEqual(stats["offline_records"], 1)
        self.assertIn("ransomware", stats["categories"])
        self.assertIn("cve", stats["categories"])

    def test_format_cti_summary(self):
        results = search_cti_records("AlphaRansom", conn=self.conn)
        summary = format_cti_summary(results)
        self.assertIn("### CTI Knowledge Base Directory Matches:", summary)
        self.assertIn("AlphaRansom", summary)
        self.assertIn("http://alpha.onion", summary)

    def test_empty_and_scrubbed_query(self):
        self.assertEqual(search_cti_records("", conn=self.conn), [])
        self.assertEqual(search_cti_records("   ", conn=self.conn), [])
        # Zero width space only
        self.assertEqual(search_cti_records("\u200b\u200c", conn=self.conn), [])


class TestCTIRealDataset(unittest.TestCase):
    """Smoke test on actual data/deepdarkCTI dataset."""

    def test_real_dataset_is_indexed_and_searchable(self):
        conn = get_db_connection()
        stats = get_cti_stats(conn=conn)
        self.assertGreater(stats["total_records"], 2000)
        self.assertGreater(stats["online_records"], 1000)

        # Search for well-known ransomware gang
        res = search_cti_records("Akira", conn=conn)
        self.assertTrue(any("akira" in r["name"].lower() for r in res))


if __name__ == "__main__":
    unittest.main()
