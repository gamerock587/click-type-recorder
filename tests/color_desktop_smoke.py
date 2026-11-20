"""Checks F2 + ordinary Ctrl+V in a temporary local window, with real screen colors."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recorder
from pynput import keyboard


def run():
    original_base = recorder.BASE
    original_dialog = recorder.simpledialog.askinteger
    original_clipboard = recorder.pyperclip.paste()
    with tempfile.TemporaryDirectory() as folder:
        recorder.BASE = Path(folder)
        (recorder.BASE / "accounts.txt").write_text("name1\nname2\nname3\nname4\nname5\nname6\n", encoding="utf-8")
        root = recorder.tk.Tk()
        app = recorder.RecorderApp(root)
        app.countdown.set("1")
        app.gap.set("0")
        target = recorder.tk.Toplevel(root)
        target.title("Recorder color test — local input only")
        target.geometry("450x260+950+100")
        patch = recorder.tk.Frame(target, bg="#12ab34", width=200, height=50)
        patch.pack(pady=10)
        name = recorder.ttk.Entry(target)
        name.pack(pady=10)
        password = recorder.ttk.Entry(target)
        password.pack(pady=10)
        results = []
        def submit(event=None):
            results.append((name.get(), password.get()))
            name.delete(0, "end")
            password.delete(0, "end")
            name.focus_set()
        password.bind("<Return>", submit)
        keys = keyboard.Controller()
        def pump(seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                root.update()
                time.sleep(0.01)
        def until(predicate, timeout=20):
            deadline = time.monotonic() + timeout
            while not predicate() and time.monotonic() < deadline:
                pump(0.02)
            assert predicate(), app.status.get()
        try:
            pump(0.25)
            recorder.pyperclip.copy("name3")  # The user's copy happens BEFORE recording.
            app.toggle_record()
            pump(1.2)
            assert recorder.pyperclip.paste() == "name3"
            target.deiconify()
            target.lift()
            name.focus_force()
            pump(0.2)
            x, y = patch.winfo_rootx() + 20, patch.winfo_rooty() + 20
            app.io.move_to(x, y)
            pump(0.15)
            keys.tap(keyboard.Key.f2)
            pump(0.2)
            captured = [e for e in app.recording.events if e["kind"] == "color"]
            assert len(captured) == 1, captured
            assert captured[0]["rgb"] == [0x12, 0xab, 0x34], captured
            assert (captured[0]["x"], captured[0]["y"]) == (x, y), (captured[0], (x, y))
            with keys.pressed(keyboard.Key.ctrl): keys.tap("v")
            pump(0.2)
            assert name.get() == "name3", name.get()
            keys.tap(keyboard.Key.tab)
            pump(0.1)
            for vk in (80, 65, 83, 83, 49): keys.tap(keyboard.KeyCode.from_vk(vk))
            keys.tap(keyboard.Key.enter)
            pump(0.2)
            keys.tap(keyboard.Key.f6)
            pump(0.2)
            assert app.state == "idle"
            assert not any(e.get("vk") == 113 for e in app.events)
            assert not any(e["kind"] == "account" for e in app.events)
            fixed_password = results[0][1]
            assert fixed_password
            patch.configure(bg="#ff0000")
            recorder.pyperclip.copy("stale clipboard")
            recorder.simpledialog.askinteger = lambda *args, **kwargs: 5
            reads = []
            original_pixel = app.io.pixel
            def measured_pixel(px, py):
                color = original_pixel(px, py)
                reads.append((time.monotonic(), color))
                return color
            app.io.pixel = measured_pixel
            app.start_play()
            pump(0.2)
            target.deiconify()
            target.lift()
            name.focus_force()
            until(lambda: "Waiting for the selected color" in app.status.get())
            assert len(results) == 1 and name.get() == "", results
            assert recorder.pyperclip.paste() == "name5"
            pump(0.3)
            assert len(reads) == 1, reads
            assert name.get() == "", "Typing happened before color matched"
            patch.configure(bg="#12ab34")
            until(lambda: app.state == "idle")
            assert reads[1][0] - reads[0][0] >= 1.9, reads
            assert results[1:] == [("name5", fixed_password), ("name6", fixed_password)], results
            assert app.resume["next_account"] == 7
            print("PASS: F2 captures exact screen RGB and location; mismatch blocks typing; polls every 2 seconds.")
            print("PASS: pre-recorded Ctrl+V uses accounts 5 then 6, with the same password and saved resume progress.")
            app.events = [{**captured[0], "delay": 0}, {"kind": "key", "vk": 65, "pressed": True, "delay": 0}]
            patch.configure(bg="#ff0000")
            app.start_play()
            pump(0.2)
            target.deiconify()
            target.lift()
            name.focus_force()
            until(lambda: "Waiting for the selected color" in app.status.get())
            keys.tap(keyboard.Key.f9)
            until(lambda: app.state == "idle", 1)
            assert app.resume["next_account"] == 5
            assert name.get() == ""
            assert app.key_listener.is_alive()
            suggestions = []
            def cancel_resume(*args, **kwargs):
                suggestions.append(kwargs["initialvalue"])
                return None
            recorder.simpledialog.askinteger = cancel_resume
            app.start_play()
            assert suggestions == [5], suggestions
            print("PASS: F9 cancels a color wait immediately and keeps the interrupted account.")
        finally:
            app.close()
            recorder.BASE = original_base
            recorder.simpledialog.askinteger = original_dialog
            recorder.pyperclip.copy(original_clipboard)


if __name__ == "__main__":
    run()
