"""Recording history window. No playback or desktop input is performed here."""
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk


class RecordingHistoryWindow:
    def __init__(self, app):
        self.app = app
        self.library = app.library
        self.window = tk.Toplevel(app.root)
        self.window.title("Recording history")
        self.window.geometry("930x570")
        self.window.minsize(850, 450)
        self.window.transient(app.root)
        self.window.grab_set()
        app.start_dialog = True
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        outer = ttk.Frame(self.window, padding=20)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Your recordings", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Each finished recording is saved here automatically. Load one to use it again.").pack(anchor="w", pady=(4, 14))
        filters = ttk.Frame(outer)
        filters.pack(fill="x", pady=(0, 10))
        self.search = tk.StringVar()
        ttk.Label(filters, text="Search").pack(side="left", padx=(0, 8))
        ttk.Entry(filters, textvariable=self.search).pack(side="left", fill="x", expand=True)
        self.deleted = tk.BooleanVar(value=False)
        ttk.Checkbutton(filters, text="Show deleted", variable=self.deleted, command=self.refresh).pack(side="left", padx=16)
        ttk.Button(filters, text="Save current as new…", command=self.save_current).pack(side="right")
        table = ttk.Frame(outer)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("name", "saved", "clicks", "duration", "events"), show="headings", selectmode="extended")
        for key, title, width in (("name", "Name", 320), ("saved", "Saved", 165), ("clicks", "Clicks", 65),
                                  ("duration", "Duration", 85), ("events", "Events", 70)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=50, stretch=key == "name")
        self.tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(table, command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(12, 8))
        self.load_button = ttk.Button(actions, text="Load selected", command=self.load)
        self.load_button.pack(side="left")
        self.rename_button = ttk.Button(actions, text="Rename…", command=self.rename)
        self.rename_button.pack(side="left", padx=6)
        self.delete_button = ttk.Button(actions, text="Delete", command=self.delete)
        self.delete_button.pack(side="left")
        self.restore_button = ttk.Button(actions, text="Restore", command=self.restore)
        self.restore_button.pack(side="left", padx=6)
        ttk.Button(actions, text="Import…", command=self.import_file).pack(side="right")
        ttk.Button(actions, text="Export…", command=self.export).pack(side="right", padx=6)
        self.message = tk.StringVar(value="Deleted recordings can be restored using Show deleted. No recording starts automatically.")
        ttk.Label(outer, textvariable=self.message, wraplength=875).pack(anchor="w")
        self.search.trace_add("write", lambda *args: self.refresh())
        self.tree.bind("<Double-1>", lambda event: self.load())
        self.refresh()

    def close(self):
        self.app.start_dialog = False
        self.app.history_window = None
        self.window.destroy()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for row in self.library.list(self.deleted.get()):
            if self.search.get().casefold() not in row["name"].casefold():
                continue
            saved = datetime.fromisoformat(row["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
            seconds = int(row["duration"])
            self.tree.insert("", "end", iid=row["id"], values=(row["name"], saved, row["clicks"], f"{seconds//60}:{seconds%60:02}", row["events"]))
        for button in (self.load_button, self.rename_button, self.delete_button):
            button.configure(state="disabled" if self.deleted.get() else "normal")
        self.restore_button.configure(state="normal" if self.deleted.get() else "disabled")

    def selected(self):
        rows = self.tree.selection()
        if not rows:
            self.message.set("Select a recording first.")
            return None
        return rows[0]

    def load(self):
        identity = self.selected()
        if not identity or self.deleted.get(): return
        try:
            self.app.activate_library_recording(identity)
            self.close()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Load recording", str(exc), parent=self.window)

    def save_current(self):
        self.app.save_to_library(parent=self.window)
        self.refresh()

    def rename(self):
        identity = self.selected()
        if not identity or self.deleted.get(): return
        current = self.library.get(identity)["library"]["name"]
        name = simpledialog.askstring("Rename recording", "Recording name:", initialvalue=current, parent=self.window)
        if name is None: return
        try:
            self.library.rename(identity, name)
            if self.app.active_recording_id == identity:
                self.app.active_name.set(self.library.get(identity)["library"]["name"])
                self.app.persist_current()
            self.refresh()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Rename", str(exc), parent=self.window)

    def delete(self):
        if self.deleted.get(): return
        self.change_selected(self.library.delete, "Deleted. Use Show deleted → Restore to undo.")

    def restore(self):
        if not self.deleted.get(): return
        self.change_selected(self.library.restore, "Restored to your recording library.")

    def change_selected(self, operation, message):
        rows = self.tree.selection()
        if not rows:
            self.message.set("Select one or more recordings first.")
            return
        try:
            for identity in rows: operation(identity)
            self.refresh()
            self.message.set(message)
        except (OSError, ValueError) as exc:
            self.refresh()
            messagebox.showerror("Recording history", str(exc), parent=self.window)

    def import_file(self):
        paths = filedialog.askopenfilenames(parent=self.window, filetypes=[("Recordings", "*.json")])
        try:
            for path in paths: self.library.import_file(path)
            self.refresh()
            if paths: self.message.set(f"Imported {len(paths)} recording(s).")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Import", str(exc), parent=self.window)

    def export(self):
        identity = self.selected()
        if not identity: return
        path = filedialog.asksaveasfilename(parent=self.window, defaultextension=".json", filetypes=[("Recording", "*.json")])
        if not path: return
        try:
            data = self.library.get(identity, self.deleted.get())
            self.app.export_recording(path, data)
            self.message.set("Recording exported.")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Export", str(exc), parent=self.window)
