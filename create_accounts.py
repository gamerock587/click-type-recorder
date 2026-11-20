"""Register a username range; append only confirmed accounts to accounts.txt."""
import argparse
from datetime import datetime
import getpass
import http.cookiejar
import json
from pathlib import Path
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = Path(__file__).resolve().parent
ORIGIN = "https://7.wan.com"
REFERRER = ORIGIN + "/index/gameNoLogin?url=https%3A%2F%2F7.wan.com%2Fgame%2Fplay%3Fsid%3D758"


class RegistrationError(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def redact(message, password):
    text = str(message)
    for secret in (password, urllib.parse.quote(password, safe=""), urllib.parse.quote_plus(password)):
        if secret: text = text.replace(secret, "[password]")
    return text[:500]


def confirmed(payload):
    return isinstance(payload, dict) and str(payload.get("state")) == "1"


class RegistrationSession:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.headers = {"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
                        "Referer": REFERRER, "Origin": ORIGIN, "X-Requested-With": "XMLHttpRequest"}

    def post(self, route, fields):
        request = urllib.request.Request(ORIGIN + route, data=urllib.parse.urlencode(fields).encode("utf-8"),
                    headers={**self.headers, "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            with self.opener.open(request, timeout=25) as response:
                result = json.loads(response.read().decode("utf-8-sig"))
        except urllib.error.HTTPError as exc:
            raise RegistrationError(f"Server returned HTTP {exc.code}; verify the account before retrying.", uncertain=True) from None
        except (OSError, ValueError, urllib.error.URLError):
            raise RegistrationError("No usable server confirmation received. The account may have been created; verify before retrying.", uncertain=True) from None
        if not isinstance(result, dict):
            raise RegistrationError("Unexpected server response; creation is unconfirmed.", uncertain=True)
        return result

    def register(self, username, password):
        try:
            with self.opener.open(urllib.request.Request(REFERRER, headers=self.headers), timeout=25) as response:
                response.read()
        except (OSError, urllib.error.URLError):
            raise RegistrationError("Could not open the registration page; nothing was submitted.") from None
        result = self.post("/accounts/doRegisterFast", {"username": username, "password": password,
                           "password2": password, "from_uid": "0"})
        if confirmed(result):
            return "created", "Registration confirmed by the server."
        message = redact(result.get("msg", "Registration was not accepted."), password)
        if any(word in message.casefold() for word in ("already", "exist", "taken", "已存在", "已被注册", "已注册")):
            login = self.post("/accounts/checklogin.html", {"user_name": username, "user_pwd": password, "cookietime": "0"})
            if confirmed(login):
                return "verified_existing", "Already exists; login with the supplied password was confirmed."
            raise RegistrationError("Username already exists but the supplied password was not accepted. Not added.")
        raise RegistrationError(message)


def write_results(path, results):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def append_account(path, username):
    content = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    if username in {line.strip() for line in content.splitlines()}: return False
    prefix = "\n" if content and not content.endswith(("\n", "\r")) else ""
    with path.open("a", encoding="utf-8", newline="") as stream:
        stream.write(prefix + username + "\n")
    return True


def create_range(prefix, start, end, password, accounts_path, results_path, delay=2.0, session_factory=RegistrationSession):
    results = json.loads(results_path.read_text(encoding="utf-8"))["results"] if results_path.exists() else {}
    if not isinstance(results, dict): raise ValueError("Invalid registration results file.")
    requested = [f"{prefix}{number}" for number in range(start, end + 1)]
    for index, username in enumerate(requested, 1):
        previous = results.get(username, {})
        if previous.get("status") in ("created", "verified_existing"):
            append_account(accounts_path, username)
            print(f"[{index}/{len(requested)}] {username}: already confirmed; skipped", flush=True)
            continue
        if previous.get("status") == "uncertain":
            print(f"Stopped at {username}: previous result uncertain. Verify it before retrying.", flush=True)
            return False
        print(f"[{index}/{len(requested)}] Registering {username}...", flush=True)
        # Save the in-flight state before submitting: interruptions never cause blind retries.
        results[username] = {"status": "uncertain", "message": "Registration in progress; verify if interrupted.",
                             "time": datetime.now().astimezone().isoformat()}
        write_results(results_path, results)
        try:
            status, message = session_factory().register(username, password)
            if status not in ("created", "verified_existing"):
                raise RegistrationError("Unexpected result; not added.", uncertain=True)
        except RegistrationError as exc:
            status = "uncertain" if exc.uncertain else "failed"
            results[username] = {"status": status, "message": redact(exc, password), "time": datetime.now().astimezone().isoformat()}
            write_results(results_path, results)
            print(f"{username}: {status} — {redact(exc, password)}", flush=True)
            return False
        results[username] = {"status": status, "message": message, "time": datetime.now().astimezone().isoformat()}
        write_results(results_path, results)
        append_account(accounts_path, username)
        print(f"{username}: {status}; added to {accounts_path.name}", flush=True)
        if index < len(requested): time.sleep(delay)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="demo")
    parser.add_argument("--start", type=int, default=22)
    parser.add_argument("--end", type=int, default=100)
    parser.add_argument("--delay", type=float, default=2)
    parser.add_argument("--password-stdin", action="store_true")
    args = parser.parse_args()
    if args.start < 1 or args.end < args.start or args.delay < 0:
        parser.error("Choose a valid inclusive range and nonnegative delay.")
    password = sys.stdin.readline().rstrip("\r\n") if args.password_stdin else getpass.getpass("Password for these accounts: ")
    if not password: parser.error("A password is required.")
    accounts = BASE / "accounts.txt"
    if accounts.exists():
        backups = BASE / "account_backups"
        backups.mkdir(exist_ok=True)
        shutil.copy2(accounts, backups / datetime.now().strftime("accounts-before-registration-%Y%m%d-%H%M%S-%f.txt"))
    try:
        success = create_range(args.prefix, args.start, args.end, password, accounts, BASE / "registration_results.json", args.delay)
    except KeyboardInterrupt:
        print("\nStopped. Confirmed accounts are saved; verify any account marked uncertain before retrying.")
        return 1
    print("Done — all requested accounts are confirmed and in accounts.txt." if success else
          "Stopped. Confirmed accounts remain saved; see registration_results.json for the reason.")
    return 0 if success else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
