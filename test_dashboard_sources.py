"""Check the public dashboard shows the source behind stable XMLTV aliases."""
import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from build import clean_name, market_of, id_valid
from dashboard_data import make_dashboard


class DashboardSourceTests(unittest.TestCase):
    def test_opaque_alias_shows_actual_source_channel(self):
        name = "PRIME: ZDF RAW"
        alias = "personal." + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
        source = SimpleNamespace(id="zdf.de", names=("ZDF HD",),
                                 feed=0, norm_names=frozenset({"ZDF"}))
        feeds = [SimpleNamespace(label="DE-MASTER", market="DE")]
        with tempfile.TemporaryDirectory() as tmp:
            inventory, output = Path(tmp) / "inventory.csv", Path(tmp) / "dashboard.json"
            with inventory.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["name", "epg_channel_id", "category_id"])
                writer.writeheader()
                writer.writerow({"name": "## PRIME RAW", "epg_channel_id": "", "category_id": "1339"})
                writer.writerow({"name": name, "epg_channel_id": "", "category_id": "1339"})
            report = make_dashboard(
                inventory_path=inventory, output_path=output,
                ppv_rows=[], always_on_rows=[], named_rows=[],
                by_id={"zdf.de": source}, by_name={"ZDF": [source]},
                aliases={alias: source}, feeds=feeds,
                clean_name=clean_name, market_of=market_of,
                best=lambda candidates, market, feeds, provider_id: candidates[0] if candidates else None,
                id_valid=id_valid, live_ppv=False
            )
            self.assertEqual(report["total"], 1)
            row = report["rows"][0]
            self.assertEqual(row[4], alias)
            self.assertEqual(row[5], "programm")
            self.assertEqual(row[9], "zdf.de")
            self.assertEqual(row[10], "ZDF HD")
            self.assertEqual(row[11], "DE-MASTER")
            self.assertEqual(json.loads(output.read_text())["rows"][0], row)

    def test_missing_and_placeholders_do_not_claim_a_real_epg_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            inventory, output = Path(tmp) / "inventory.csv", Path(tmp) / "dashboard.json"
            with inventory.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["name", "epg_channel_id", "category_id"])
                writer.writeheader()
                writer.writerow({"name": "## SPECIAL SHOWS", "epg_channel_id": "", "category_id": "20"})
                writer.writerow({"name": "US: SHOW A", "epg_channel_id": "", "category_id": "20"})
                writer.writerow({"name": "US: SHOW B", "epg_channel_id": "", "category_id": "20"})
            report = make_dashboard(
                inventory_path=inventory, output_path=output,
                ppv_rows=[], always_on_rows=[],
                named_rows=[{"name": "US: SHOW A", "category_id": "20", "id": "named.static.id", "source": "static"}],
                by_id={}, by_name={}, aliases={}, feeds=[],
                clean_name=clean_name, market_of=market_of,
                best=lambda candidates, market, feeds, provider_id: None,
                id_valid=id_valid, live_ppv=False
            )
            rows = {row[0]: row for row in report["rows"]}
            self.assertEqual(rows["US: SHOW A"][5], "platzhalter")
            self.assertEqual(rows["US: SHOW B"][5], "ohne")
            self.assertEqual(rows["US: SHOW A"][9:], ["", "", ""])
            self.assertEqual(rows["US: SHOW B"][9:], ["", "", ""])


if __name__ == "__main__":
    unittest.main()
