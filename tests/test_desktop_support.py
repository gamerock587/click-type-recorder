import io
import json
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from desktop_support import (TelegramClient, capture_monitor, monitor_at, remap_events,
                             save_telegram_credentials, launch_or_focus)
from recorder_core import load_recording, play, save_macro


MONITORS = [{"device": "left", "bounds": [-1920, 0, 0, 1080]},
            {"device": "main", "bounds": [0, 0, 1920, 1080]},
            {"device": "right", "bounds": [1920, -100, 3840, 980]}]


class MonitorTests(unittest.TestCase):
    def test_launch_step_round_trip_and_direct_launch_without_shell(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / "game.exe"
            executable.write_bytes(b"test")
            path = Path(folder) / "macro.json"
            events = [{"kind": "launch", "path": str(executable), "delay": 0}, {"kind": "wait", "delay": 3}]
            save_macro(path, events)
            self.assertEqual(load_recording(path)["events"], events)
            with patch("desktop_support.find_app_window", return_value=None), patch("desktop_support.subprocess.Popen") as launch:
                launch_or_focus(executable)
            launch.assert_called_once_with([str(executable)], cwd=str(executable.parent))

    def test_launch_is_followed_by_three_second_wait_before_clicks(self):
        from test_recorder import FakeIO
        class ImmediateStop:
            def __init__(self): self.waits = []
            def is_set(self): return False
            def wait(self, delay):
                self.waits.append(delay)
                return False
        stop = ImmediateStop()
        io_adapter = FakeIO()
        launch = {"kind": "launch", "path": r"C:\Games\game.exe", "delay": 0}
        click = {"kind": "click", "x": 10, "y": 20, "button": "left", "pressed": True, "delay": 0}
        play([launch, {"kind": "wait", "delay": 3}, click], [(1, None)], io_adapter, stop)
        self.assertEqual(io_adapter.log, [("action", launch), ("action", click)])
        self.assertEqual(stop.waits, [0, 3, 0])

    def test_monitor_under_mouse_on_three_screens_and_boundaries(self):
        self.assertEqual(monitor_at(MONITORS, -1, 0)["device"], "left")
        self.assertEqual(monitor_at(MONITORS, 0, 0)["device"], "main")
        self.assertEqual(monitor_at(MONITORS, 1920, 0)["device"], "right")
        self.assertEqual(monitor_at(MONITORS, 2000, -50)["device"], "right")
        with self.assertRaises(ValueError): monitor_at(MONITORS, 3840, 0)

    def test_click_color_and_screenshot_stay_on_the_recorded_display(self):
        changed = [{"device": "left", "bounds": [1920, 10, 3840, 1090]}, MONITORS[1]]
        events = [{"kind": "click", "x": -1800, "y": 50, "monitor": "left"},
                  {"kind": "color", "x": -1800, "y": 60, "monitor": "left"},
                  {"kind": "screenshot", "monitor": "left"}]
        result = remap_events(events, MONITORS, changed)
        self.assertEqual((result[0]["x"], result[0]["y"]), (2040, 60))
        self.assertEqual((result[1]["x"], result[1]["y"]), (2040, 70))
        self.assertEqual(result[2]["monitor"], "left")
        self.assertEqual(events[0]["x"], -1800)

    def test_missing_or_resized_display_fails_before_playback(self):
        event = [{"kind": "screenshot", "monitor": "left"}]
        with self.assertRaisesRegex(ValueError, "disconnected"):
            remap_events(event, MONITORS, MONITORS[1:])
        with self.assertRaisesRegex(ValueError, "Resolution changed"):
            remap_events(event, MONITORS, [{"device": "left", "bounds": [0, 0, 800, 600]}])

    def test_screen_metadata_round_trip_and_legacy_recordings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "record.json"
            events = [{"kind": "screenshot", "monitor": "left", "delay": 0}]
            save_macro(path, events, MONITORS, "left")
            data = load_recording(path)
            self.assertEqual(data["events"], events)
            self.assertEqual(data["monitors"], MONITORS)
            self.assertEqual(data["recording_monitor"], "left")
            path.write_text(json.dumps({"version": 1, "events": [{"kind": "wait", "delay": 0}]}))
            self.assertEqual(load_recording(path)["monitors"], [])

    def test_capture_crops_exactly_one_monitor_including_negative_coordinates(self):
        from PIL import Image
        with patch("desktop_support.ImageGrab.grab", return_value=Image.new("RGB", (1920, 1080))) as grab:
            png, size = capture_monitor("left", MONITORS)
        grab.assert_called_once_with(bbox=(-1920, 0, 0, 1080), all_screens=True)
        self.assertEqual(size, (1920, 1080))
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_screenshot_failure_or_stop_does_not_advance_account(self):
        from test_recorder import FakeIO
        for fail in (False, True):
            io_adapter = FakeIO()
            def screenshot(*args):
                if fail: raise RuntimeError("Delivery failed")
                return False
            io_adapter.screenshot = screenshot
            completed = []
            def run():
                return play([{"kind": "screenshot", "delay": 0, "monitor": "left"}], [(5, "name5")],
                            io_adapter, threading.Event(), completed=lambda *args: completed.append(args))
            if fail:
                with self.assertRaises(RuntimeError): run()
            else:
                self.assertEqual(run(), 0)
            self.assertEqual(completed, [])
            self.assertGreater(io_adapter.release_count, 0)


class TelegramTests(unittest.TestCase):
    def test_credentials_are_encrypted_and_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "secret"
            save_telegram_credentials(path, "123:test-only-token", "456")
            self.assertNotIn(b"test-only-token", path.read_bytes())
            self.assertEqual(TelegramClient(path).credentials()["token"], "123:test-only-token")

    def test_upload_uses_saved_chat_and_hides_token_from_http_errors(self):
        client = TelegramClient("unused")
        client.credentials = lambda: {"token": "123:secret-token", "chat_id": "456"}
        response = io.BytesIO(b'{"ok":true,"result":{"message_id":123}}')
        with patch("desktop_support.urllib.request.urlopen", return_value=response) as request:
            self.assertEqual(client.send_image(b"PNG-test", (1920, 1080), "test")["message_id"], 123)
            payload = request.call_args[0][0].data
            self.assertIn(b'chat_id"\r\n\r\n456', payload)
            self.assertIn(b'filename="screenshot.png"', payload)
        failure = urllib.error.HTTPError("https://api.telegram.org/bot123:secret-token/sendPhoto", 401, "bad", {}, None)
        with patch("desktop_support.urllib.request.urlopen", side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, "token is invalid") as error:
                client.send_image(b"PNG", (10, 10), "test")
            self.assertNotIn("secret-token", str(error.exception))

    def test_f9_does_not_wait_for_slow_network(self):
        client = TelegramClient("unused")
        upload_started, finish_upload, stop = threading.Event(), threading.Event(), threading.Event()
        def slow(*args):
            upload_started.set()
            finish_upload.wait(2)
            return {}
        client.send_image = slow
        result = []
        worker = threading.Thread(target=lambda: result.append(client.send_cancellable(b"PNG", (1, 1), "test", stop)))
        worker.start()
        self.assertTrue(upload_started.wait(1))
        stop.set()
        worker.join(0.5)
        finish_upload.set()
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [False])


if __name__ == "__main__":
    unittest.main()
