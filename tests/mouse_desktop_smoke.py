"""Record and replay real left/right clicks and dragging inside local test windows."""
import ctypes
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recorder
from recorder_core import play
from pynput import mouse


def run():
    original_base = recorder.BASE
    with tempfile.TemporaryDirectory() as folder:
        recorder.BASE = Path(folder)
        (recorder.BASE / "accounts.txt").write_text("test\n")
        root = recorder.tk.Tk()
        app = recorder.RecorderApp(root)
        app.record_monitors = app.monitors
        received = []
        overlays = []
        user32 = ctypes.WinDLL("user32")
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        def pump(seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                root.update()
                time.sleep(0.005)
        try:
            for monitor in app.monitors:
                overlay = recorder.tk.Toplevel(root)
                overlay.overrideredirect(True)
                canvas = recorder.tk.Canvas(overlay, bg="#234567", highlightthickness=0)
                canvas.pack(fill="both", expand=True)
                canvas.create_text(300, 100, text="Mouse recording and playback test", fill="white", font=("Segoe UI", 22))
                for sequence, action in (("<ButtonPress-1>", "left-down"), ("<ButtonRelease-1>", "left-up"),
                                         ("<ButtonPress-3>", "right-down"), ("<ButtonRelease-3>", "right-up"),
                                         ("<B1-Motion>", "drag")):
                    canvas.bind(sequence, lambda e, action=action, device=monitor["device"]:
                                received.append((device, action, e.x_root, e.y_root)))
                root.update()
                left, top, right, bottom = monitor["bounds"]
                hwnd = user32.GetAncestor(overlay.winfo_id(), 2)
                assert user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), left, top, right-left, bottom-top, 0x40)
                overlays.append(overlay)
            pump(0.3)
            app.toggle_record()
            assert app.state == "recording" and app.recording.active, "F6 must start recording immediately, without a countdown"
            for overlay, monitor in zip(overlays, app.monitors):
                overlay.deiconify()
                root.update()
                left, top, right, bottom = monitor["bounds"]
                hwnd = user32.GetAncestor(overlay.winfo_id(), 2)
                user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), left, top, right-left, bottom-top, 0x40)
            control = mouse.Controller()
            for monitor in app.monitors:
                left, top, right, bottom = monitor["bounds"]
                # The taskbar's screen-coordinate band is covered by our local test canvas.
                control.position = (left+300, bottom-20)
                pump(0.05)
                control.press(mouse.Button.left)
                pump(0.05)
                control.release(mouse.Button.left)
                pump(0.05)
                control.position = (left+300, top+300)
                pump(0.1)
                control.press(mouse.Button.left)
                pump(0.08)
                control.release(mouse.Button.left)
                pump(0.08)
                control.position = (left+450, top+350)
                pump(0.08)
                control.press(mouse.Button.right)
                pump(0.08)
                control.release(mouse.Button.right)
                pump(0.08)
                control.position = (left+550, top+450)
                pump(0.08)
                control.press(mouse.Button.left)
                pump(0.08)
                control.position = (left+650, top+500)
                pump(0.08)
                control.release(mouse.Button.left)
                pump(0.08)
            events = app.recording.stop()
            app.state = "idle"
            expected = [e for e in received if e[1] != "drag"]
            assert len(expected) == 8 * len(app.monitors), expected
            assert len([e for e in events if e["kind"] == "click"]) == len(expected)
            received.clear()
            errors = []
            def replay():
                try: play(events, [(1, None)], app.io, threading.Event(), 0)
                except Exception as exc: errors.append(str(exc))
            worker = threading.Thread(target=replay)
            worker.start()
            deadline = time.monotonic() + 15
            while worker.is_alive() and time.monotonic() < deadline:
                pump(0.02)
            worker.join(0.1)
            pump(0.2)
            assert not errors, errors
            assert not worker.is_alive()
            actual = [e for e in received if e[1] != "drag"]
            assert actual == expected, ("expected", expected, "actual", actual)
            assert len([e for e in received if e[1] == "drag"]) >= len(app.monitors)
            print(f"PASS: immediate recording captured and replayed {len(expected)//2} real left/right clicks, including taskbar-band coordinates, and dragging across {len(app.monitors)} monitors.")
        finally:
            for overlay in overlays: overlay.destroy()
            app.close()
            recorder.BASE = original_base


if __name__ == "__main__":
    run()
