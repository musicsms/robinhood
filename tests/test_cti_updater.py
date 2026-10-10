"""Unit tests for modules.cti.updater."""

import os
import tempfile
import unittest
from unittest import mock

from modules.cti.indexer import reset_db_connection
from modules.cti.updater import (
    _sha256,
    fetch_file_content,
    list_upstream_files,
    sync_cti_dataset,
    KNOWN_CTI_FILES,
)


class TestCTIUpdater(unittest.TestCase):

    def tearDown(self):
        reset_db_connection()

    def test_sha256_computation(self):
        data = b"Hello Threat Intel"
        h = _sha256(data)
        self.assertEqual(len(h), 64)
        self.assertEqual(h, _sha256(data))

    @mock.patch("urllib.request.urlopen")
    def test_fetch_file_content_success(self, mock_urlopen):
        mock_resp = mock.MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"# Markdown Content"
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        content = fetch_file_content("ransomware_gang.md")
        self.assertEqual(content, b"# Markdown Content")

    @mock.patch("urllib.request.urlopen")
    def test_fetch_file_content_failure(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Network down")
        content = fetch_file_content("nonexistent.md")
        self.assertIsNone(content)

    @mock.patch("urllib.request.urlopen")
    def test_list_upstream_files_fallback_on_error(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("API rate limited")
        files = list_upstream_files()
        self.assertEqual(files, KNOWN_CTI_FILES)

    def test_sync_cti_dataset_with_mock_fetcher(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_md = b"""| Name | Status | Link |
|---|---|---|
| GangX | ONLINE | http://gangx.onion |
"""
            def mock_fetch(fn):
                if fn == "ransomware_gang.md":
                    return sample_md
                return None

            res = sync_cti_dataset(
                target_dir=tmpdir,
                files_to_sync=["ransomware_gang.md", "failed.md"],
                fetch_fn=mock_fetch,
            )

            self.assertEqual(res["status"], "ok")
            self.assertIn("ransomware_gang.md", res["updated_files"])
            self.assertIn("failed.md", res["failed_files"])

            # Verify file exists on disk
            written_file = os.path.join(tmpdir, "ransomware_gang.md")
            self.assertTrue(os.path.exists(written_file))

            # Second sync without changes should report unchanged
            res2 = sync_cti_dataset(
                target_dir=tmpdir,
                files_to_sync=["ransomware_gang.md"],
                fetch_fn=mock_fetch,
            )
            self.assertIn("ransomware_gang.md", res2["unchanged_files"])
            self.assertEqual(res2["updated_files"], [])


if __name__ == "__main__":
    unittest.main()
