"""Requested sender-name EPG category regression tests."""
import csv
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from named_categories import CATEGORY_NAMES, category_is_named, named_category_ids, static_named_rows
from xtream_ppv import current_ppv_rows

ACCESS = "https://iptv.example/get.php?username=test_user&password=test_secret&type=m3u_plus"

class NamedCategoryTests(unittest.TestCase):
    def test_all_13_categories_in_full_inventory(self):
        root = Path(__file__).resolve().parent
        with (root / "data" / "inventory.csv").open(encoding="utf-8-sig", newline="") as handle:
            inventory = list(csv.DictReader(handle))
        ids = named_category_ids(inventory)
        rows = static_named_rows(inventory, ids)
        self.assertEqual(len(CATEGORY_NAMES), 13)
        self.assertEqual(len(ids), 13)
        self.assertGreaterEqual(len(rows), 780)  # Duplicate names within a category share one XMLTV ID.
        self.assertEqual(len({r["id"] for r in rows}), len(rows))
        self.assertTrue(all(not r["name"].startswith("##") for r in rows))
        self.assertEqual(rows, static_named_rows(inventory, ids))

    def test_unicode_headings_and_no_false_matches(self):
        self.assertTrue(category_is_named("### ️HULU ORIGINALS ᴿᴬᵂ ⁶⁰ᶠᵖˢ ###"))
        self.assertTrue(category_is_named("###  NHL LEAGUE REPLAY ###"))
        self.assertFalse(category_is_named("ESPN+ PPV"))
        rows = [
            {"name": "## NFL LIVE EVENTS ##", "category_id": "10"},
            {"name": "NFL | Week 10", "category_id": "10"},
            {"name": "## US SPORTS ##", "category_id": "20"},
            {"name": "US: NFL REPLAY HD", "category_id": "20"},
        ]
        self.assertEqual(named_category_ids(rows), {"10"})
        self.assertEqual(len(static_named_rows(rows, {"10"})), 1)

    def test_live_renaming_keeps_epg_id(self):
        streams = [
            {"stream_id": 4, "category_id": "597", "name": "NFL | Week 10"},
            {"stream_id": 5, "category_id": "2056", "name": "US: 13 REASONS WHY RAW"},
            {"stream_id": 6, "category_id": "33", "name": "US: DAZN PPV 1"},
            {"stream_id": 7, "category_id": "99", "name": "US: NORMAL"},
        ]
        categories = [
            {"category_id": "597", "category_name": "NFL LIVE EVENTS"},
            {"category_id": "2056", "category_name": "NETFLIX ON AIR"},
            {"category_id": "33", "category_name": "DAZN PPV"},
            {"category_id": "99", "category_name": "US SPORTS"},
        ]
        def fake_open(req, timeout=0):
            class Response:
                def __init__(self, data): self.data = data
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self, n=-1): return self.data
            data = categories if "get_live_categories" in req.full_url else streams
            return Response(json.dumps(data).encode())
        with patch("xtream_ppv.urlopen", side_effect=fake_open):
            ppv, named = current_ppv_rows(ACCESS, {"33"}, {"597", "2056"})
            self.assertEqual(len(ppv), 1)
            self.assertEqual(len(named), 2)
            original_id = named[0]["id"]
            self.assertTrue(original_id.startswith("named.live."))
            streams[0]["name"] = "NFL | Week 11"
            _, updated = current_ppv_rows(ACCESS, {"33"}, {"597", "2056"})
            self.assertEqual(updated[0]["id"], original_id)
            self.assertEqual(updated[0]["name"], "NFL | Week 11")
            self.assertEqual(len(current_ppv_rows(ACCESS, {"33"})), 1)

if __name__ == "__main__":
    unittest.main()
