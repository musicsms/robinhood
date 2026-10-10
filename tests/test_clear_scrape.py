"""Unit tests for modules/scrape/clearweb.py."""

import unittest
from unittest.mock import MagicMock

import requests
from core.security import STATUS_BLOCKED, STATUS_ERROR, STATUS_OK, UNTRUSTED_OPEN_PREFIX
from modules.scrape.clearweb import scrape_clearweb_multiple, scrape_clearweb_single


class TestClearScrape(unittest.TestCase):
    def _create_mock_response(
        self,
        status_code=200,
        content=b"<html><body><h1>Test Page</h1><p>Hello clearweb world</p></body></html>",
        content_type="text/html; charset=utf-8",
    ):
        resp = MagicMock(spec=requests.Response)
        resp.status_code = status_code
        resp.headers = {"Content-Type": content_type}
        resp.iter_content = lambda chunk_size=8192: [content]
        resp.close = MagicMock()
        return resp

    def test_scrape_clearweb_single_success(self):
        mock_session = MagicMock()
        mock_session.get.return_value = self._create_mock_response(
            200, b"<html><head><title>Legit Title</title></head><body><h1>Content</h1><p>Article body</p></body></html>"
        )

        res = scrape_clearweb_single(
            {"link": "https://example.com/article", "title": "Passed Title"},
            session=mock_session,
        )

        self.assertEqual(res["status"], STATUS_OK)
        self.assertEqual(res["title"], "Passed Title")
        self.assertIn("Article body", res["text"])
        self.assertIn(UNTRUSTED_OPEN_PREFIX, res["text"])
        self.assertIn('source="https://example.com/article"', res["text"])

    def test_scrape_clearweb_single_detects_challenge(self):
        mock_session = MagicMock()
        challenge_html = b"<html><head><title>Attention Required! | Cloudflare</title></head><body>cf-browser-verification</body></html>"
        mock_session.get.return_value = self._create_mock_response(200, challenge_html)

        res = scrape_clearweb_single("https://cloudflare-protected.com", session=mock_session)
        self.assertEqual(res["status"], STATUS_BLOCKED)
        self.assertIn("challenge interstitial", res["detail"])

    def test_scrape_clearweb_single_blocked_status_codes(self):
        mock_session = MagicMock()
        for code in (403, 429, 503):
            mock_session.get.return_value = self._create_mock_response(code, b"Forbidden")
            res = scrape_clearweb_single("https://example.com", session=mock_session)
            self.assertEqual(res["status"], STATUS_BLOCKED)
            self.assertEqual(res["http_status"], code)

    def test_scrape_clearweb_single_error_status_codes(self):
        mock_session = MagicMock()
        for code in (404, 500):
            mock_session.get.return_value = self._create_mock_response(code, b"Error")
            res = scrape_clearweb_single("https://example.com", session=mock_session)
            self.assertEqual(res["status"], STATUS_ERROR)
            self.assertEqual(res["http_status"], code)

    def test_scrape_clearweb_refuses_onion_urls(self):
        res = scrape_clearweb_single("http://somethingdark.onion/test")
        self.assertEqual(res["status"], STATUS_ERROR)
        self.assertIn("onion host passed to clearweb scraper", res["detail"])

    def test_scrape_clearweb_url_policy_enforcement(self):
        # Userinfo injection
        res = scrape_clearweb_single("https://user:pass@evil.com/path")
        self.assertEqual(res["status"], STATUS_ERROR)
        self.assertIn("refused", res["detail"])

        # Invalid scheme
        res = scrape_clearweb_single("ftp://example.com/file")
        self.assertEqual(res["status"], STATUS_ERROR)
        self.assertIn("refused", res["detail"])

    def test_scrape_clearweb_multiple(self):
        urls = [
            {"link": "https://example1.com", "title": "Page 1"},
            {"link": "https://example2.com", "title": "Page 2"},
        ]
        mock_session = MagicMock()
        mock_session.get.return_value = self._create_mock_response(200, b"<html><body>Page content</body></html>")

        with unittest.mock.patch("modules.scrape.clearweb.build_clearweb_session", return_value=mock_session):
            results = scrape_clearweb_multiple(urls, max_workers=2)
            self.assertEqual(len(results), 2)
            for r in results:
                self.assertEqual(r["status"], STATUS_OK)
                self.assertIn(UNTRUSTED_OPEN_PREFIX, r["text"])


if __name__ == "__main__":
    unittest.main()
