import io
import json
import threading
import time
import unittest
from unittest.mock import patch

from desktop_support import TelegramClient
from telegram_progress import TelegramRunStatus


class FakeTelegram:
    def __init__(self):
        self.calls = []
        self.sent = threading.Event()

    def send_status(self, text):
        self.calls.append(("send", 77, text))
        self.sent.set()
        return 77

    def edit_status(self, message_id, text):
        self.calls.append(("edit", message_id, text))

    def send_image(self, image, size, caption):
        self.calls.append(("image", image, caption))


class TelegramProgressTests(unittest.TestCase):
    def test_account_and_run_completion_only_edit_status(self):
        client = FakeTelegram()
        reporter = TelegramRunStatus(client, [(3, "name3"), (4, "name4")], interval=0)
        reporter.start()
        self.assertTrue(client.sent.wait(1))
        reporter.begin_account(1, 2, 3, "name3")
        reporter.step(1, 2)
        reporter.account_done(3, "name3")
        reporter.begin_account(2, 2, 4, "name4")
        reporter.account_done(4, "name4")
        reporter.finish(True)
        reporter.thread.join(1)
        self.assertFalse(reporter.thread.is_alive())
        self.assertEqual([c[0] for c in client.calls].count("send"), 1)
        self.assertIn("Starting in account :: name3", client.calls[0][2])
        self.assertIn("Progress: 0%", client.calls[0][2])
        self.assertTrue(all(c[1] == 77 for c in client.calls if c[0] == "edit"))
        self.assertFalse(any(c[0] == "image" for c in client.calls))
        self.assertIn("Done :: name4", client.calls[-1][2])
        self.assertIn("Progress: 100%", client.calls[-1][2])

    def test_stop_does_not_claim_completion_or_send_image(self):
        client = FakeTelegram()
        reporter = TelegramRunStatus(client, [(5, "name5"), (6, "name6")], interval=0)
        reporter.start()
        reporter.step(1, 2)
        reporter.finish(False)
        reporter.thread.join(1)
        self.assertIn("Stopped :: name5", client.calls[-1][2])
        self.assertIn("Progress: 25%", client.calls[-1][2])
        self.assertIn("Completed: 0/2", client.calls[-1][2])
        self.assertFalse(any(c[0] == "image" for c in client.calls))

    def test_progress_updates_are_coalesced_and_throttled(self):
        client = FakeTelegram()
        reporter = TelegramRunStatus(client, [(1, "a")], interval=0.1)
        reporter.start()
        self.assertTrue(client.sent.wait(1))
        for step in range(1, 90): reporter.step(step, 100)
        time.sleep(0.02)
        self.assertEqual(len(client.calls), 1)
        reporter.finish(False)
        reporter.thread.join(1)
        self.assertEqual(len(client.calls), 2)
        self.assertIn("89%", client.calls[-1][2])

    def test_network_error_is_reported_without_sending_duplicate_messages(self):
        client = FakeTelegram()
        def fail(text): raise RuntimeError("Network unavailable")
        client.send_status = fail
        errors = []
        reporter = TelegramRunStatus(client, [(1, "a")], report=errors.append, interval=0)
        reporter.start()
        reporter.thread.join(1)
        self.assertIn("Network unavailable", errors[0])
        self.assertEqual(client.calls, [])

    def test_api_send_and_edit_use_same_saved_chat_and_message_id(self):
        client = TelegramClient("unused")
        client.credentials = lambda: {"token": "123:test", "chat_id": "456"}
        responses = [io.BytesIO(b'{"ok":true,"result":{"message_id":77}}'),
                     io.BytesIO(b'{"ok":true,"result":{"message_id":77}}')]
        with patch("desktop_support.urllib.request.urlopen", side_effect=responses) as calls:
            message_id = client.send_status("Starting in account :: name3\nProgress: 0%")
            client.edit_status(message_id, "Progress: 50%")
        sent, edited = [json.loads(c.args[0].data) for c in calls.call_args_list]
        self.assertEqual(sent["chat_id"], "456")
        self.assertEqual(edited["chat_id"], "456")
        self.assertEqual(edited["message_id"], 77)


if __name__ == "__main__":
    unittest.main()
