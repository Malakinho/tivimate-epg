"""Diagnostic feed health must be useful but must never expose secrets."""
import json
import unittest

from diagnostics import begin_source_report, mark_source, public_source_report


class SourceDiagnosticsTests(unittest.TestCase):
    def test_success_failed_empty_and_no_secrets(self):
        sources = [
            ("DE-SOURCE", "DE", "https://example.com/?token=secret-one"),
            ("GR-SOURCE", "GR", "https://example.com/?password=secret-two"),
            ("US-SOURCE", "US", "https://example.com/?username=secret-three"),
        ]
        report = begin_source_report(sources)
        mark_source(report, "DE-SOURCE", "ok", channels=12, programmes=450)
        mark_source(report, "GR-SOURCE", "failed", "download")
        mark_source(report, "US-SOURCE", "empty", "no_programmes", channels=7)
        exported = public_source_report(report)
        self.assertEqual([x["status"] for x in exported],
                         ["ok", "failed", "empty"])
        self.assertEqual(exported[0]["programmes"], 450)
        self.assertEqual(exported[1]["reason"], "download")
        self.assertEqual(exported[2]["channels"], 7)
        serialized = json.dumps(exported)
        for secret in ("secret-one", "secret-two", "secret-three",
                       "token", "password", "username", "https://"):
            self.assertNotIn(secret, serialized)

    def test_reject_unrecognized_source_or_untrusted_error(self):
        report = begin_source_report([("DE", "DE", "https://example.test")])
        with self.assertRaises(KeyError):
            mark_source(report, "MISSING", "failed", "download")
        with self.assertRaises(ValueError):
            mark_source(report, "DE", "failed", "https://private/user/password")
        with self.assertRaises(ValueError):
            mark_source(report, "DE", "unknown")
        self.assertEqual(report["DE"]["reason"], "not_checked")


if __name__ == "__main__":
    unittest.main()
