import json
from pathlib import Path
import tempfile
import unittest
from create_accounts import RegistrationError, confirmed, create_range, redact


class RegistrationTests(unittest.TestCase):
    def test_inclusive_success_and_resume_without_duplicate_submissions(self):
        calls = []
        class Session:
            def register(self, username, password):
                calls.append(username)
                return "created", "Confirmed"
        with tempfile.TemporaryDirectory() as folder:
            accounts, results = Path(folder)/"accounts.txt", Path(folder)/"results.json"
            accounts.write_text("demo1")
            self.assertTrue(create_range("demo", 22, 24, "test", accounts, results, 0, Session))
            self.assertEqual(accounts.read_text().splitlines(), ["demo1", "demo22", "demo23", "demo24"])
            self.assertTrue(create_range("demo", 22, 24, "test", accounts, results, 0, Session))
            self.assertEqual(calls, ["demo22", "demo23", "demo24"])

    def test_uncertain_result_stops_and_does_not_append_unconfirmed_names(self):
        class Session:
            def register(self, username, password):
                if username == "demo23": raise RegistrationError("Unconfirmed", uncertain=True)
                return "created", "Confirmed"
        with tempfile.TemporaryDirectory() as folder:
            accounts, results = Path(folder)/"accounts.txt", Path(folder)/"results.json"
            self.assertFalse(create_range("demo", 22, 24, "test", accounts, results, 0, Session))
            self.assertEqual(accounts.read_text().splitlines(), ["demo22"])
            self.assertEqual(json.loads(results.read_text())["results"]["demo23"]["status"], "uncertain")

    def test_explicit_server_success_and_secret_redaction(self):
        self.assertTrue(confirmed({"state": "1"}))
        self.assertFalse(confirmed({"state": 0, "msg": "success"}))
        self.assertFalse(confirmed({"ok": True}))
        self.assertEqual(redact("test@@ test%40%40", "test@@"), "[password] [password]")


if __name__ == "__main__": unittest.main()
