"""Unit tests for modules.listen.seed."""

import tempfile
import unittest
from pathlib import Path

from modules.listen.seed import extract_telegram_channels, seed_from_deepdarkcti
from modules.listen.watchlist import list_enabled, open_db


class TestExtractChannels(unittest.TestCase):

    def test_extract_telegram_channels_from_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "x.md"
            md.write_text(
                "|[Chan A](https://t.me/chan_a)|ONLINE|\n"
                "|[Chan B](https://t.me/s/chan_b)|ONLINE|\n"
                "|[Chan A again](https://t.me/chan_a)|OFFLINE|\n"
                "|[Private](https://t.me/joinchat/XYZ)|OFFLINE|\n"
            )
            self.assertEqual(
                extract_telegram_channels(md), ["chan_a", "chan_b"]
            )


class TestSeedInfostealer(unittest.TestCase):
    """Pins the infostealer seed count. If the upstream file changes and this
    fails, update both the spec and the assertion — do not loosen it to > 0."""

    def test_seed_on_real_infostealer_file_inserts_98(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "w.db")
            path = Path("data/deepdarkCTI/telegram_infostealer.md")
            inserted = seed_from_deepdarkcti(conn, path, tags=["infostealer"])
            self.assertEqual(inserted, 98)
            self.assertEqual(len(list_enabled(conn)), 98)
            # second call must be a no-op
            self.assertEqual(
                seed_from_deepdarkcti(conn, path, tags=["infostealer"]), 0
            )


if __name__ == "__main__":
    unittest.main()
