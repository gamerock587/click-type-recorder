"""Exercise recording-history GUI commands using temporary data; no desktop replay."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recorder
from recorder_core import load_recording, save_macro


def run():
    original_base = recorder.BASE
    original_prompt = recorder.simpledialog.askstring
    with tempfile.TemporaryDirectory() as folder:
        recorder.BASE = Path(folder)
        (recorder.BASE / "accounts.txt").write_text("test\n")
        events = [{"kind": "key", "vk": 65, "pressed": True, "delay": 0}]
        save_macro(recorder.BASE / "last_recording.json", events)
        root = recorder.tk.Tk()
        app = recorder.RecorderApp(root)
        try:
            recorder.simpledialog.askstring = lambda *args, **kwargs: "My second recording"
            app.save_to_library()
            identity = app.active_recording_id
            assert len(app.library.list()) == 2
            app.open_history()
            history = app.history_window
            root.update()
            assert len(history.tree.get_children()) == 2
            history.tree.selection_set(identity)
            recorder.simpledialog.askstring = lambda *args, **kwargs: "Renamed recording"
            history.rename()
            assert app.active_name.get() == "Renamed recording"
            history.tree.selection_set(identity)
            history.delete()
            assert len(history.tree.get_children()) == 1
            history.deleted.set(True)
            history.refresh()
            history.tree.selection_set(identity)
            history.restore()
            assert not history.tree.get_children()
            history.deleted.set(False)
            history.refresh()
            history.tree.selection_set(identity)
            history.load()
            assert app.history_window is None and not app.start_dialog
            assert app.events == events and app.state == "idle"
            assert load_recording(recorder.BASE / "last_recording.json")["events"] == events
            print("PASS: save multiple recordings, rename, delete, restore, and load through the GUI without starting playback.")
        finally:
            if app.history_window is not None: app.history_window.close()
            app.close()
            recorder.BASE = original_base
            recorder.simpledialog.askstring = original_prompt


if __name__ == "__main__":
    run()
