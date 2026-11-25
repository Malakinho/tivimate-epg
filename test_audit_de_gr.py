import gzip
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from audit_de_gr import compare, inspect

class CandidateAuditTests(unittest.TestCase):
    def test_stale_and_name_only_events_do_not_count(self):
        now = datetime(2026, 11, 26, 0, tzinfo=timezone.utc)
        xml = """<tv>
          <channel id="good.de"><display-name>ZDF</display-name></channel>
          <channel id="stale.de"><display-name>RTL</display-name></channel>
          <channel id="dummy.de"><display-name>Arte</display-name></channel>
          <programme channel="good.de" start="20261126010000 +0000" stop="20261126020000 +0000"><title>Nachrichten</title></programme>
          <programme channel="stale.de" start="20261125010000 +0000" stop="20261125020000 +0000"><title>Nachrichten</title></programme>
          <programme channel="dummy.de" start="20261126010000 +0000" stop="20261126020000 +0000"><title>Arte</title></programme>
        </tv>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.gz"
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write(xml)
            self.assertEqual(set(inspect(path, "DE", now)), {"good.de"})

    def test_unique_name_and_matching_id_only(self):
        rows = [
            ["DE: ZDF HD", "General", "186", "ZDF.de", "", "ohne", "DE", "Sender", False],
            ["DE: RTL HD", "General", "186", "", "", "ohne", "DE", "Sender", False],
            ["DE: ARTE HD", "General", "186", "ZDF.de", "", "ohne", "DE", "Sender", False],
            ["US: ZDF", "USA", "1689", "", "", "ohne", "DE", "Sender", False],
            ["GR: ZDF", "Greece", "4", "", "", "ohne", "GR", "Sender", False],
        ]
        channels = {"ZDF.de": {"ZDF"}, "RTL1.de": {"RTL"}, "RTL2.de": {"RTL"}, "Arte.de": {"ARTE"}}
        stats, results = compare(rows, "DE", channels)
        self.assertEqual(stats["new_programme_candidates"], 1)
        self.assertEqual(stats["ambiguous_name"], 1)
        self.assertEqual(stats["conflicting_id"], 1)
        self.assertEqual(results[0]["method"], "exact_id_and_name")

if __name__ == "__main__":
    unittest.main()
