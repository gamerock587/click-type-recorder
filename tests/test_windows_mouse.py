"""Exercise failures and long runs without moving the user's mouse or sending keys."""
import ctypes
from ctypes import wintypes
import tempfile
import unittest
from unittest.mock import Mock, patch

from desktop_support import RecorderInstance, mouse_recovery_point
from recorder import DesktopIO
from recorder_core import play
from windows_mouse import Input, WindowsMouse


class FakeWindows:
    def __init__(self):
        self.bounds = (-1920, -100, 5760, 1180)
        self.cursor = (0, 0)
        self.inputs = []
        self.dropped_moves = 0
        self.rejected = False
        self.unlock_on_right_click = False
        self.direct_blocked = False
        self.absolute_offset = (0, 0)
        self.SetCursorPos = Mock(side_effect=self.direct_move)
        self.SendInput = Mock(side_effect=self.send)
        self.GetCursorPos = Mock(side_effect=self.position)
        self.GetSystemMetrics = Mock(side_effect=lambda i: self.bounds[i - 76])

    def send(self, count, pointer, size):
        if self.rejected:
            return 0
        event = ctypes.cast(pointer, ctypes.POINTER(Input)).contents.value.mi
        self.inputs.append((event.dwFlags, event.dx, event.dy, event.mouseData))
        if event.dwFlags == 16 and self.unlock_on_right_click:
            self.dropped_moves = 0
            self.direct_blocked = False
        if event.dwFlags == 0x2001:
            if not self.direct_blocked:
                self.cursor = (self.cursor[0] + event.dx * 2, self.cursor[1] + event.dy * 2)
            return count
        if event.dwFlags & 1:
            if self.dropped_moves:
                self.dropped_moves -= 1
            else:
                left, top, width, height = self.bounds
                self.cursor = (left + event.dx * width // 65536 + self.absolute_offset[0],
                               top + event.dy * height // 65536 + self.absolute_offset[1])
        return count

    def direct_move(self, x, y):
        if not self.direct_blocked:
            self.cursor = (x, y)
        return 1

    def position(self, pointer):
        point = ctypes.cast(pointer, ctypes.POINTER(wintypes.POINT)).contents
        point.x, point.y = self.cursor
        return 1


class FastStop:
    def __init__(self):
        self.stopped = False
        self.waits = []

    def is_set(self): return self.stopped
    def wait(self, delay):
        self.waits.append(delay)
        return self.stopped


class WindowsMouseTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeWindows()
        self.mouse = WindowsMouse(self.api)
        self.stop = FastStop()
        self.monitors = [{"device": "left", "bounds": [-1920, 0, 0, 1080]},
                         {"device": "main", "bounds": [0, 0, 1920, 1080]},
                         {"device": "right", "bounds": [1920, -100, 3840, 980]}]
        monitors = patch("recorder.enumerate_monitors", return_value=self.monitors)
        monitors.start()
        self.addCleanup(monitors.stop)

    def adapter(self):
        io = DesktopIO.__new__(DesktopIO)
        io.pointer = self.mouse
        io.stop = self.stop
        io.keys = Mock()
        io.held_keys = set()
        io.held_buttons = set()
        io.report = Mock()
        return io

    def test_many_runs_reach_all_three_screens_and_edges(self):
        points = [(-1920, 0), (-1, 1079), (0, 0), (1919, 1079), (1920, -100), (3839, 979)]
        for loop in range(100):
            for x, y in points:
                self.assertTrue(self.mouse.move_to(x, y, self.stop))
                self.assertEqual(self.api.cursor, (x, y))
        self.assertEqual(len(self.api.inputs), 600)
        self.assertTrue(all(flags == 0xE001 for flags, *_ in self.api.inputs))
        self.assertEqual(ctypes.sizeof(Input), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)

    def test_lost_movement_retries_before_exactly_one_click(self):
        self.api.dropped_moves = 1
        io = self.adapter()
        io.perform({"kind": "click", "x": -1500, "y": 200, "button": "left", "pressed": True})
        self.assertEqual(self.api.cursor, (-1500, 200))
        self.assertEqual([e[0] for e in self.api.inputs], [0xE001, 0xE001, 2])
        self.assertEqual(sum(self.stop.waits), 0.08)
        io.release_all()
        self.assertEqual(self.api.inputs[-1][0], 4)
        self.assertFalse(io.held_buttons)

    def test_repeated_position_still_sends_movement_and_invalid_position_never_clicks(self):
        for repeat in range(3):
            self.mouse.move_to(100, 200, self.stop)
        self.assertEqual(len(self.api.inputs), 3)
        with self.assertRaisesRegex(ValueError, "outside the current desktop"):
            self.adapter().perform({"kind": "click", "x": -2000, "y": 0, "button": "left", "pressed": True})
        self.assertEqual(len(self.api.inputs), 3)

    def test_permanently_blocked_position_keeps_retrying_until_f9_without_completion(self):
        self.api.dropped_moves = 1000
        self.api.direct_blocked = True
        io = self.adapter()
        completed = Mock()
        waits = []
        def cancel_after_three_cycles(delay):
            waits.append(delay)
            if waits.count(2.0) == 3:
                self.stop.stopped = True
            return self.stop.stopped
        self.stop.wait = cancel_after_three_cycles
        event = {"kind": "click", "delay": 0, "x": 200, "y": 300, "button": "left", "pressed": True}
        self.assertEqual(play([event], [(1, None)], io, self.stop, completed=completed), 0)
        completed.assert_not_called()
        flags = [e[0] for e in self.api.inputs]
        self.assertEqual(flags.count(8), 3)
        self.assertEqual(flags.count(16), 3)
        self.assertNotIn(2, flags)
        self.assertFalse(io.held_buttons)
        self.assertFalse(io.held_keys)

    def test_recovery_moves_200_then_right_clicks_and_resumes_pending_click_once(self):
        self.api.cursor = (574, 73)
        self.api.dropped_moves = 100
        io = self.adapter()
        completed = Mock()
        events = [{"kind": "click", "delay": 0, "x": 569, "y": 73, "button": "left", "pressed": state}
                  for state in (True, False)]
        # Release the absolute input blockage after the recovery right-click.
        self.api.unlock_on_right_click = True
        self.assertEqual(play(events, [(1, None)], io, self.stop, completed=completed), 1)
        completed.assert_called_once_with(1, None)
        flags = [e[0] for e in self.api.inputs]
        self.assertEqual(flags.count(8), 1)
        self.assertEqual(flags.count(2), 1)
        self.assertEqual(flags.count(4), 1)
        self.assertLess(flags.index(0x2001), flags.index(8))
        self.assertEqual([call.args for call in self.api.SetCursorPos.call_args_list], [(774, 73), (569, 73)])
        self.assertEqual(self.api.cursor, (569, 73))
        self.assertEqual(io.keys.press.call_count, 1)

    def test_later_recovery_cycle_succeeds_without_skipping_or_repeating_pending_action(self):
        self.api.dropped_moves = 1000
        self.api.direct_blocked = True
        release_count = 0
        original_send = self.api.send
        def unlock_on_third_right_click(*args):
            nonlocal release_count
            result = original_send(*args)
            if self.api.inputs[-1][0] == 16:
                release_count += 1
                if release_count == 3:
                    self.api.direct_blocked = False
            return result
        self.api.SendInput.side_effect = unlock_on_third_right_click
        self.adapter().perform({"kind": "click", "x": 569, "y": 73, "button": "left", "pressed": True})
        self.assertEqual(self.api.cursor, (569, 73))
        flags = [e[0] for e in self.api.inputs]
        self.assertEqual(flags.count(2), 1)
        self.assertEqual(flags.count(8), 3)
        self.assertEqual(self.stop.waits.count(2.0), 2)

    def test_five_pixel_absolute_offset_is_corrected_with_direct_positioning(self):
        self.api.absolute_offset = (5, 0)
        self.adapter().perform({"kind": "click", "x": 569, "y": 73, "button": "left", "pressed": True})
        self.assertEqual(self.api.cursor, (569, 73))
        self.assertEqual(self.api.SetCursorPos.call_args.args, (569, 73))
        self.assertEqual([e[0] for e in self.api.inputs].count(2), 1)
        self.assertNotIn(2.0, self.stop.waits)

    def test_recovery_nudge_stays_on_same_monitor_at_edges_and_negative_coordinates(self):
        for point, expected in [((574, 73), (774, 73)), ((1919, 73), (1719, 73)),
                                ((-1, 73), (-201, 73)), ((-1920, 73), (-1720, 73)),
                                ((3839, -100), (3639, -100))]:
            self.assertEqual(mouse_recovery_point(self.monitors, *point), expected)
        tiny = [{"device": "tiny", "bounds": [-20, -20, 20, 20]}]
        x, y = mouse_recovery_point(tiny, 0, 0)
        self.assertTrue(-20 <= x < 20 and -20 <= y < 20)

    def test_stop_during_each_recovery_pause_releases_buttons_and_skips_original_click(self):
        for pause_index in range(1, 8):
            with self.subTest(pause=pause_index):
                self.api.inputs.clear()
                self.api.cursor = (574, 73)
                self.stop = FastStop()
                count = 0
                def interrupt(delay):
                    nonlocal count
                    count += 1
                    if count == pause_index:
                        self.stop.stopped = True
                    return self.stop.stopped
                self.stop.wait = interrupt
                io = self.adapter()
                self.assertFalse(io.recover_mouse(569, 73))
                flags = [e[0] for e in self.api.inputs]
                self.assertLessEqual(flags.count(8), 1)
                self.assertEqual(flags.count(16), flags.count(8))
                self.assertNotIn(2, flags)
                self.assertFalse(io.held_buttons)
                self.assertFalse(io.held_keys)

    def test_recovery_releases_held_buttons_and_modifiers_before_right_click(self):
        io = self.adapter()
        io.held_buttons = {"left"}
        io.held_keys = {17}
        self.assertTrue(io.recover_mouse(569, 73))
        flags = [e[0] for e in self.api.inputs]
        self.assertEqual(flags[0], 4)
        self.assertLess(flags.index(4), flags.index(8))
        self.assertEqual(flags.count(16), 1)
        self.assertFalse(io.held_buttons)
        self.assertFalse(io.held_keys)

    def test_rejected_input_waits_for_recovery_without_clicking_until_f9(self):
        self.api.rejected = True
        io = self.adapter()
        cycles = 0
        def cancel(delay):
            nonlocal cycles
            if delay == 2.0:
                cycles += 1
                if cycles == 2:
                    self.stop.stopped = True
            return self.stop.stopped
        self.stop.wait = cancel
        io.perform({"kind": "click", "x": 569, "y": 73, "button": "left", "pressed": True})
        self.assertEqual(cycles, 2)
        self.assertFalse(self.api.inputs)
        self.assertTrue(io.report.called)

    def test_stop_interrupts_retry_without_click(self):
        self.api.dropped_moves = 100
        def stop_on_wait(delay):
            self.stop.stopped = True
            return True
        self.stop.wait = stop_on_wait
        self.adapter().perform({"kind": "click", "x": 200, "y": 300, "button": "left", "pressed": True})
        self.assertEqual(len(self.api.inputs), 1)
        self.assertEqual(self.api.inputs[0][0], 0xE001)

    def test_rejected_input_or_missing_cursor_is_an_error(self):
        self.api.rejected = True
        with self.assertRaisesRegex(OSError, "blocked mouse input"):
            self.mouse.move_to(100, 200)
        self.api.rejected = False
        self.api.GetCursorPos.side_effect = None
        self.api.GetCursorPos.return_value = 0
        with self.assertRaisesRegex(OSError, "read the mouse position"):
            self.mouse.move_to(100, 200)

    def test_buttons_scroll_and_drag_preserve_input_order(self):
        io = self.adapter()
        events = [dict(kind="click", x=-50, y=100, button="left", pressed=True),
                  dict(kind="move", x=50, y=100),
                  dict(kind="click", x=50, y=100, button="left", pressed=False),
                  dict(kind="scroll", x=50, y=100, dx=-1, dy=-2)]
        for event in events:
            io.perform(event)
        self.assertEqual([e[0] for e in self.api.inputs], [0xE001, 2, 0xE001, 0xE001, 4, 0xE001, 0x800, 0x1000])
        self.assertEqual(self.api.inputs[-2][3], ctypes.c_uint32(-240).value)
        self.assertEqual(self.api.inputs[-1][3], ctypes.c_uint32(-120).value)
        for button, down, up, data in [("right", 8, 16, 0), ("middle", 32, 64, 0),
                                       ("x1", 128, 256, 1), ("x2", 128, 256, 2)]:
            self.mouse.press(button)
            self.mouse.release(button)
            self.assertEqual(self.api.inputs[-2:], [(down, 0, 0, data), (up, 0, 0, data)])

    def test_cleanup_attempts_every_held_input_even_after_an_error(self):
        io = self.adapter()
        io.held_keys = {65, 66}
        io.held_buttons = {"left", "right"}
        io.keys.release.side_effect = OSError("key release failed")
        with self.assertRaisesRegex(RuntimeError, "release all held inputs"):
            io.release_all()
        self.assertEqual(io.keys.release.call_count, 2)
        self.assertEqual({e[0] for e in self.api.inputs}, {4, 16})
        self.assertFalse(io.held_buttons)

    def test_duplicate_instance_is_blocked_until_first_closes(self):
        with tempfile.TemporaryDirectory() as folder:
            first = RecorderInstance(folder)
            try:
                with self.assertRaisesRegex(RuntimeError, "already open"):
                    RecorderInstance(folder)
            finally:
                first.close()
            second = RecorderInstance(folder)
            second.close()


if __name__ == "__main__":
    unittest.main()
