import json
import tempfile
import threading
import unittest
from pathlib import Path

from recorder_core import COLOR_CHECK_INTERVAL, Recording, load_macro, make_plan, play, read_accounts, save_macro


class FakeIO:
    def __init__(self):
        self.log = []
        self.release_count = 0

    def copy(self, value): self.log.append(("copy", value))
    def paste_account(self, value, stop): self.log.append(("paste", value))
    def perform(self, event): self.log.append(("action", event))
    def release_all(self): self.release_count += 1


class RecorderTests(unittest.TestCase):
    def test_start_number_includes_selected_account_and_ends_at_last(self):
        names = [f"name{i}" for i in range(1, 9)]
        self.assertEqual(make_plan("accounts", 1, names, 5), [(i, f"name{i}") for i in range(5, 9)])
        self.assertEqual(make_plan("accounts", 1, names, 8), [(8, "name8")])
        for bad in (0, 9):
            with self.assertRaises(ValueError): make_plan("accounts", 1, names, bad)

    def test_accounts_read_bom_and_skip_blanks(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "accounts.txt"
            path.write_text("\ufefffirst\n\nsecond\n third \n", encoding="utf-8")
            self.assertEqual(read_accounts(path), ["first", "second", "third"])

    def test_every_loop_copies_and_pastes_current_name_but_keeps_password_keys(self):
        io = FakeIO()
        password = {"kind": "key", "vk": 65, "pressed": True, "delay": 0}
        events = [{"kind": "account", "delay": 0}, password]
        completed = []
        plan = [(5, "fifth"), (6, "sixth")]
        self.assertEqual(play(events, plan, io, threading.Event(), 0, completed=lambda n, a: completed.append(n)), 2)
        self.assertEqual(io.log, [("copy", "fifth"), ("paste", "fifth"), ("action", password),
                                  ("copy", "sixth"), ("paste", "sixth"), ("action", password)])
        self.assertEqual(completed, [5, 6])

    def test_repeat_exact_count(self):
        io = FakeIO()
        event = {"kind": "key", "vk": 65, "pressed": True, "delay": 0}
        self.assertEqual(play([event], make_plan("repeat", 3, [], 1), io, threading.Event(), 0), 3)
        self.assertEqual(io.log, [("action", event)] * 3)

    def test_plain_ctrl_v_gets_current_account_before_first_key(self):
        io = FakeIO()
        events = [{"kind": "key", "vk": vk, "pressed": pressed, "delay": 0}
                  for vk, pressed in [(17, True), (86, True), (86, False), (17, False)]]
        play(events, [(5, "fifth"), (6, "sixth")], io, threading.Event(), 0)
        self.assertEqual(io.log[0], ("copy", "fifth"))
        self.assertEqual(io.log[5], ("copy", "sixth"))
        self.assertEqual(io.log[1:5], [("action", event) for event in events])

    def test_color_blocks_next_action_and_checks_every_two_seconds(self):
        class ClocklessStop:
            def __init__(self): self.waits = []
            def is_set(self): return False
            def wait(self, seconds):
                self.waits.append(seconds)
                return False
        io = FakeIO()
        stop = ClocklessStop()
        colors = iter([(0, 0, 0), (10, 20, 31), (10, 20, 30)])
        io.move_to = lambda x, y: io.log.append(("move", x, y))
        def pixel(x, y):
            rgb = next(colors)
            io.log.append(("pixel", rgb))
            return rgb
        io.pixel = pixel
        color = {"kind": "color", "x": -50, "y": 20, "rgb": [10, 20, 30], "delay": 0}
        action = {"kind": "key", "vk": 65, "pressed": True, "delay": 0}
        checks = []
        self.assertEqual(play([color, action], [(1, None)], io, stop, color_checked=lambda *args: checks.append(args)), 1)
        self.assertEqual(COLOR_CHECK_INTERVAL, 2.0)
        self.assertEqual([delay for delay in stop.waits if delay], [2.0, 2.0])
        self.assertEqual([entry for entry in io.log if entry[0] == "move"], [("move", -50, 20)] * 3)
        self.assertEqual(io.log[-2:], [("pixel", (10, 20, 30)), ("action", action)])
        self.assertEqual([check[2] for check in checks], [False, False, True])

    def test_stop_during_color_wait_never_runs_next_step_or_advances_account(self):
        io = FakeIO()
        stop = threading.Event()
        checked = threading.Event()
        io.move_to = lambda x, y: None
        io.pixel = lambda x, y: (0, 0, 0)
        events = [{"kind": "color", "x": 1, "y": 2, "rgb": [255, 255, 255], "delay": 0},
                  {"kind": "key", "vk": 65, "pressed": True, "delay": 0}]
        completed = []
        worker = threading.Thread(target=lambda: play(events, [(5, "fifth")], io, stop,
                                  completed=lambda *args: completed.append(args),
                                  color_checked=lambda *args: checked.set()))
        worker.start()
        self.assertTrue(checked.wait(1))
        stop.set()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(io.log, [("copy", "fifth")])
        self.assertEqual(completed, [])
        self.assertGreater(io.release_count, 0)

    def test_color_step_round_trip_and_invalid_rgb(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "macro.json"
            event = {"kind": "color", "x": -12, "y": 40, "rgb": [1, 2, 255], "delay": 0.5}
            save_macro(path, [event])
            self.assertEqual(load_macro(path), [event])
            for invalid in ([0, 0, 256], [1, 2], "red", [True, 1, 2]):
                save_macro(path, [{**event, "rgb": invalid}])
                with self.assertRaises(ValueError): load_macro(path)

    def test_stop_mid_loop_does_not_advance_account(self):
        io = FakeIO()
        stop = threading.Event()
        def perform(event):
            stop.set()
        io.perform = perform
        completed = []
        events = [{"kind": "key", "vk": 65, "pressed": True, "delay": 0}]
        self.assertEqual(play(events, [(5, "fifth"), (6, "sixth")], io, stop, 0,
                              completed=lambda n, a: completed.append(n)), 0)
        self.assertEqual(completed, [])
        self.assertGreater(io.release_count, 0)

    def test_stop_interrupts_long_wait(self):
        io = FakeIO()
        stop = threading.Event()
        worker = threading.Thread(target=lambda: play([{"kind": "wait", "delay": 60}], [(1, None)], io, stop))
        worker.start()
        stop.set()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertGreater(io.release_count, 0)

    def test_error_releases_inputs(self):
        io = FakeIO()
        def broken(event): raise RuntimeError("test")
        io.perform = broken
        with self.assertRaises(RuntimeError):
            play([{"kind": "move", "x": 1, "y": 2, "delay": 0}], [(1, None)], io, threading.Event())
        self.assertEqual(io.release_count, 1)

    def test_recording_round_trip_preserves_trailing_wait(self):
        now = [10.0]
        recording = Recording(lambda: now[0])
        recording.start()
        now[0] = 10.5
        recording.add("account")
        now[0] = 13.0
        events = recording.stop()
        self.assertEqual(events, [{"kind": "account", "delay": 0.5}, {"kind": "wait", "delay": 2.5}])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "macro.json"
            save_macro(path, events)
            self.assertEqual(load_macro(path), events)

    def test_bad_macro_rejected_before_playback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "macro.json"
            for event in ({"kind": "wait", "delay": -1}, {"kind": "wait", "delay": float("nan")},
                          {"kind": "key", "vk": 120, "pressed": True, "delay": 0},
                          {"kind": "click", "delay": 0}):
                path.write_text(json.dumps({"version": 1, "events": [event]}), encoding="utf-8")
                with self.assertRaises(ValueError): load_macro(path)


if __name__ == "__main__":
    unittest.main()
