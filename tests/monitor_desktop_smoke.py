"""Exercise actual F3 on all monitors. --send-test sends ONE setup screenshot to the configured chat."""
import ctypes
import io
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recorder
from desktop_support import enumerate_monitors, monitor_at
from PIL import Image
from pynput import keyboard


def run(send_test=False):
    original_base = recorder.BASE
    with tempfile.TemporaryDirectory() as folder:
        recorder.BASE = Path(folder)
        (recorder.BASE / "accounts.txt").write_text("test-account\n")
        if send_test:
            (recorder.BASE / ".telegram_credentials").write_bytes((original_base / ".telegram_credentials").read_bytes())
        root = recorder.tk.Tk()
        app = recorder.RecorderApp(root)
        app.monitors = enumerate_monitors()
        app.record_monitors = list(app.monitors)
        app.record_monitor = monitor_at(app.monitors, *app.io.mouse.position)["device"]
        overlays = []
        colors = ["#234567", "#456789", "#6789ab"]
        user32 = ctypes.WinDLL("user32")
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.SetWindowPos.restype = ctypes.c_int
        def pump(seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                root.update()
                time.sleep(0.01)
        def until(predicate, timeout=30):
            deadline = time.monotonic() + timeout
            while not predicate() and time.monotonic() < deadline:
                pump(0.02)
            assert predicate(), app.telegram_status.get()
        calls = []
        original_send = app.io.telegram.send_image
        def fake_send(image, size, caption):
            calls.append((image, size, caption))
            return {"message_id": 1}
        app.io.telegram.send_image = fake_send
        try:
            for index, monitor in enumerate(app.monitors):
                overlay = recorder.tk.Toplevel(root)
                overlay.overrideredirect(True)
                overlay.configure(bg=colors[index % len(colors)])
                recorder.tk.Label(overlay, text="Recorder screenshot setup test", font=("Segoe UI", 30, "bold"),
                                  bg=colors[index % len(colors)], fg="white").pack(expand=True)
                recorder.tk.Label(overlay, text=f"{monitor['device']}  •  Full monitor capture\nTelegram screenshot delivery test",
                                  font=("Segoe UI", 18), bg=colors[index % len(colors)], fg="white").pack(expand=True)
                root.update()
                handle = user32.GetAncestor(overlay.winfo_id(), 2)
                left, top, right, bottom = monitor["bounds"]
                assert user32.SetWindowPos(handle, ctypes.c_void_p(-1), left, top, right-left, bottom-top, 0x40)
                overlays.append(overlay)
            pump(0.5)
            app.recording.start()
            app.state = "recording"
            keys = keyboard.Controller()
            for index, monitor in enumerate(app.monitors):
                left, top, right, bottom = monitor["bounds"]
                app.io.move_to(left + 50, top + 50)
                pump(0.15)
                before = len(calls)
                keys.tap(keyboard.Key.f3)
                until(lambda: len(calls) > before and not app.screenshot_busy)
                png, size, caption = calls[-1]
                assert size == (right-left, bottom-top), (size, monitor)
                expected = tuple(bytes.fromhex(colors[index % len(colors)][1:]))
                picture = Image.open(io.BytesIO(png)).convert("RGB")
                for point in ((5, 5), (size[0]-5, size[1]-5)):
                    assert picture.getpixel(point) == expected, (monitor, picture.getpixel(point), expected)
                assert monitor["device"] in caption
            app.events = app.recording.stop()
            app.state = "idle"
            shots = [e for e in app.events if e["kind"] == "screenshot"]
            assert [s["monitor"] for s in shots] == [m["device"] for m in app.monitors]
            assert not any(e.get("vk") == 114 for e in app.events)
            print(f"PASS: actual F3 captured each of {len(app.monitors)} full monitors and recorded the correct monitor IDs.")
            # Replay a saved screenshot while the mouse is on another monitor.
            app.io.move_to(app.monitors[-1]["bounds"][0] + 100, app.monitors[-1]["bounds"][1] + 100)
            assert app.io.screenshot(shots[0], app.stop, "test-account")
            first_color = tuple(bytes.fromhex(colors[0][1:]))
            assert Image.open(io.BytesIO(calls[-1][0])).convert("RGB").getpixel((5, 5)) == first_color
            print("PASS: replay captures the recorded monitor even when the mouse is on another screen.")
            if send_test:
                app.io.telegram.send_image = original_send
                app.io.move_to(app.monitors[0]["bounds"][0] + 50, app.monitors[0]["bounds"][1] + 50)
                pump(0.15)
                app.telegram_status.set("Waiting for F3")
                keys.tap(keyboard.Key.f3)
                until(lambda: not app.screenshot_busy and ("sent to Telegram" in app.telegram_status.get() or "failed" in app.telegram_status.get()), 35)
                assert "sent to Telegram" in app.telegram_status.get(), app.telegram_status.get()
                print("PASS: Telegram confirmed delivery of ONE full-monitor setup screenshot through F3.")
        finally:
            for overlay in overlays:
                overlay.destroy()
            app.close()
            recorder.BASE = original_base


if __name__ == "__main__":
    run("--send-test" in sys.argv)
