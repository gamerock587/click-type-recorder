"""Windows monitor identity, screenshots, and locally protected Telegram credentials."""
import ctypes
from ctypes import wintypes
import io
import hashlib
import json
import os
import subprocess
from pathlib import Path
import threading
import urllib.error
import urllib.request
import uuid

from PIL import ImageGrab


class RecorderInstance:
    """Prevent two recorder processes in this folder from competing for global input."""
    def __init__(self, folder):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.api.CreateMutexW.restype = wintypes.HANDLE
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        identity = hashlib.sha256(os.path.normcase(str(Path(folder).resolve())).encode("utf-8")).hexdigest()
        ctypes.set_last_error(0)
        self.handle = self.api.CreateMutexW(None, False, "Local\\ClickTypeRecorder_" + identity)
        error = ctypes.get_last_error()
        if not self.handle:
            raise OSError("Could not reserve the recorder's input session.")
        if error == 183:  # ERROR_ALREADY_EXISTS
            self.close()
            raise RuntimeError("The recorder is already open. Use the existing window, or close it before reopening.")

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def find_app_window(executable):
    """Find an existing visible window belonging to this exact executable."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    found = []
    target = os.path.normcase(str(Path(executable).resolve()))
    def inspect(handle, data):
        if not user32.IsWindowVisible(handle):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        process = kernel32.OpenProcess(0x1000, False, pid.value)
        if not process:
            return True
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            if kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(length)) and os.path.normcase(buffer.value) == target:
                found.append(handle)
                return False
        finally:
            kernel32.CloseHandle(process)
        return True
    user32.EnumWindows(callback_type(inspect), 0)
    return found[0] if found else None


def launch_or_focus(executable):
    path = Path(executable)
    if not path.is_file():
        raise ValueError(f"Application not found: {path}")
    handle = find_app_window(path)
    if handle is None:
        # Launch the requested interactive app directly, with no shell expansion.
        subprocess.Popen([str(path)], cwd=str(path.parent))
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.ShowWindow(handle, 9 if user32.IsIconic(handle) else 5)
    if not user32.SetForegroundWindow(handle):
        raise RuntimeError("Windows could not activate the game window. Click its taskbar icon, then run again.")


def monitor_at(monitors, x, y):
    for monitor in monitors:
        left, top, right, bottom = monitor["bounds"]
        if left <= x < right and top <= y < bottom:
            return monitor
    raise ValueError("The mouse position is outside the connected monitors.")


def mouse_recovery_point(monitors, x, y, distance=200):
    """Choose a nearby point without crossing onto another screen or a desktop gap."""
    left, top, right, bottom = monitor_at(monitors, x, y)["bounds"]
    for point in ((x + distance, y), (x - distance, y), (x, y + distance), (x, y - distance)):
        if left <= point[0] < right and top <= point[1] < bottom:
            return point
    # Very small displays: use the farthest reachable corner within this screen.
    return max(((left, top), (right - 1, top), (left, bottom - 1), (right - 1, bottom - 1)),
               key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)


def enumerate_monitors():
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.EnumDisplayMonitors.argtypes = [ctypes.c_void_p, ctypes.c_void_p, callback_type, wintypes.LPARAM]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    monitors = []
    def collect(handle, dc, rectangle, data):
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            return False
        rect = info.rcMonitor
        monitors.append({"device": info.szDevice,
                         "bounds": [rect.left, rect.top, rect.right, rect.bottom],
                         "primary": bool(info.dwFlags & 1)})
        return True
    if not user32.EnumDisplayMonitors(None, None, callback_type(collect), 0) or not monitors:
        raise OSError("Cannot read the connected monitors.")
    return monitors


def remap_events(events, saved_monitors, current_monitors):
    """Keep each action on its saved Windows display when screen positions change."""
    saved = {m["device"]: m for m in saved_monitors}
    current = {m["device"]: m for m in current_monitors}
    result = []
    for original in events:
        event = dict(original)
        device = event.get("monitor")
        if device:
            if device not in current:
                raise ValueError(f"Recorded monitor {device} is disconnected. Reconnect it before playback.")
            if device not in saved:
                raise ValueError("The recording is missing its monitor information. Record it again.")
            old, new = saved[device]["bounds"], current[device]["bounds"]
            if (old[2] - old[0], old[3] - old[1]) != (new[2] - new[0], new[3] - new[1]):
                raise ValueError(f"Resolution changed on {device}. Restore it or record the sequence again.")
            if "x" in event:
                event["x"] += new[0] - old[0]
                event["y"] += new[1] - old[1]
        result.append(event)
    return result


def capture_monitor(device, monitors=None):
    monitors = enumerate_monitors() if monitors is None else monitors
    monitor = next((m for m in monitors if m["device"] == device), None)
    if monitor is None:
        raise ValueError(f"Screenshot monitor {device} is disconnected.")
    # all_screens=True supports negative coordinates on left / upper monitors.
    picture = ImageGrab.grab(bbox=tuple(monitor["bounds"]), all_screens=True)
    output = io.BytesIO()
    picture.save(output, format="PNG")
    return output.getvalue(), picture.size


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect_secret(data, decrypt=False):
    """DPAPI protection is scoped to the current Windows account."""
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError("Cannot access Telegram credentials for this Windows account.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32.LocalFree(target.data)


def save_telegram_credentials(path, token, chat_id):
    token = token.strip()
    chat_id = str(chat_id).strip()
    if ":" not in token or not chat_id:
        raise ValueError("Enter a bot token and chat ID.")
    encrypted = protect_secret(json.dumps({"token": token, "chat_id": chat_id}).encode("utf-8"))
    Path(path).write_bytes(encrypted)


class TelegramClient:
    def __init__(self, credentials_path):
        self.credentials_path = Path(credentials_path)

    def credentials(self):
        if not self.credentials_path.exists():
            raise ValueError("Telegram is not configured. Open Telegram settings first.")
        return json.loads(protect_secret(self.credentials_path.read_bytes(), decrypt=True))

    def request(self, method, fields=None, image=None, size=None):
        credentials = self.credentials()
        fields = dict(fields or {})
        if method in ("sendPhoto", "sendDocument", "getChat", "sendMessage", "editMessageText"):
            fields["chat_id"] = credentials["chat_id"]
        if image is None:
            body = json.dumps(fields).encode("utf-8")
            content_type = "application/json"
        else:
            boundary = "recorder" + uuid.uuid4().hex
            parts = []
            for name, value in fields.items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8"))
            file_field = "photo" if method == "sendPhoto" else "document"
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="screenshot.png"\r\nContent-Type: image/png\r\n\r\n'.encode())
            parts.extend([image, f"\r\n--{boundary}--\r\n".encode()])
            body = b"".join(parts)
            content_type = f"multipart/form-data; boundary={boundary}"
        request = urllib.request.Request(f"https://api.telegram.org/bot{credentials['token']}/{method}",
                                         data=body, headers={"Content-Type": content_type}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            if method == "editMessageText" and exc.code == 400:
                try:
                    detail = json.loads(exc.read()).get("description", "")
                except (OSError, ValueError):
                    detail = ""
                if "message is not modified" in detail.lower():
                    return {"message_id": fields["message_id"]}
            # Do not expose the request URL: it includes the bot token.
            messages = {400: "Telegram rejected the request. Open the bot chat and press Start; check the chat ID.",
                        401: "Telegram bot token is invalid.",
                        403: "Telegram cannot message this chat. Unblock the bot and press Start.",
                        429: "Telegram rate limit reached. Wait before the next update."}
            raise RuntimeError(messages.get(exc.code, f"Telegram request failed (HTTP {exc.code}).")) from None
        except (OSError, ValueError):
            raise RuntimeError("Telegram could not confirm delivery. Check the internet connection; the message may have arrived.") from None
        if not result.get("ok"):
            raise RuntimeError("Telegram did not accept the request. Check your bot and chat settings.")
        return result["result"]

    def send_status(self, text):
        return self.request("sendMessage", {"text": text})["message_id"]

    def edit_status(self, message_id, text):
        return self.request("editMessageText", {"message_id": message_id, "text": text})

    def send_image(self, image, size, caption):
        width, height = size
        method = "sendPhoto" if len(image) <= 10_000_000 and width + height <= 10000 and max(width, height) <= 20 * min(width, height) else "sendDocument"
        return self.request(method, {"caption": caption}, image, size)

    def send_cancellable(self, image, size, caption, stop):
        if stop.is_set():
            return False
        finished = threading.Event()
        outcome = []
        def upload():
            try:
                outcome.append(self.send_image(image, size, caption))
            except Exception as exc:
                outcome.append(exc)
            finally:
                finished.set()
        threading.Thread(target=upload, daemon=True).start()
        while not finished.wait(0.05):
            if stop.is_set():
                return False
        if isinstance(outcome[0], Exception):
            raise outcome[0]
        return not stop.is_set()
