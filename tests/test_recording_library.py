import tempfile
import unittest
from pathlib import Path

from recording_library import RecordingLibrary
from recorder_core import save_macro


DATA = {"version": 2, "events": [{"kind": "key", "vk": 65, "pressed": True, "delay": 0.5},
                                  {"kind": "key", "vk": 65, "pressed": False, "delay": 0.1}],
        "monitors": [], "recording_monitor": None}


class LibraryTests(unittest.TestCase):
    def test_multiple_snapshots_rename_delete_restore_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            library = RecordingLibrary(Path(folder) / "recordings")
            first = library.add(DATA, "First")
            second = library.add(DATA, "Second")
            self.assertEqual(len(library.list()), 2)
            library.rename(first["id"], "Renamed")
            self.assertEqual(library.get(first["id"])["library"]["name"], "Renamed")
            library.delete(first["id"])
            self.assertEqual([r["id"] for r in library.list()], [second["id"]])
            self.assertEqual(library.list(True)[0]["id"], first["id"])
            library = RecordingLibrary(Path(folder) / "recordings")
            library.restore(first["id"])
            self.assertEqual(library.get(first["id"])["events"], DATA["events"])
            self.assertEqual(len(library.list()), 2)

    def test_imports_existing_and_backups_once_without_resurrecting_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "recording_backups").mkdir()
            save_macro(base / "last_recording.json", DATA["events"])
            save_macro(base / "last_recording0.json", DATA["events"])
            save_macro(base / "recording_backups" / "older.json", DATA["events"])
            (base / "settings.json").write_text('{"auto_image":"Each account"}')
            library = RecordingLibrary(base / "recordings")
            library.migrate_existing(base)
            self.assertEqual(len(library.list()), 3)
            deleted = library.list()[0]["id"]
            library.delete(deleted)
            library.migrate_existing(base)
            self.assertEqual(len(library.list()), 2)
            self.assertEqual(len(library.list(True)), 1)
            self.assertTrue((base / "last_recording.json").exists())

    def test_invalid_ids_cannot_change_files_outside_library(self):
        with tempfile.TemporaryDirectory() as folder:
            library = RecordingLibrary(Path(folder) / "recordings")
            for invalid in ("../last_recording", "C:\\outside", "", None):
                with self.assertRaises(ValueError): library.delete(invalid)
            for name in ("", "a\nb", "x" * 121):
                with self.assertRaises(ValueError): library.add(DATA, name)


if __name__ == "__main__":
    unittest.main()
