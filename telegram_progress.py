"""One Telegram message per run, edited in place without delaying playback."""
import threading
import time


class TelegramRunStatus:
    def __init__(self, client, plan, report=lambda text: None, interval=2.0):
        self.client = client
        self.total = len(plan)
        self.current = plan[0][1] or f"loop {plan[0][0]}"
        self.completed = 0
        self.fraction = 0.0
        self.phase = "Starting in account"
        self.detail = ""
        self.condition = threading.Condition()
        self.closed = False
        self.message_id = None
        self.report = report
        self.interval = interval
        self.error = None
        self.initial_text = self._text()
        self.thread = threading.Thread(target=self._work, daemon=True)

    def _text(self):
        percentage = min(99, int(100 * (self.completed + self.fraction) / self.total))
        if self.completed == self.total or self.phase == "Done": percentage = 100
        lines = [f"{self.phase} :: {self.current}",
                 f"Progress: {percentage}%", f"Completed: {self.completed}/{self.total}"]
        if self.phase not in ("Done", "Stopped", "Failed"):
            account_percent = 100 if self.phase == "Account complete" else int(self.fraction * 100)
            lines.append(f"Current account: {account_percent}%")
        if self.detail: lines.append(self.detail)
        return "\n".join(lines)

    def start(self):
        self.thread.start()

    def begin_account(self, position, total, number, account):
        with self.condition:
            self.current = account or f"loop {number}"
            self.fraction = 0
            self.phase = "Starting in account"
            self.detail = ""
            self.condition.notify()

    def step(self, position, total):
        with self.condition:
            self.fraction = position / max(1, total)
            self.phase = "Running account"
            self.detail = ""
            self.condition.notify()

    def color(self, event, current, matched):
        with self.condition:
            self.detail = "" if matched else "Waiting for the selected color (checks every 2 seconds)."
            self.condition.notify()

    def account_done(self, number, account):
        with self.condition:
            self.completed += 1
            self.fraction = 0
            self.phase = "Account complete"
            self.detail = ""
            self.condition.notify()

    def finish(self, success, error=None):
        with self.condition:
            self.phase = "Done" if success else "Failed" if error else "Stopped"
            self.detail = str(error) if error else ""
            self.closed = True
            self.condition.notify()

    def _work(self):
        try:
            self.message_id = self.client.send_status(self.initial_text)
            last_text = self.initial_text
            last_sent = time.monotonic()
            while True:
                with self.condition:
                    while True:
                        text = self._text()
                        if self.closed and text == last_text:
                            self.report("Telegram status updated.")
                            return
                        if text != last_text:
                            remaining = self.interval - (time.monotonic() - last_sent)
                            if remaining > 0:
                                self.condition.wait(remaining)
                                continue
                            break
                        self.condition.wait()
                if text != last_text:
                    self.client.edit_status(self.message_id, text)
                    last_text = text
                    last_sent = time.monotonic()
        except Exception as exc:
            with self.condition:
                self.error = str(exc)
            self.report(f"Telegram status update failed: {exc}")
