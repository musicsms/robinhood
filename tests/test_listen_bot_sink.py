"""Unit tests for modules.listen.sinks.telegram_bot."""

import logging
import unittest
from unittest import mock

from modules.listen.events import MatchEvent
from modules.listen.sinks.telegram_bot import format_message, telegram_bot_sink


def _event():
    return MatchEvent(
        event_id="e1", timestamp="t", watchlist_id="a",
        source_type="telegram_web", target="@x", post_date="d",
        post_views="", post_text="Vietcombank leak",
        match_reasons=["bank:vietcombank"],
    )


class TestBotSink(unittest.IsolatedAsyncioTestCase):

    async def test_bot_sink_no_ops_when_env_unset_and_warns_once(self):
        sink = telegram_bot_sink(token=None, chat_id=None)
        with self.assertLogs("modules.listen.sinks.telegram_bot",
                             level="WARNING") as cm:
            await sink(_event())
            await sink(_event())
        warnings = [r for r in cm.records if r.levelname == "WARNING"]
        self.assertEqual(len(warnings), 1)

    async def test_bot_sink_calls_http_post_with_formatted_body(self):
        resp = mock.MagicMock(status_code=200)
        poster = mock.MagicMock(return_value=resp)
        sink = telegram_bot_sink(token="T", chat_id="C", http_post=poster)
        await sink(_event())
        poster.assert_called_once()
        url = poster.call_args.args[0]
        self.assertEqual(url, "https://api.telegram.org/botT/sendMessage")
        payload = poster.call_args.kwargs["json"]
        self.assertEqual(payload["chat_id"], "C")
        self.assertIn("Vietcombank leak", payload["text"])

    async def test_bot_sink_logs_but_does_not_raise_on_api_error(self):
        resp = mock.MagicMock(status_code=400, text="bad request")
        poster = mock.MagicMock(return_value=resp)
        sink = telegram_bot_sink(token="T", chat_id="C", http_post=poster)
        with self.assertLogs("modules.listen.sinks.telegram_bot",
                             level="WARNING") as cm:
            await sink(_event())
        joined = " ".join(r.getMessage() for r in cm.records)
        self.assertIn("400", joined)
        # Must NOT leak the response body or the token.
        self.assertNotIn("bad request", joined)
        self.assertNotIn("T", joined.split("HTTP")[-1])  # token 'T' absent from log

    async def test_bot_sink_swallows_network_error(self):
        poster = mock.MagicMock(side_effect=RuntimeError("DNS fail"))
        sink = telegram_bot_sink(token="T", chat_id="C", http_post=poster)
        with self.assertLogs("modules.listen.sinks.telegram_bot",
                             level="WARNING"):
            await sink(_event())  # must not raise


class TestFormatMessage(unittest.TestCase):

    def test_format_message_under_telegram_limit(self):
        e = _event()
        e.post_text = "x" * 20000
        self.assertLessEqual(len(format_message(e)), 4096)

    def test_format_message_includes_target_and_reasons_and_link(self):
        msg = format_message(_event())
        self.assertIn("@x", msg)
        self.assertIn("bank:vietcombank", msg)
        self.assertIn("https://t.me/s/x", msg)


if __name__ == "__main__":
    unittest.main()
