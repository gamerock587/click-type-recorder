"""Checked mouse input in physical coordinates across the Windows desktop."""
import ctypes
from ctypes import wintypes
import threading


class MouseMovementError(RuntimeError):
    """Input was accepted, but the cursor failed to reach the requested position."""


class MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KeyboardInput(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class InputValue(ctypes.Union):
    _fields_ = [("mi", MouseInput), ("ki", KeyboardInput), ("hi", HardwareInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("value", InputValue)]


class WindowsMouse:
    # Absolute positions refer to the entire virtual desktop, including negative origins.
    MOVE_FLAGS = 0x0001 | 0x2000 | 0x4000 | 0x8000
    BUTTONS = {"left": (0x0002, 0x0004, 0), "right": (0x0008, 0x0010, 0),
               "middle": (0x0020, 0x0040, 0), "x1": (0x0080, 0x0100, 1),
               "x2": (0x0080, 0x0100, 2)}

    def __init__(self, user32=None):
        self.api = user32 if user32 is not None else ctypes.WinDLL("user32", use_last_error=True)
        self.api.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
        self.api.SendInput.restype = wintypes.UINT
        self.api.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.api.GetCursorPos.restype = wintypes.BOOL
        self.api.GetSystemMetrics.argtypes = [ctypes.c_int]
        self.api.GetSystemMetrics.restype = ctypes.c_int
        self.api.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        self.api.SetCursorPos.restype = wintypes.BOOL

    def _send(self, flags, dx=0, dy=0, data=0):
        event = Input(type=0, value=InputValue(mi=MouseInput(
            dx=dx, dy=dy, mouseData=data, dwFlags=flags)))
        ctypes.set_last_error(0)
        if self.api.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input)) != 1:
            code = ctypes.get_last_error()
            raise OSError(f"Windows blocked mouse input (error {code}). "
                          "Check that the desktop is unlocked and the target app is accepting input.")

    @property
    def position(self):
        point = wintypes.POINT()
        if not self.api.GetCursorPos(ctypes.byref(point)):
            raise OSError("Windows could not read the mouse position. Check that the desktop is unlocked.")
        return point.x, point.y

    def move_to(self, x, y, stop=None):
        """Retry only movement, never clicks; stop before a click if positioning fails."""
        stop = stop if stop is not None else threading.Event()
        if stop.is_set():
            return False
        x, y = int(x), int(y)
        left, top, width, height = (self.api.GetSystemMetrics(i) for i in (76, 77, 78, 79))
        if width <= 0 or height <= 0 or not (left <= x < left + width and top <= y < top + height):
            raise ValueError(f"Mouse target ({x}, {y}) is outside the current desktop.")
        # Aim at each pixel's center to avoid rounding onto a neighboring pixel.
        dx = min(65535, ((x - left) * 65536 + 32768) // width)
        dy = min(65535, ((y - top) * 65536 + 32768) // height)
        actual = None
        for attempt in range(3):
            if stop.is_set():
                return False
            self._send(self.MOVE_FLAGS, dx, dy)
            # SendInput queues input. Give Windows time to deliver it before retrying.
            for poll in range(9):
                if stop.is_set():
                    return False
                actual = self.position
                if actual == (x, y):
                    return True
                if poll < 8 and stop.wait(0.01):
                    return False
        raise MouseMovementError(f"Mouse did not reach ({x}, {y}); it stayed at {actual} after 3 attempts.")

    def press(self, button):
        down, up, data = self.BUTTONS[button]
        self._send(down, data=data)

    def recovery_move_to(self, x, y, stop):
        """Use direct positioning plus relative input when absolute input is stuck."""
        if stop.is_set():
            return False
        x, y = int(x), int(y)
        left, top, width, height = (self.api.GetSystemMetrics(i) for i in (76, 77, 78, 79))
        if width <= 0 or height <= 0 or not (left <= x < left + width and top <= y < top + height):
            raise ValueError(f"Mouse target ({x}, {y}) is outside the current desktop.")
        current_x, current_y = self.position
        # A small relative event wakes apps that ignore the absolute movement stream.
        # Direct positioning then corrects any Windows pointer acceleration exactly.
        self._send(0x2001, (x > current_x) - (x < current_x), (y > current_y) - (y < current_y))
        if stop.wait(0.02):
            return False
        ctypes.set_last_error(0)
        if not self.api.SetCursorPos(x, y):
            raise OSError(f"Windows rejected direct mouse movement (error {ctypes.get_last_error()}).")
        for poll in range(9):
            if stop.is_set():
                return False
            actual = self.position
            if actual == (x, y):
                return True
            if poll < 8 and stop.wait(0.01):
                return False
        raise MouseMovementError(f"Direct mouse movement did not reach ({x}, {y}); current position is {actual}.")

    def release(self, button):
        down, up, data = self.BUTTONS[button]
        self._send(up, data=data)

    def scroll(self, dx, dy):
        if dy:
            self._send(0x0800, data=int(dy * 120))
        if dx:
            self._send(0x1000, data=int(dx * 120))
