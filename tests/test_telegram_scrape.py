"""Unit tests for modules.scrape.telegram."""

import unittest
from unittest import mock

from modules.scrape.telegram import (
    extract_telegram_channel_name,
    is_telegram_channel,
    scrape_telegram_channel,
)


class TestTelegramScraper(unittest.TestCase):

    def test_extract_telegram_channel_name_formats(self):
        # Handles various input formats
        self.assertEqual(extract_telegram_channel_name("@lockbit_news"), "lockbit_news")
        self.assertEqual(extract_telegram_channel_name("https://t.me/threat_intel"), "threat_intel")
        self.assertEqual(extract_telegram_channel_name("http://t.me/s/darkfeed"), "darkfeed")
        self.assertEqual(extract_telegram_channel_name("https://telegram.me/vxunderground"), "vxunderground")
        self.assertEqual(extract_telegram_channel_name("t.me/cybernews"), "cybernews")
        self.assertEqual(extract_telegram_channel_name("ransomware_watch"), "ransomware_watch")

    def test_extract_telegram_channel_name_invalid_or_internal(self):
        # Rejects private/system paths and non-matching formats
        self.assertIsNone(extract_telegram_channel_name("https://t.me/joinchat/AAAAAF"))
        self.assertIsNone(extract_telegram_channel_name("https://google.com/search"))
        self.assertIsNone(extract_telegram_channel_name("https://t.me/c/12345/6789"))
        self.assertIsNone(extract_telegram_channel_name(""))
        self.assertIsNone(extract_telegram_channel_name(None))

    def test_is_telegram_channel(self):
        self.assertTrue(is_telegram_channel("https://t.me/lockbit_gang"))
        self.assertTrue(is_telegram_channel("@akira_ransomware"))
        self.assertFalse(is_telegram_channel("https://example.com"))

    @mock.patch("requests.Session.get")
    def test_scrape_telegram_channel_success(self, mock_get):
        mock_html = """
        <!DOCTYPE html>
        <html>
        <head></head>
        <body>
            <div class="tgme_channel_info_header_title">LockBit Cyber Intel</div>
            <div class="tgme_channel_info_description">Official news and leak announcements.</div>
            <div class="tgme_header_counter">15,000 subscribers</div>

            <div class="tgme_widget_message_wrap">
                <div class="tgme_widget_message_text">Victim database posted: US Bank national affiliate.</div>
                <div class="tgme_widget_message_date"><time datetime="2026-08-20T12:00:00Z"></time></div>
                <div class="tgme_widget_message_views">1,250</div>
            </div>
            <div class="tgme_widget_message_wrap">
                <div class="tgme_widget_message_text">PoC exploit available for CVE-2024-3400.</div>
                <div class="tgme_widget_message_date"><time datetime="2026-08-21T14:30:00Z"></time></div>
                <div class="tgme_widget_message_views">2,100</div>
            </div>
        </body>
        </html>
        """
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_html
        mock_get.return_value = mock_resp

        res = scrape_telegram_channel("https://t.me/lockbit_news")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["channel_name"], "lockbit_news")
        self.assertEqual(res["title"], "LockBit Cyber Intel")
        self.assertEqual(res["posts_count"], 2)
        self.assertIn("Victim database posted", res["text"])
        self.assertIn("PoC exploit available", res["text"])
        # Check security invariant: untrusted fence wrapping
        self.assertTrue(res["text"].startswith("<<<ROBIN_UNTRUSTED_CONTENT"))
        self.assertTrue(res["text"].endswith("<<<END_ROBIN_UNTRUSTED_CONTENT>>>"))

    @mock.patch("requests.Session.get")
    def test_scrape_telegram_channel_http_error(self, mock_get):
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        res = scrape_telegram_channel("https://t.me/nonexistent_channel_404")
        self.assertEqual(res["status"], "http_404")
        self.assertEqual(res["posts_count"], 0)
        self.assertEqual(res["text"], "")


if __name__ == "__main__":
    unittest.main()
