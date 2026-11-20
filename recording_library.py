"""Named recording snapshots, import history, and reversible deletion."""
import hashlib
import json
from datetime import datetime
from pathlib import Path
import uuid

from recorder_core import load_recording


class RecordingLibrary:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.trash = self.folder / "deleted"
        self.trash.mkdir(exist_ok=True)
        self.imports_file = self.folder / "_imports.json"

    @staticmethod
    def valid_name(name):
        name = str(name).strip()
        if not name or len(name) > 120 or any(c in name for c in "\r\n\x00"):
            raise ValueError("Choose a recording name between 1 and 120 characters.")
        return name

    def _path(self, identity, deleted=False):
        if not isinstance(identity, str) or len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
            raise ValueError("Invalid recording identifier.")
        parent = self.trash if deleted else self.folder
        path = parent / f"{identity}.json"
        if path.resolve().parent != parent.resolve():
            raise ValueError("Recording path is outside the library.")
        return path

    @staticmethod
    def _write(path, data):
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        load_recording(temporary)
        temporary.replace(path)

    def add(self, data, name=None, created_at=None):
        identity = uuid.uuid4().hex
        created = created_at or datetime.now().astimezone().isoformat(timespec="seconds")
        result = dict(data)
        result["library"] = {"id": identity, "name": self.valid_name(name if name is not None else datetime.now().strftime("Recording %Y-%m-%d %H.%M.%S")),
                             "created_at": created}
        self._write(self._path(identity), result)
        return result["library"]

    def get(self, identity, deleted=False):
        return load_recording(self._path(identity, deleted))

    def list(self, deleted=False):
        rows = []
        for path in (self.trash if deleted else self.folder).glob("*.json"):
            if path.name.startswith("_"):
                continue
            try:
                data = load_recording(path)
                entry = dict(data["library"])
                if self._path(entry["id"], deleted) != path:
                    continue
                entry.update(events=len(data["events"]),
                             clicks=sum(e["kind"] == "click" and e["pressed"] for e in data["events"]),
                             duration=sum(e["delay"] for e in data["events"]))
                rows.append(entry)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(rows, key=lambda row: (row["created_at"], row["id"]), reverse=True)

    def rename(self, identity, name):
        data = self.get(identity)
        data["library"]["name"] = self.valid_name(name)
        self._write(self._path(identity), data)

    def delete(self, identity):
        source, destination = self._path(identity), self._path(identity, True)
        if destination.exists():
            raise ValueError("A deleted recording with this ID already exists.")
        source.rename(destination)

    def restore(self, identity):
        source, destination = self._path(identity, True), self._path(identity)
        if destination.exists():
            raise ValueError("This recording is already in the library.")
        source.rename(destination)

    def _imports(self):
        if not self.imports_file.exists():
            return {}
        data = json.loads(self.imports_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Recording import history is invalid.")
        return data

    def remember_source(self, source):
        source = Path(source).resolve()
        imports = self._imports()
        imports[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
        temporary = self.imports_file.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(imports, indent=2), encoding="utf-8")
        temporary.replace(self.imports_file)

    def import_file(self, source, name=None, only_new=False):
        source = Path(source).resolve()
        data = load_recording(source)
        signature = hashlib.sha256(source.read_bytes()).hexdigest()
        if only_new and self._imports().get(str(source)) == signature:
            return None
        entry = self.add(data, name or source.stem,
                         datetime.fromtimestamp(source.stat().st_mtime).astimezone().isoformat(timespec="seconds"))
        self.remember_source(source)
        return entry

    def migrate_existing(self, base):
        """Import existing recordings and backups once; never resurrect deleted entries."""
        base = Path(base)
        candidates = list(base.glob("*.json")) + list((base / "recording_backups").glob("*.json"))
        for source in sorted(candidates):
            try:
                load_recording(source)
            except (OSError, ValueError, TypeError):
                continue
            name = "Current recording" if source.name == "last_recording.json" else source.stem
            if source.parent.name == "recording_backups":
                name = "Backup: " + name
            self.import_file(source, name=name, only_new=True)
