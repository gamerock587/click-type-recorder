"""Local two-account playback. --send-test sends one edited status (no screenshots)."""
import ctypes
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recorder
from desktop_support import monitor_at


def run(send_test=False):
    original_base = recorder.BASE
    original_dialog = recorder.simpledialog.askinteger
    original_clipboard = recorder.pyperclip.paste()
    with tempfile.TemporaryDirectory() as folder:
        recorder.BASE = Path(folder)
        (recorder.BASE / "accounts.txt").write_text("skip1\nskip2\nsetup-test-name3\nsetup-test-name4\n", encoding="utf-8")
        credentials = (original_base / ".telegram_credentials").read_bytes() if send_test else b"mock"
        (recorder.BASE / ".telegram_credentials").write_bytes(credentials)
        root = recorder.tk.Tk()
        app = recorder.RecorderApp(root)
        app.countdown.set("1")
        app.gap.set("0")
        monitor = monitor_at(app.monitors, *app.io.mouse.position)
        left, top, right, bottom = monitor["bounds"]
        app.record_monitor = monitor["device"]
        app.record_monitors = app.monitors
        app.events = [{"kind": "wait", "delay": 3},
                      {"kind": "move", "delay": 3, "x": left + 50, "y": top + 50, "monitor": monitor["device"]}]
        overlay = recorder.tk.Toplevel(root)
        overlay.overrideredirect(True)
        overlay.configure(bg="#234567")
        recorder.tk.Label(overlay, text="Telegram progress setup test", font=("Segoe UI", 30, "bold"),
                          bg="#234567", fg="white").pack(expand=True)
        recorder.tk.Label(overlay, text="Two test accounts • One edited status message\nNo automatic completion screenshots",
                          font=("Segoe UI", 18), bg="#234567", fg="white").pack(expand=True)
        user32 = ctypes.WinDLL("user32")
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        calls = []
        client = app.io.telegram
        real_send, real_edit = client.send_status, client.edit_status
        def send(text):
            result = real_send(text) if send_test else 77
            calls.append(("send", result, text))
            return result
        def edit(message_id, text):
            result = real_edit(message_id, text) if send_test else {}
            calls.append(("edit", message_id, text))
            return result
        def image(png, size, caption):
            calls.append(("image", size, caption))
            raise AssertionError("Completion must not send a screenshot")
        client.send_status, client.edit_status, client.send_image = send, edit, image
        def pump(seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                root.update()
                time.sleep(0.01)
        try:
            recorder.simpledialog.askinteger = lambda *args, **kwargs: 3
            app.start_play()
            pump(0.1)
            overlay.deiconify()
            root.update()
            handle = user32.GetAncestor(overlay.winfo_id(), 2)
            assert user32.SetWindowPos(handle, ctypes.c_void_p(-1), left, top, right-left, bottom-top, 0x40)
            deadline = time.monotonic() + 50
            while time.monotonic() < deadline:
                pump(0.05)
                if app.state == "idle" and app.telegram_runs and not app.telegram_runs[-1].thread.is_alive():
                    break
            assert app.state == "idle", app.status.get()
            assert app.telegram_runs and not app.telegram_runs[-1].thread.is_alive()
            assert not app.telegram_runs[-1].error, app.telegram_runs[-1].error
            sends = [c for c in calls if c[0] == "send"]
            edits = [c for c in calls if c[0] == "edit"]
            photos = [c for c in calls if c[0] == "image"]
            assert len(sends) == 1, sends
            assert "Starting in account :: setup-test-name3" in sends[0][2]
            assert all(c[1] == sends[0][1] for c in edits)
            assert any("Progress: 25%" in c[2] for c in edits), edits
            assert "Progress: 100%" in edits[-1][2] and "Done ::" in edits[-1][2], edits[-1]
            assert not photos, photos
            assert app.resume["next_account"] == 5
            print("PASS: application sends one start message, edits the same message from 0% to 100%, and sends no completion screenshots.")
            if send_test: print("PASS: Telegram confirmed the status edits without screenshots.")
        finally:
            overlay.destroy()
            app.close()
            recorder.BASE = original_base
            recorder.simpledialog.askinteger = original_dialog
            recorder.pyperclip.copy(original_clipboard)


if __name__ == "__main__":
    run("--send-test" in sys.argv)
