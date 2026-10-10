"""Unit tests for modules/search/clearweb.py."""

import unittest
from unittest.mock import MagicMock, patch

from modules.search.clearweb import _dedup_key, _filter_and_cap_results, search_clearweb


class TestClearSearch(unittest.TestCase):
    def test_dedup_key(self):
        k1 = _dedup_key("https://example.com/page/")
        k2 = _dedup_key("https://example.com/page")
        self.assertEqual(k1, k2)

        k3 = _dedup_key("https://EXAMPLE.COM/page")
        self.assertEqual(k1, k3)

    def test_filter_and_cap_results_deduplication(self):
        raw = [
            {"title": "Result 1", "href": "https://example.com/page", "body": "Snippet 1"},
            {"title": "Result 1 Duplicate", "href": "https://example.com/page/", "body": "Snippet 1 dup"},
            {"title": "Result 2", "href": "https://example.com/other", "body": "Snippet 2"},
        ]
        kept = _filter_and_cap_results(raw)
        self.assertEqual(len(kept), 2)
        self.assertEqual(kept[0]["title"], "Result 1")
        self.assertEqual(kept[1]["title"], "Result 2")

    def test_filter_and_cap_results_per_host_limit(self):
        raw = [
            {"title": f"Page {i}", "href": f"https://example.com/sub/{i}", "body": "..."}
            for i in range(10)
        ] + [
            {"title": "Other Host", "href": "https://other.org/page", "body": "..."}
        ]
        kept = _filter_and_cap_results(raw, per_host_limit=3)
        self.assertEqual(len(kept), 4)
        example_links = [k for k in kept if "example.com" in k["link"]]
        other_links = [k for k in kept if "other.org" in k["link"]]
        self.assertEqual(len(example_links), 3)
        self.assertEqual(len(other_links), 1)

    def test_filter_and_cap_results_csam_filtered(self):
        raw = [
            {"title": "Benign Threat Intel", "href": "https://example.com/advisory", "body": "CVE details"},
            {"title": "child porn illegal content", "href": "https://bad.com/illegal", "body": "bad"},
        ]
        kept = _filter_and_cap_results(raw)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["title"], "Benign Threat Intel")

    def test_search_clearweb_empty_query(self):
        self.assertEqual(search_clearweb(""), [])
        self.assertEqual(search_clearweb("   "), [])
        self.assertEqual(search_clearweb("\u200b\u200c"), [])

    def test_search_clearweb_with_injected_fn(self):
        def mock_search(q, max_res):
            return [
                {"title": f"Found: {q}", "href": "https://search.com/result", "body": "Body text"}
            ]

        results = search_clearweb("cyber security", max_results=5, search_fn=mock_search)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Found: cyber security")
        self.assertEqual(results[0]["engine"], "duckduckgo")
        self.assertEqual(results[0]["source"], "clearweb")

    def test_search_clearweb_handles_exception_gracefully(self):
        def failing_search(q, max_res):
            raise RuntimeError("Network down or rate limited")

        results = search_clearweb("test", search_fn=failing_search)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
