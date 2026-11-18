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
    def test_all_22_categories_in_full_inventory(self):
        root = Path(__file__).resolve().parent
        with (root / "data" / "inventory.csv").open(encoding="utf-8-sig", newline="") as handle:
            inventory = list(csv.DictReader(handle))
        ids = named_category_ids(inventory)
        rows = static_named_rows(inventory, ids)
        self.assertEqual(len(CATEGORY_NAMES), 22)
        self.assertEqual(len(ids), 22)
        self.assertTrue({"1115", "334", "2211", "2210", "2209",
                         "1241", "1255", "545", "332"}.issubset(ids))
        self.assertGreaterEqual(len(rows), 1100)  # Duplicated names within one category share one XMLTV ID.
        self.assertEqual(len({r["id"] for r in rows}), len(rows))
        self.assertTrue(all(not r["name"].startswith("##") for r in rows))
        self.assertEqual(rows, static_named_rows(inventory, ids))

    def test_unicode_headings_and_no_false_matches(self):
        self.assertTrue(category_is_named("### ️HULU ORIGINALS ᴿᴬᵂ ⁶⁰ᶠᵖˢ ###"))
        self.assertTrue(category_is_named("###  NHL LEAGUE REPLAY ###"))
        for heading in (
            "##### BUNDESLIGA REPLAY #####",
            "##### KIDS HD/4K #####",
            "##### PRIME VIDEO ᴿᴬᵂ #####",
            "####### DISNEY+ ᴿᴬᵂ #######",
            "####### HULU ᴿᴬᵂ #######",
            "####### MAGENTA FILME 4K #######",
            "####### MAGENTA SERIE 4K #######",
            "##### Vodafone GO FILME 4K #####",
            "###### VIP BOX FILME ######",
        ):
            with self.subTest(heading=heading):
                self.assertTrue(category_is_named(heading))
        self.assertFalse(category_is_named("ESPN+ PPV"))
        self.assertFalse(category_is_named("### 24/7 PRIME VIDEO ᴿᴬᵂ ⁶⁰ᶠᵖˢ ###"))
        self.assertFalse(category_is_named("### 24/7 DISNEY+ ᴿᴬᵂ ⁶⁰ᶠᵖˢ ###"))
        self.assertFalse(category_is_named("##### KIDS ᴴᴰ/ᴿᴬᵂ ⁶⁰ᶠᵖˢ #####"))
        self.assertFalse(category_is_named("##### KIDS ONEPLAY #####"))
        rows = [
            {"name": "## NFL LIVE EVENTS ##", "category_id": "10"},
            {"name": "NFL | Week 10", "category_id": "10"},
            {"name": "## US SPORTS ##", "category_id": "20"},
            {"name": "US: NFL REPLAY HD", "category_id": "20"},
        ]
        self.assertEqual(named_category_ids(rows), {"10"})
        self.assertEqual(len(static_named_rows(rows, {"10"})), 1)

    def test_new_categories_work_with_live_xtream_renames(self):
        streams = [
            {"stream_id": 200, "category_id": "2211", "name": "DE: SHOW A RAW"},
            {"stream_id": 201, "category_id": "334", "name": "DE: KIKA HD"},
            {"stream_id": 202, "category_id": "332", "name": "DE: BOX ACTION 4K"},
            {"stream_id": 203, "category_id": "2213", "name": "US: OLD 24/7 STREAM"},
            {"stream_id": 204, "category_id": "33", "name": "US: PPV EVENT"},
        ]
        categories = [
            {"category_id": "2211", "category_name": "PRIME VIDEO ᴿᴬᵂ"},
            {"category_id": "334", "category_name": "KIDS HD/4K"},
            {"category_id": "332", "category_name": "VIP BOX FILME"},
            {"category_id": "2213", "category_name": "24/7 PRIME VIDEO ᴿᴬᵂ"},
            {"category_id": "33", "category_name": "PPV EVENTS"},
        ]
        def fake_open(req, timeout=0):
            class Response:
                def __init__(self, data): self.data = data
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self, n=-1): return self.data
            payload = categories if "get_live_categories" in req.full_url else streams
            return Response(json.dumps(payload).encode())
        with patch("xtream_ppv.urlopen", side_effect=fake_open):
            ppv, named = current_ppv_rows(ACCESS, {"33"}, set())
            self.assertEqual(len(ppv), 1)
            self.assertEqual(len(named), 3)
            self.assertEqual({r["category_id"] for r in named},
                             {"2211", "334", "332"})
            original = {r["name"]: r["id"] for r in named}
            streams[0]["name"] = "DE: SHOW B RAW"
            _, renamed = current_ppv_rows(ACCESS, {"33"}, set())
            self.assertEqual(renamed[0]["name"], "DE: SHOW B RAW")
            self.assertEqual(renamed[0]["id"], original["DE: SHOW A RAW"])

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
