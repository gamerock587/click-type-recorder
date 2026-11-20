"""Manual integration check: uses only its own temporary Tk window as a target."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recorder
from pynput import keyboard


def run():
    original_base = recorder.BASE
    original_clipboard = recorder.pyperclip.paste()
    with tempfile.TemporaryDirectory() as folder:
        recorder.BASE = Path(folder)
        (recorder.BASE / "accounts.txt").write_text("name1\nname2\nname3\nname4\nname5\nname6\n", encoding="utf-8")
        root = recorder.tk.Tk()
        app = recorder.RecorderApp(root)
        app.countdown.set("1")
        app.gap.set("0")
        target = recorder.tk.Toplevel(root)
        target.title("Recorder integration test — local input only")
        target.geometry("450x220+950+100")
        name = recorder.ttk.Entry(target)
        name.pack(pady=20)
        password = recorder.ttk.Entry(target)
        password.pack(pady=20)
        results = []
        def submit(event=None):
            results.append((name.get(), password.get()))
            name.delete(0, "end")
            password.delete(0, "end")
            name.focus_set()
        password.bind("<Return>", submit)
        controller = keyboard.Controller()
        def pump(seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                root.update()
                time.sleep(0.01)
        try:
            pump(0.25)
            assert app.key_listener.is_alive() and app.mouse_listener.is_alive()
            recorder.pyperclip.copy("name1")
            app.toggle_record()
            pump(1.2)
            target.deiconify()
            target.lift()
            name.focus_force()
            pump(0.2)
            controller.tap(keyboard.Key.f8)
            pump(0.5)
            assert name.get() == "name1", f"F8 did not insert sample: {name.get()!r}"
            controller.tap(keyboard.Key.tab)
            pump(0.1)
            # KeyCode virtual keys match real physical key recording on Windows.
            for vk in (80, 65, 83, 83, 49, 50, 51):
                controller.tap(keyboard.KeyCode.from_vk(vk))
            controller.tap(keyboard.Key.enter)
            pump(0.3)
            controller.tap(keyboard.Key.f6)
            pump(0.3)
            assert app.state == "idle", app.state
            assert len(results) == 1, results
            recorded_password = results[0][1]
            assert recorded_password, results
            assert sum(e["kind"] == "account" for e in app.events) == 1
            assert not any(e.get("vk") in (17, 86) for e in app.events), "Synthetic paste got recorded"
            recorder.simpledialog.askinteger = lambda *args, **kwargs: 5
            app.start_play()
            pump(0.2)
            target.deiconify()
            target.lift()
            name.focus_force()
            deadline = time.monotonic() + 15
            while app.state != "idle" and time.monotonic() < deadline:
                pump(0.05)
            assert app.state == "idle", app.status.get()
            assert results[1:] == [("name5", recorded_password), ("name6", recorded_password)], results
            assert app.resume["next_account"] == 7, app.resume
            assert app.status.get().startswith("Done"), app.status.get()
            assert app.key_listener.is_alive(), "Keyboard hook failed"
            print("PASS: real F8 paste, fixed password recording, F6 stop, start at account 5, run through 6, save progress.")
            # Exercise F9 through the real global hook during a long pending action.
            app.events = [{"kind": "wait", "delay": 30}, {"kind": "key", "vk": 65, "pressed": True, "delay": 0}]
            app.start_play()
            pump(1.2)
            controller.tap(keyboard.Key.f9)
            pump(0.3)
            assert app.state == "idle", app.state
            assert app.resume["next_account"] == 5, app.resume
            print("PASS: global F9 interrupts wait and preserves interrupted account for resume.")
        finally:
            app.close()
            recorder.BASE = original_base
            recorder.pyperclip.copy(original_clipboard)


if __name__ == "__main__":
    run()
