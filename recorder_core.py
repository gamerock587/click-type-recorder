"""Recording and playback logic, independent of the desktop UI."""
import json
import math
import threading
import time
from pathlib import Path

COLOR_CHECK_INTERVAL = 2.0


def read_accounts(path):
    return [line.strip() for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def make_plan(mode, count, accounts, start):
    if mode == "accounts":
        if not accounts:
            raise ValueError("The account file is empty.")
        if not 1 <= start <= len(accounts):
            raise ValueError(f"Choose an account from 1 to {len(accounts)}.")
        return [(i + 1, accounts[i]) for i in range(start - 1, len(accounts))]
    if count < 1:
        raise ValueError("Repeat count must be at least 1.")
    return [(i + 1, None) for i in range(count)]


def save_macro(path, events, monitors=None, recording_monitor=None):
    destination = Path(path)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps({"version": 2, "events": events, "monitors": monitors or [],
                                    "recording_monitor": recording_monitor}, indent=2), encoding="utf-8")
    temporary.replace(destination)


def load_recording(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") not in (1, 2) or not isinstance(data.get("events"), list):
        raise ValueError("Not a supported recorder file.")
    monitors = data.setdefault("monitors", [])
    data.setdefault("recording_monitor", None)
    if not isinstance(monitors, list):
        raise ValueError("Invalid monitor information.")
    devices = set()
    for monitor in monitors:
        if not isinstance(monitor, dict) or not isinstance(monitor.get("device"), str):
            raise ValueError("Invalid monitor information.")
        bounds = monitor.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4 or any(type(n) is not int for n in bounds) or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            raise ValueError("Invalid monitor bounds.")
        if monitor["device"] in devices:
            raise ValueError("Duplicate monitor identifier.")
        devices.add(monitor["device"])
    if data["recording_monitor"] is not None and (not isinstance(data["recording_monitor"], str) or data["recording_monitor"] not in devices):
        raise ValueError("Invalid recording monitor.")
    events = data["events"]
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Invalid recorded action.")
        delay = event.get("delay")
        if not isinstance(delay, (int, float)) or not math.isfinite(delay) or delay < 0:
            raise ValueError("Invalid action timing.")
        kind = event.get("kind")
        if "monitor" in event and (not isinstance(event["monitor"], str) or event["monitor"] not in devices):
            raise ValueError("A recorded action refers to an unknown monitor.")
        if kind in ("move", "click", "scroll", "color"):
            for name in ("x", "y"):
                if not isinstance(event.get(name), int):
                    raise ValueError("Invalid mouse position.")
        if kind == "click":
            if event.get("button") not in ("left", "right", "middle", "x1", "x2") or type(event.get("pressed")) is not bool:
                raise ValueError("Invalid mouse button.")
        elif kind == "scroll":
            if any(not isinstance(event.get(n), int) for n in ("dx", "dy")):
                raise ValueError("Invalid scroll action.")
        elif kind == "key":
            if not isinstance(event.get("vk"), int) or not 0 <= event["vk"] <= 255 or type(event.get("pressed")) is not bool:
                raise ValueError("Invalid keyboard action.")
            if event["vk"] in (113, 114, 117, 118, 119, 120):
                raise ValueError("Recorder hotkeys cannot be replayed.")
        elif kind == "color":
            rgb = event.get("rgb")
            if not isinstance(rgb, list) or len(rgb) != 3 or any(type(c) is not int or not 0 <= c <= 255 for c in rgb):
                raise ValueError("Invalid target color.")
        elif kind == "screenshot":
            if not isinstance(event.get("monitor"), str) or event["monitor"] not in devices:
                raise ValueError("Screenshot step is missing its monitor.")
        elif kind == "launch":
            path = event.get("path")
            if not isinstance(path, str) or not Path(path).is_absolute() or Path(path).suffix.lower() != ".exe":
                raise ValueError("An app launch step requires an absolute .exe path.")
        elif kind not in ("move", "account", "wait"):
            raise ValueError("Unknown recorded action.")
    return data


def load_macro(path):
    return load_recording(path)["events"]


class Recording:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.events = []
        self.active = False
        self.last_move = 0
        self.muted = False

    def start(self):
        with self.lock:
            self.events = []
            self.last = self.clock()
            self.last_move = 0
            self.active = True

    def add(self, kind, **values):
        with self.lock:
            if not self.active or self.muted:
                return
            now = self.clock()
            if kind == "move" and now - self.last_move < 0.04:
                return
            if kind == "move":
                self.last_move = now
            self.events.append({"kind": kind, "delay": max(0, now - self.last), **values})
            self.last = now

    def stop(self):
        with self.lock:
            if self.active:
                self.events.append({"kind": "wait", "delay": max(0, self.clock() - self.last)})
            self.active = False
            return list(self.events)


def wait_for_color(event, io, stop, checked=lambda *args: None):
    """Check immediately, then every two seconds; never skip an unmatched color."""
    while not stop.is_set():
        io.move_to(event["x"], event["y"])
        if stop.is_set():
            return False
        current = io.pixel(event["x"], event["y"])
        matched = tuple(current) == tuple(event["rgb"])
        checked(event, current, matched)
        if matched:
            return not stop.is_set()
        if stop.wait(COLOR_CHECK_INTERVAL):
            break
    return False


def play(events, plan, io, stop, gap=1.0, progress=lambda *args: None, completed=lambda *args: None,
         color_checked=lambda *args: None, step_progress=lambda *args: None):
    """Only mark a loop completed after every event and its trailing wait."""
    done = 0
    try:
        for position, (number, account) in enumerate(plan, 1):
            if stop.is_set():
                break
            progress(position, len(plan), number, account)
            if account is not None:
                io.copy(account)
            for event_index, event in enumerate(events, 1):
                if stop.wait(event["delay"]):
                    return done
                if event["kind"] == "account":
                    if account is None:
                        raise ValueError("This recording uses an account placeholder. Choose account-list mode.")
                    io.paste_account(account, stop)
                elif event["kind"] == "color":
                    if not wait_for_color(event, io, stop, color_checked):
                        return done
                elif event["kind"] == "screenshot":
                    if not io.screenshot(event, stop, account):
                        return done
                elif event["kind"] != "wait":
                    io.perform(event)
                if stop.is_set():
                    return done
                step_progress(event_index, len(events))
            io.release_all()
            done += 1
            completed(number, account)
            if position < len(plan) and stop.wait(gap):
                break
        return done
    finally:
        io.release_all()
