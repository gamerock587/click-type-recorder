"""Windows click and keyboard recorder. Run with Start Recorder.bat."""
import ctypes
import hashlib
import json
import queue
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

# Match listener and controller coordinates on scaled Windows displays.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):
    pass

import pyperclip
from pynput import keyboard, mouse
from recorder_core import Recording, load_recording, make_plan, play, read_accounts, save_macro
from desktop_support import (TelegramClient, capture_monitor, enumerate_monitors, monitor_at,
                             remap_events, save_telegram_credentials, launch_or_focus, RecorderInstance,
                             mouse_recovery_point)
from telegram_progress import TelegramRunStatus
from recording_library import RecordingLibrary
from recording_history_ui import RecordingHistoryWindow
from windows_mouse import WindowsMouse, MouseMovementError

BASE = Path(__file__).resolve().parent


class DesktopIO:
    def __init__(self):
        self.keys = keyboard.Controller()
        self.mouse = mouse.Controller()
        self.pointer = WindowsMouse()
        self.stop = threading.Event()
        self.held_keys = set()
        self.held_buttons = set()
        self.telegram = TelegramClient(BASE / ".telegram_credentials")
        self.report = lambda *args: None
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.user32.GetDC.argtypes = [ctypes.c_void_p]
        self.user32.GetDC.restype = ctypes.c_void_p
        self.user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.user32.ReleaseDC.restype = ctypes.c_int
        self.gdi32.GetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        self.gdi32.GetPixel.restype = ctypes.c_uint32

    def move_to(self, x, y):
        try:
            return self.pointer.move_to(x, y, self.stop)
        except (MouseMovementError, OSError):
            return self.recover_mouse(x, y)

    def recover_mouse(self, x, y):
        """Keep the pending step active until movement recovers or F9 cancels."""
        attempt = 0
        while not self.stop.is_set():
            attempt += 1
            self.report("mouse_recovery", f"Mouse recovery #{attempt}: move 200 px, right-click, return to ({x}, {y}). F9 stops.")
            try:
                reached = self.recover_mouse_once(x, y)
                if reached:
                    self.report("mouse_recovery", "Mouse recovered — continuing the interrupted action. F9 stops.")
                return reached
            except (MouseMovementError, OSError) as exc:
                self.report("mouse_recovery", f"Mouse still blocked — recovery #{attempt}. Retrying in 2 seconds. F9 stops.\n{exc}")
            if self.stop.wait(2.0):
                return False
        return False

    def recover_mouse_once(self, x, y):
        """One recovery cycle; never replay an already delivered action."""
        if self.stop.is_set():
            return False
        nudge = mouse_recovery_point(enumerate_monitors(), *self.pointer.position)
        # A recovery cancels any current drag/modifier to avoid modified recovery clicks.
        try:
            self.release_all()
        except RuntimeError as exc:
            raise OSError(f"Waiting to release held input: {exc}") from exc
        try:
            if not self.pointer.recovery_move_to(*nudge, self.stop):
                return False
        except MouseMovementError:
            # The right-click can still release an app's cursor capture.
            pass
        if self.stop.wait(0.15):
            return False
        if self.stop.is_set():
            return False
        self.held_buttons.add("right")
        try:
            self.pointer.press("right")
            self.stop.wait(0.08)
        finally:
            self.pointer.release("right")
            self.held_buttons.discard("right")
        if self.stop.wait(0.15):
            return False
        # Close a context menu opened by the recovery right-click before continuing.
        self.held_keys.add(27)
        try:
            self.keys.press(keyboard.Key.esc)
            self.stop.wait(0.05)
        finally:
            self.keys.release(keyboard.Key.esc)
            self.held_keys.discard(27)
        if self.stop.wait(0.10):
            return False
        return self.pointer.recovery_move_to(x, y, self.stop)

    def screenshot(self, event, stop, account=None):
        if stop.is_set():
            return False
        device = event["monitor"]
        self.report("telegram_status", f"Capturing {device} and sending to Telegram…")
        image, size = capture_monitor(device)
        caption = f"Recorder screenshot • {device} • {datetime.now():%Y-%m-%d %H:%M:%S}"
        if account is not None:
            caption += f"\nAccount: {account}"
        sent = self.telegram.send_cancellable(image, size, caption, stop)
        if sent:
            self.report("telegram_status", f"Screenshot sent to Telegram — {device} ({size[0]} × {size[1]}).")
        return sent

    def pixel(self, x, y):
        dc = self.user32.GetDC(None)
        if not dc:
            raise OSError("Cannot read the screen color.")
        try:
            color = self.gdi32.GetPixel(dc, x, y)
            if color == 0xFFFFFFFF:
                raise OSError(f"Cannot read color at ({x}, {y}). Check the screen layout.")
            return (color & 255, (color >> 8) & 255, (color >> 16) & 255)
        finally:
            self.user32.ReleaseDC(None, dc)

    def copy(self, text):
        pyperclip.copy(text)

    def paste_account(self, account, stop):
        self.copy(account)
        if stop.wait(0.08):
            return
        try:
            self.keys.press(keyboard.Key.ctrl)
            self.keys.press("v")
            self.keys.release("v")
        finally:
            self.keys.release(keyboard.Key.ctrl)
        stop.wait(0.12)

    def perform(self, event):
        kind = event["kind"]
        if kind == "launch":
            launch_or_focus(event["path"])
            return
        if kind in ("move", "click", "scroll"):
            if not self.move_to(event["x"], event["y"]):
                return
        if self.stop.is_set():
            return
        if kind == "click":
            button = event["button"]
            if event["pressed"]:
                self.held_buttons.add(button)
                self.pointer.press(button)
            else:
                self.pointer.release(button)
                self.held_buttons.discard(button)
        elif kind == "scroll":
            self.pointer.scroll(event["dx"], event["dy"])
        elif kind == "key":
            vk = event["vk"]
            key = keyboard.KeyCode.from_vk(vk)
            if event["pressed"]:
                self.held_keys.add(vk)
                self.keys.press(key)
            else:
                self.keys.release(key)
                self.held_keys.discard(vk)

    def release_all(self):
        errors = []
        for vk in list(self.held_keys):
            try:
                self.keys.release(keyboard.KeyCode.from_vk(vk))
                self.held_keys.discard(vk)
            except Exception as exc:
                errors.append(str(exc))
        for button in list(self.held_buttons):
            try:
                self.pointer.release(button)
                self.held_buttons.discard(button)
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("Could not release all held inputs: " + "; ".join(errors))


class RecorderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Click & Type Recorder")
        self.root.geometry("930x890")
        self.root.minsize(900, 850)
        self.root.configure(bg="#f3f5fa")
        self.queue = queue.Queue()
        self.recording = Recording()
        self.io = DesktopIO()
        self.io.report = lambda *args: self.queue.put(args)
        self.monitors = enumerate_monitors()
        self.record_monitors = []
        self.record_monitor = None
        self.screenshot_busy = False
        self.screenshot_stop = threading.Event()
        self.telegram_runs = []
        self.hud = None
        self.hud_text = tk.StringVar(value="")
        self.last_hud_refresh = 0
        self.stop = self.io.stop
        self.events = []
        self.accounts = []
        self.state = "idle"
        self.closed = False
        self.worker = None
        self.start_dialog = False
        self.countdown_generation = 0
        self.library = RecordingLibrary(BASE / "recordings")
        self.history_window = None
        self.active_recording_id = None
        self.active_name = tk.StringVar(value="No recording loaded")
        self.path = tk.StringVar(value=str(BASE / "accounts.txt"))
        self.mode = tk.StringVar(value="accounts")
        self.count = tk.StringVar(value="10")
        self.gap = tk.StringVar(value="2")
        self.countdown = tk.StringVar(value="3")
        self.status = tk.StringVar(value="Ready. Record a sequence to begin.")
        self.details = tk.StringVar(value="No recording yet")
        self.color_details = tk.StringVar(value="F2: point at the desired color while recording to add a wait step.")
        self.monitor_details = tk.StringVar(value=f"{len(self.monitors)} monitors detected. The recording remembers each action's monitor.")
        self.telegram_status = tk.StringVar(value="F3 sends the full monitor under your mouse to Telegram.")
        self.resume = {}
        try:
            self.resume = json.loads((BASE / "progress.json").read_text(encoding="utf-8"))
            if not isinstance(self.resume, dict):
                self.resume = {}
        except (OSError, ValueError):
            pass
        self.build_ui()
        self.reload_accounts()
        try:
            self.library.migrate_existing(BASE)
        except (OSError, ValueError) as exc:
            self.status.set(f"Could not import recording history: {exc}")
        try:
            if (BASE / "last_recording.json").exists():
                self.restore_recording(BASE / "last_recording.json")
                self.describe()
                self.status.set("Previous recording loaded. Press F7 to run, or F6 to record a new sequence.")
        except (OSError, ValueError) as exc:
            self.status.set(f"Could not restore recording: {exc}")
        self.key_listener = keyboard.Listener(on_press=lambda k: self.record_key(k, True),
                                              on_release=lambda k: self.record_key(k, False),
                                              win32_event_filter=self.hotkey_filter)
        self.mouse_listener = mouse.Listener(
            on_move=lambda x, y: self.record_point("move", x, y),
            on_click=lambda x, y, b, p: self.record_point("click", x, y, button=b.name, pressed=p),
            on_scroll=lambda x, y, dx, dy: self.record_point("scroll", x, y, dx=int(dx), dy=int(dy)))
        self.key_listener.start()
        self.mouse_listener.start()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(40, self.poll)

    def build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f5fa")
        style.configure("TLabel", background="#f3f5fa", foreground="#202a40", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 9))
        style.configure("TRadiobutton", background="#f3f5fa", font=("Segoe UI", 10))
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Click & Type Recorder", font=("Segoe UI", 23, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Record once. Repeat a set number of times, or work through your account list.").pack(anchor="w", pady=(4, 16))
        help_text = ("F6  Record / finish     •     F7  Run     •     F9  Stop     •     F2  Color     •     F3  Screenshot → Telegram\n"
                     "Copy a name before recording, then paste normally with Ctrl+V. Type the fixed password.\n"
                     "Each account loop copies its current name first. F8 is an optional account paste shortcut.\n"
                     "F2 saves the color under your mouse. Playback waits for that color, checking every 2 seconds.\n"
                     "F3 captures the mouse's whole monitor. While recording, it also saves a screenshot step for replay.")
        tk.Label(outer, text=help_text, justify="left", anchor="w", bg="#e3eafa", fg="#25375a",
                 font=("Segoe UI", 10), padx=14, pady=12).pack(fill="x", pady=(0, 16))
        row = ttk.Frame(outer)
        row.pack(fill="x")
        ttk.Radiobutton(row, text="Account list → run to the end", variable=self.mode, value="accounts").pack(side="left")
        ttk.Radiobutton(row, text="Repeat recording", variable=self.mode, value="repeat").pack(side="left", padx=(24, 8))
        ttk.Spinbox(row, from_=1, to=100000, width=7, textvariable=self.count).pack(side="left")
        ttk.Label(row, text="times").pack(side="left", padx=6)
        files = ttk.Frame(outer)
        files.pack(fill="x", pady=(14, 8))
        ttk.Entry(files, textvariable=self.path).pack(side="left", fill="x", expand=True)
        ttk.Button(files, text="Browse…", command=self.browse_accounts).pack(side="left", padx=(8, 0))
        ttk.Button(files, text="Reload", command=self.reload_accounts).pack(side="left", padx=(8, 0))
        self.account_label = ttk.Label(outer, text="Accounts — one name per non-empty line")
        self.account_label.pack(anchor="w", pady=(0, 5))
        listing = ttk.Frame(outer)
        listing.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(listing, height=7, font=("Consolas", 11), relief="flat", bg="white",
                                  selectbackground="#dce7fc", selectforeground="#183366", exportselection=False)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(listing, command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=scrollbar.set)
        settings = ttk.Frame(outer)
        settings.pack(fill="x", pady=14)
        ttk.Label(settings, text="Delay between loops (seconds)").pack(side="left")
        ttk.Spinbox(settings, from_=0, to=3600, increment=0.5, width=6, textvariable=self.gap).pack(side="left", padx=(8, 22))
        ttk.Label(settings, text="Playback countdown").pack(side="left")
        ttk.Spinbox(settings, from_=1, to=30, width=5, textvariable=self.countdown).pack(side="left", padx=8)
        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        self.record_button = ttk.Button(actions, text="● Record  F6", command=self.toggle_record)
        self.record_button.pack(side="left")
        self.play_button = ttk.Button(actions, text="▶ Run  F7", command=self.start_play)
        self.play_button.pack(side="left", padx=8)
        ttk.Button(actions, text="■ Stop  F9", command=self.stop_now).pack(side="left")
        self.save_button = ttk.Button(actions, text="Save as…", command=self.save)
        self.save_button.pack(side="right")
        self.load_button = ttk.Button(actions, text="Load…", command=self.load)
        self.load_button.pack(side="right", padx=8)
        self.history_button = ttk.Button(actions, text="History / Manage", command=self.open_history)
        self.history_button.pack(side="right")
        active = ttk.Frame(outer)
        active.pack(fill="x", pady=(8, 0))
        ttk.Label(active, text="Current recording: ").pack(side="left")
        ttk.Label(active, textvariable=self.active_name, font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Label(outer, textvariable=self.details).pack(anchor="w", pady=(12, 5))
        colors = ttk.Frame(outer)
        colors.pack(fill="x", pady=(0, 5))
        self.color_swatch = tk.Label(colors, bg="#d8dce6", width=3, relief="solid", borderwidth=1)
        self.color_swatch.pack(side="left", padx=(0, 8))
        ttk.Label(colors, textvariable=self.color_details, wraplength=745).pack(side="left")
        ttk.Label(outer, textvariable=self.monitor_details, wraplength=870).pack(anchor="w", pady=(2, 4))
        telegram = ttk.Frame(outer)
        telegram.pack(fill="x", pady=(0, 5))
        ttk.Label(telegram, textvariable=self.telegram_status, wraplength=710).pack(side="left", fill="x", expand=True)
        ttk.Button(telegram, text="Telegram…", command=self.telegram_settings).pack(side="right")
        notification = ttk.Frame(outer)
        notification.pack(fill="x", pady=(0, 6))
        ttk.Label(notification, text="Telegram: live progress updates • Screenshots only from F3 (including recorded F3 steps)").pack(side="left")
        self.bar = ttk.Progressbar(outer, mode="determinate")
        self.bar.pack(fill="x", pady=(4, 10))
        ttk.Label(outer, textvariable=self.status, wraplength=760, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(outer, text="F6 starts recording immediately — switching apps through the taskbar is recorded too.\n"
                              "Press F6 / F9 in the target app to finish. Keep the window in the same position for playback.",
                  foreground="#657087", font=("Segoe UI", 9)).pack(anchor="w", pady=(10, 0))

    def hotkey_filter(self, msg, data):
        commands = {113: "color", 114: "screenshot", 117: "record", 118: "play", 119: "account", 120: "stop"}
        if data.vkCode in commands:
            if msg in (0x101, 0x105):  # Trigger once on release; suppress hotkeys in the target app.
                if data.vkCode == 120:
                    self.stop.set()
                    self.screenshot_stop.set()
                    self.recording.stop()
                if data.vkCode == 113:
                    # Capture before later clicks/keys can be added to the recording.
                    # A single native pixel read avoids a full-screen screenshot in the hook.
                    if self.recording.active and not self.recording.muted:
                        try:
                            x, y = self.io.mouse.position
                            rgb = self.io.pixel(int(x), int(y))
                            self.record_point("color", x, y, rgb=list(rgb))
                            self.queue.put(("color_captured", x, y, rgb))
                        except Exception as exc:
                            self.recording.stop()
                            self.queue.put(("error", f"Color was not recorded: {exc}"))
                elif data.vkCode == 114:
                    if not self.screenshot_busy:
                        try:
                            monitor = monitor_at(self.monitors, *self.io.mouse.position)
                            self.recording.add("screenshot", monitor=monitor["device"])
                            self.screenshot_busy = True
                            self.screenshot_stop.clear()
                            self.queue.put(("screenshot", monitor["device"]))
                        except ValueError as exc:
                            self.queue.put(("telegram_status", str(exc)))
                    else:
                        self.queue.put(("telegram_status", "A screenshot is still sending. Wait for delivery before pressing F3 again."))
                else:
                    self.queue.put((commands[data.vkCode],))
            self.key_listener.suppress_event()
        return True

    def show_hud(self, text, recording_monitor=None):
        """A non-activating status window on an unused monitor, with no taskbar icon."""
        if self.state not in ("recording", "playing", "countdown"):
            return
        if self.hud is None:
            self.hud = tk.Toplevel(self.root)
            self.hud.withdraw()
            self.hud.overrideredirect(True)
            self.hud.attributes("-topmost", True)
            self.hud.attributes("-toolwindow", True)
            tk.Label(self.hud, textvariable=self.hud_text, bg="#142238", fg="white", justify="left",
                     anchor="w", font=("Segoe UI", 11, "bold"), padx=14, pady=10, wraplength=420).pack(fill="both", expand=True)
        self.hud_text.set(text)
        used = {e.get("monitor") for e in self.events}
        used.add(recording_monitor or self.record_monitor)
        screen = next((m for m in self.monitors if m["device"] not in used), self.monitors[0])
        self.hud.update_idletasks()
        user32 = self.io.user32
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        handle = user32.GetAncestor(self.hud.winfo_id(), 2)
        user32.ShowWindow(handle, 4)  # SW_SHOWNOACTIVATE: never steal app focus.
        user32.SetWindowPos(handle, ctypes.c_void_p(-1), screen["bounds"][0]+20, screen["bounds"][1]+20,
                            450, 96, 0x10 | 0x40)

    def hide_hud(self):
        if self.hud is not None:
            self.hud.withdraw()

    def record_key(self, key, pressed):
        vk = getattr(key, "vk", None)
        if vk is None:
            vk = getattr(getattr(key, "value", None), "vk", None)
        if vk is not None and vk not in (113, 114, 117, 118, 119, 120):
            self.recording.add("key", vk=vk, pressed=pressed)

    def record_point(self, kind, x, y, **values):
        if not self.recording.active:
            return
        try:
            monitor = monitor_at(self.record_monitors, int(x), int(y))
            self.recording.add(kind, x=int(x), y=int(y), monitor=monitor["device"], **values)
        except ValueError:
            self.recording.stop()
            self.queue.put(("error", "The monitor layout changed while recording. Start a new recording."))

    def restore_recording(self, path):
        data = load_recording(path)
        self.events = data["events"]
        self.record_monitors = data["monitors"]
        self.record_monitor = data["recording_monitor"]
        self.active_recording_id = data.get("library", {}).get("id")
        self.active_name.set(data.get("library", {}).get("name", Path(path).stem))

    def screenshot_now(self, device):
        if self.screenshot_stop.is_set():
            self.screenshot_busy = False
            self.telegram_status.set("Screenshot cancelled.")
            return
        # Capture now; network upload must not block the keyboard hook or Tk event loop.
        try:
            image, size = capture_monitor(device)
            self.telegram_status.set(f"Sending {device} to Telegram…")
        except Exception as exc:
            self.screenshot_busy = False
            self.telegram_status.set(f"Screenshot failed: {exc}")
            return
        def send():
            try:
                caption = f"Recorder screenshot • {device} • {datetime.now():%Y-%m-%d %H:%M:%S}"
                sent = self.io.telegram.send_cancellable(image, size, caption, self.screenshot_stop)
                self.queue.put(("telegram_status", f"Screenshot sent to Telegram — {device} ({size[0]} × {size[1]})." if sent else "Screenshot wait stopped. An upload already sent may still arrive."))
            except Exception as exc:
                self.queue.put(("telegram_status", f"Screenshot delivery failed: {exc}"))
            finally:
                self.queue.put(("screenshot_finished",))
        threading.Thread(target=send, daemon=True).start()

    def telegram_settings(self):
        if self.state != "idle":
            return
        window = tk.Toplevel(self.root)
        window.title("Telegram settings")
        window.transient(self.root)
        window.grab_set()
        body = ttk.Frame(window, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Bot token (leave blank to keep the saved token)").pack(anchor="w")
        token = ttk.Entry(body, show="*", width=55)
        token.pack(fill="x", pady=(4, 12))
        ttk.Label(body, text="Chat ID").pack(anchor="w")
        chat = ttk.Entry(body, width=55)
        chat.pack(fill="x", pady=4)
        existing = {}
        try:
            existing = self.io.telegram.credentials()
            chat.insert(0, existing["chat_id"])
        except (OSError, ValueError):
            pass
        def save():
            try:
                save_telegram_credentials(BASE / ".telegram_credentials", token.get() or existing.get("token", ""), chat.get())
                self.telegram_status.set("Telegram configured. F3 sends the monitor under your mouse.")
                window.destroy()
            except (OSError, ValueError) as exc:
                messagebox.showerror("Telegram", str(exc), parent=window)
        ttk.Button(body, text="Save", command=save).pack(anchor="e", pady=(12, 0))

    def poll(self):
        if self.closed:
            return
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == "record": self.toggle_record()
                elif kind == "play": self.start_play()
                elif kind == "stop": self.stop_now()
                elif kind == "account": self.insert_account()
                elif kind == "screenshot": self.screenshot_now(item[1])
                elif kind == "screenshot_finished": self.screenshot_busy = False
                elif kind == "telegram_status": self.telegram_status.set(item[1])
                elif kind == "mouse_recovery":
                    self.status.set(item[1])
                    self.hud_text.set(item[1])
                elif kind == "color_captured":
                    _, x, y, rgb = item
                    color = "#{:02X}{:02X}{:02X}".format(*rgb)
                    self.color_swatch.configure(bg=color)
                    self.color_details.set(f"Saved {color} at ({x}, {y}) — playback checks every 2 seconds.")
                    self.status.set("Color wait recorded. Continue your next click or typing; F6 finishes recording.")
                elif kind == "color_checked":
                    _, event, current, matched = item
                    target = "#{:02X}{:02X}{:02X}".format(*event["rgb"])
                    actual = "#{:02X}{:02X}{:02X}".format(*current)
                    self.color_swatch.configure(bg=target)
                    self.color_details.set(f"Target {target} at ({event['x']}, {event['y']}) • current {actual}")
                    self.status.set("Color matched — continuing recorded steps." if matched else
                                    "Waiting for the selected color… checking every 2 seconds. F9 stops.")
                    self.hud_text.set("COLOR MATCHED — continuing. F9 stops." if matched else
                                      f"WAITING FOR COLOR at ({event['x']}, {event['y']})\nNeed {target} • now {actual}\nChecks every 2 seconds. F9 stops.")
                elif kind == "progress":
                    _, pos, total, number, account = item
                    self.bar.configure(maximum=total, value=pos - 1)
                    self.status.set(f"Running {pos}/{total}" + (f" — account #{number}: {account}" if account is not None else ""))
                    self.hud_text.set(f"PLAYING {pos}/{total} — {account or 'fixed repeat'}\nReplaying recorded actions. F9 stops.")
                elif kind == "completed":
                    _, number, account, fingerprint = item
                    self.bar.configure(value=float(self.bar.cget("value")) + 1)
                    if account is not None:
                        self.resume = {"fingerprint": fingerprint, "next_account": number + 1}
                        try:
                            (BASE / "progress.json").write_text(json.dumps(self.resume), encoding="utf-8")
                        except OSError as exc:
                            self.stop.set()
                            messagebox.showerror("Cannot save progress", str(exc), parent=self.root)
                elif kind == "finished":
                    self.state = "idle"
                    self.hide_hud()
                    self.root.deiconify()
                    self.status.set(item[1])
                    if item[2]:
                        self.bar.configure(value=self.bar.cget("maximum"))
                    self.buttons()
                elif kind == "inserted":
                    self.recording.muted = False
                elif kind == "error":
                    self.recording.muted = False
                    self.stop_now()
                    messagebox.showerror("Recorder", item[1], parent=self.root)
        except queue.Empty:
            pass
        if self.state == "recording":
            now = self.recording.clock()
            if now - self.last_hud_refresh >= 0.25:
                self.last_hud_refresh = now
                with self.recording.lock:
                    clicks = sum(e["kind"] == "click" and e["pressed"] for e in self.recording.events)
                    events = len(self.recording.events)
                self.hud_text.set(f"RECORDING NOW — {clicks} clicks captured\n{events} events • F6 finishes • F9 stops")
        self.root.after(40, self.poll)

    def reload_accounts(self):
        if self.state != "idle":
            return
        try:
            self.accounts = read_accounts(self.path.get())
        except (OSError, UnicodeError) as exc:
            self.accounts = []
            self.status.set(f"Cannot read accounts: {exc}")
        self.listbox.delete(0, "end")
        for number, account in enumerate(self.accounts, 1):
            self.listbox.insert("end", f"{number:>4}.  {account}")
        self.account_label.configure(text=f"{len(self.accounts)} accounts — choose a starting number each time you run")

    def browse_accounts(self):
        if self.state != "idle": return
        path = filedialog.askopenfilename(parent=self.root, initialdir=BASE, filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            self.path.set(path)
            self.reload_accounts()

    def buttons(self):
        idle = self.state == "idle"
        self.record_button.configure(text="Finish recording  F6" if self.state == "recording" else "● Record  F6",
                                     state="normal" if idle or self.state == "recording" else "disabled")
        for button in (self.play_button, self.save_button, self.load_button, self.history_button):
            button.configure(state="normal" if idle else "disabled")

    def countdown_start(self, label, callback):
        try:
            seconds = int(self.countdown.get())
            if not 1 <= seconds <= 30: raise ValueError()
        except ValueError:
            messagebox.showerror("Countdown", "Choose a countdown from 1 to 30 seconds.", parent=self.root)
            return
        self.stop.clear()
        self.countdown_generation += 1
        generation = self.countdown_generation
        self.state = "countdown"
        self.buttons()
        self.root.iconify()
        def tick(remaining):
            if self.stop.is_set() or self.state != "countdown" or generation != self.countdown_generation: return
            self.status.set(f"{label} in {remaining}… F9 cancels.")
            if remaining:
                self.root.after(1000, lambda: tick(remaining - 1))
            else:
                callback()
        tick(seconds)

    def toggle_record(self):
        if self.start_dialog: return
        if self.state == "idle" and self.worker and self.worker.is_alive(): return
        if self.state == "recording":
            self.finish_record()
        elif self.state == "idle":
            self.reload_accounts()
            selection = self.listbox.curselection()
            self.sample = self.accounts[selection[0] if selection else 0] if self.accounts else None
            # Do not replace a name that the user copied before recording.
            try:
                copied_name = pyperclip.paste()
                if copied_name in self.accounts:
                    self.sample = copied_name
            except pyperclip.PyperclipException:
                pass
            try:
                self.monitors = enumerate_monitors()
                self.record_monitors = list(self.monitors)
                self.record_monitor = monitor_at(self.monitors, *self.io.mouse.position)["device"]
                self.monitor_details.set(f"Recording starts on {self.record_monitor} • {len(self.monitors)} connected monitors")
                self.recording.muted = False
                self.stop.clear()
                self.recording.start()
                self.state = "recording"
                self.status.set("Recording NOW — taskbar clicks and mouse movement are included. F6 finishes.")
                self.buttons()
                self.root.iconify()
                self.root.after(80, lambda: self.show_hud("RECORDING NOW — switch to your app and continue.\nF6 finishes • F9 stops", self.record_monitor))
            except (OSError, ValueError) as exc:
                self.state = "idle"
                self.buttons()
                messagebox.showerror("Cannot start recording", str(exc), parent=self.root)

    def finish_record(self):
        self.events = self.recording.stop()
        self.state = "idle"
        self.hide_hud()
        self.root.deiconify()
        self.describe()
        self.status.set("Recording saved. Choose a mode, then press Run (F7).")
        try:
            if any(e["kind"] != "wait" for e in self.events):
                entry = self.library.add(self.current_recording_data())
                self.active_recording_id = entry["id"]
                self.active_name.set(entry["name"])
                self.persist_current()
                self.status.set("Saved in History / Manage. Use Save as… to give it a name.")
            else:
                self.active_recording_id = None
                self.active_name.set("Empty recording")
                self.status.set("No actions captured. Your previous saved recordings are still in History / Manage.")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not save recording", str(exc), parent=self.root)
        self.buttons()

    def insert_account(self):
        if self.state != "recording" or self.recording.muted: return
        if self.sample is None:
            self.status.set("Load an account list before recording an F8 account step.")
            return
        self.recording.add("account")
        self.recording.muted = True
        def insert():
            try:
                self.io.paste_account(self.sample, self.stop)
                self.queue.put(("inserted",))
            except Exception as exc:
                self.queue.put(("error", str(exc)))
        self.worker = threading.Thread(target=insert, daemon=True)
        self.worker.start()

    def fingerprint(self):
        return hashlib.sha256("\n".join(self.accounts).encode("utf-8")).hexdigest()

    def start_play(self):
        if self.state != "idle" or self.start_dialog: return
        if self.worker and self.worker.is_alive(): return
        if not any(e["kind"] != "wait" for e in self.events):
            messagebox.showinfo("Record first", "Press F6 to record your clicks and typing first.", parent=self.root)
            return
        self.reload_accounts()
        try:
            self.monitors = enumerate_monitors()
            playback_events = remap_events(self.events, self.record_monitors, self.monitors)
            for event in playback_events:
                if event["kind"] == "launch" and not Path(event["path"]).is_file():
                    raise ValueError(f"Application not found: {event['path']}")
            notify_telegram = self.io.telegram.credentials_path.exists()
            playback_monitor = self.record_monitor or next((e["monitor"] for e in playback_events if e.get("monitor")), None)
            if playback_monitor is None:
                point = next(((e["x"], e["y"]) for e in playback_events if "x" in e), self.io.mouse.position)
                playback_monitor = monitor_at(self.monitors, *point)["device"]
            if any(e["kind"] == "screenshot" for e in playback_events):
                self.io.telegram.credentials()
            gap = float(self.gap.get())
            if not 0 <= gap <= 3600: raise ValueError("Loop delay must be between 0 and 3600 seconds.")
            mode = self.mode.get()
            start = 1
            fingerprint = self.fingerprint()
            if mode == "accounts":
                if not self.accounts: raise ValueError("Load a text file with one account per line.")
                suggested = self.resume.get("next_account", 1) if self.resume.get("fingerprint") == fingerprint else 1
                if not isinstance(suggested, int) or not 1 <= suggested <= len(self.accounts): suggested = 1
                self.start_dialog = True
                try:
                    start = simpledialog.askinteger("Start from which account?",
                        f"Enter account number (1–{len(self.accounts)}).\n\n5 starts WITH account #5.\nIf you finished the first 5, enter 6.\nRuns from this account through the end.",
                        initialvalue=suggested, minvalue=1, maxvalue=len(self.accounts), parent=self.root)
                finally:
                    self.start_dialog = False
                if start is None: return
            elif any(e["kind"] == "account" for e in self.events):
                raise ValueError("This recording includes F8 account steps. Select Account list mode, or record a sequence without F8 for fixed repeats.")
            plan = make_plan(mode, int(self.count.get()) if mode == "repeat" else 1, self.accounts, start)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Playback settings", str(exc), parent=self.root)
            return
        def begin():
            if mode == "accounts":
                self.resume = {"fingerprint": fingerprint, "next_account": start}
                try:
                    (BASE / "progress.json").write_text(json.dumps(self.resume), encoding="utf-8")
                except OSError as exc:
                    self.state = "idle"
                    self.root.deiconify()
                    self.buttons()
                    messagebox.showerror("Cannot save progress", str(exc), parent=self.root)
                    return
            self.state = "playing"
            self.buttons()
            self.show_hud("PLAYBACK STARTED\nF9 stops immediately.", playback_monitor)
            def run():
                reporter = None
                if notify_telegram:
                    reporter = TelegramRunStatus(self.io.telegram, plan,
                        report=lambda text: self.queue.put(("telegram_status", text)))
                    self.telegram_runs.append(reporter)
                    reporter.start()
                def progress(*args):
                    self.queue.put(("progress", *args))
                    if reporter: reporter.begin_account(*args)
                def completed(number, account):
                    self.queue.put(("completed", number, account, fingerprint))
                    if reporter:
                        reporter.account_done(number, account)
                def color_checked(*args):
                    self.queue.put(("color_checked", *args))
                    if reporter: reporter.color(*args)
                try:
                    done = play(playback_events, plan, self.io, self.stop, gap,
                                progress=progress, completed=completed, color_checked=color_checked,
                                step_progress=reporter.step if reporter else lambda *args: None)
                    success = done == len(plan)
                    if reporter:
                        reporter.finish(success)
                    message = f"Done — all {done} loops finished." if success else f"Stopped — {done}/{len(plan)} loops finished. An interrupted account will be offered again."
                    self.queue.put(("finished", message, success))
                except Exception as exc:
                    if reporter: reporter.finish(False, error=exc)
                    self.queue.put(("finished", f"Playback stopped: {exc}", False))
            self.worker = threading.Thread(target=run, daemon=True)
            self.worker.start()
        self.countdown_start("Playback", begin)

    def stop_now(self):
        self.stop.set()
        self.screenshot_stop.set()
        if self.state == "recording":
            self.finish_record()
        elif self.state == "countdown":
            self.state = "idle"
            self.root.deiconify()
            self.status.set("Cancelled.")
            self.buttons()
        elif self.state == "playing":
            self.status.set("Stopping…")

    def describe(self):
        steps = sum(e["kind"] == "account" for e in self.events)
        colors = sum(e["kind"] == "color" for e in self.events)
        screenshots = sum(e["kind"] == "screenshot" for e in self.events)
        self.details.set(f"{len(self.events)} events • {sum(e['delay'] for e in self.events):.1f}s + waits • {steps} account steps • {colors} color waits • {screenshots} screenshots")
        self.monitor_details.set(f"Saved recording monitor: {self.record_monitor} • {len(self.monitors)} monitors connected" if self.record_monitor else
                                 f"{len(self.monitors)} monitors connected • older recording uses its original absolute screen positions")

    def current_recording_data(self):
        return {"version": 2, "events": self.events, "monitors": self.record_monitors,
                "recording_monitor": self.record_monitor}

    def persist_current(self):
        data = self.current_recording_data()
        if self.active_recording_id:
            data["library"] = {"id": self.active_recording_id, "name": self.active_name.get()}
        self.library._write(BASE / "last_recording.json", data)
        self.library.remember_source(BASE / "last_recording.json")

    def activate_library_recording(self, identity):
        data = self.library.get(identity)
        self.events = data["events"]
        self.record_monitors = data["monitors"]
        self.record_monitor = data["recording_monitor"]
        self.active_recording_id = identity
        self.active_name.set(data["library"]["name"])
        self.persist_current()
        self.describe()
        self.status.set(f"Loaded {self.active_name.get()}. Press F7 when ready to run.")

    def open_history(self):
        if self.state != "idle": return
        if self.history_window is not None:
            self.history_window.window.lift()
            return
        self.history_window = RecordingHistoryWindow(self)

    def save(self):
        self.save_to_library(parent=self.root)

    def save_to_library(self, parent=None):
        if self.state != "idle" or not any(e["kind"] != "wait" for e in self.events): return
        old_dialog = self.start_dialog
        self.start_dialog = True
        try:
            name = simpledialog.askstring("Save recording as new", "Name for this recording:",
                                          initialvalue=self.active_name.get(), parent=parent or self.root)
            if name is None: return
            entry = self.library.add(self.current_recording_data(), name)
            self.active_recording_id = entry["id"]
            self.active_name.set(entry["name"])
            self.persist_current()
            self.status.set(f"Saved {entry['name']} in History / Manage.")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Save recording", str(exc), parent=parent or self.root)
        finally:
            self.start_dialog = old_dialog

    def export_recording(self, path, data):
        save_macro(path, data["events"], data["monitors"], data["recording_monitor"])
        self.library.remember_source(path)

    def load(self):
        if self.state != "idle": return
        self.start_dialog = True
        try:
            path = filedialog.askopenfilename(parent=self.root, initialdir=BASE, filetypes=[("Recording", "*.json")])
            if path:
                entry = self.library.import_file(path)
                self.activate_library_recording(entry["id"])
        except (OSError, ValueError) as exc:
            messagebox.showerror("Load", str(exc), parent=self.root)
        finally:
            self.start_dialog = False

    def close(self):
        if self.state == "recording": self.finish_record()
        self.stop.set()
        self.screenshot_stop.set()
        # Allow the playback worker to release held keys before exiting.
        if self.worker and self.worker.is_alive():
            self.root.after(50, self.close)
            return
        if any(run.thread.is_alive() for run in self.telegram_runs):
            self.status.set("Finishing the last Telegram update before closing…")
            self.root.after(100, self.close)
            return
        self.closed = True
        self.key_listener.stop()
        self.mouse_listener.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    instance = None
    try:
        instance = RecorderInstance(BASE)
        app = RecorderApp(root)
    except Exception as exc:
        messagebox.showerror("Recorder could not start", str(exc), parent=root)
        root.destroy()
        if instance is not None:
            instance.close()
        raise
    try:
        root.mainloop()
    finally:
        instance.close()
