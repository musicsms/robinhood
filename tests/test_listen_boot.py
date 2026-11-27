"""Unit tests for modules.listen.boot."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aiohttp import web

from modules.listen.boot import start_listen


class TestStartListen(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        # Steer boot at the temp dir and an empty YAML.
        (self.tmp_path / "empty.yaml").write_text("[]\n")
        self._env = mock.patch.dict(os.environ, {
            "LISTEN_WATCHLIST_DB_PATH": str(self.tmp_path / "w.db"),
            "LISTEN_WATCHLIST_YAML_PATH": str(self.tmp_path / "empty.yaml"),
            # Disable the deepdarkCTI boot-seed for this isolated test by
            # pointing it at a file that does not exist; boot must log and
            # continue.
            "LISTEN_SEED_INFOSTEALER_PATH": str(self.tmp_path / "nope.md"),
        })
        self._env.start()
        for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_CHAT_ID"):
            os.environ.pop(k, None)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    async def test_start_listen_with_empty_yaml_launches_no_tasks(self):
        from config import RobinConfig
        app = web.Application()
        tasks = await start_listen(app, RobinConfig.from_env())
        self.assertEqual(tasks, [])
        self.assertTrue(app.on_cleanup)  # cleanup handler registered

    async def test_start_listen_with_one_yaml_entry_launches_one_task(self):
        yaml = self.tmp_path / "one.yaml"
        yaml.write_text(
            "- id: t1\n  source_type: telegram_web\n  target: '@dummy'\n"
            "  interval_sec: 3600\n  enabled: true\n  tags: []\n"
        )
        os.environ["LISTEN_WATCHLIST_YAML_PATH"] = str(yaml)
        from config import RobinConfig
        app = web.Application()
        # Replace the poll_loop with an inert coroutine so no real HTTP fires.
        async def fake_loop(*a, **kw):
            return
        with mock.patch(
            "modules.listen.boot.poll_loop", side_effect=fake_loop
        ):
            tasks = await start_listen(app, RobinConfig.from_env())
        self.assertEqual(len(tasks), 1)
        # Cleanup must cancel the task without raising.
        for handler in app.on_cleanup:
            await handler(app)


if __name__ == "__main__":
    unittest.main()
