"""Ensure 24/7 category streams without provider EPG IDs get placeholders."""
import csv
import hashlib
import unittest
from pathlib import Path

from build import extend_always_on_categories


class CategoryPlaceholdersTest(unittest.TestCase):
    def test_category_only_24_7_and_no_real_id(self):
        records = [
            {"name": "#### 24/7 PRIME VIDEO ####", "category_id": "2213", "epg_channel_id": ""},
            {"name": "#### COSMOTE FILM 24/7 ####", "category_id": "1470", "epg_channel_id": ""},
            {"name": "#### 24/7 DISNEY+ ####", "category_id": "2133", "epg_channel_id": ""},
            {"name": "#### REGULAR PROGRAMMES ####", "category_id": "77", "epg_channel_id": ""},
            {"name": "US: FALLOUT RAW", "category_id": "2213", "epg_channel_id": ""},
            {"name": "GR: COSMOTE ACTION 1 4K", "category_id": "1470", "epg_channel_id": ""},
            {"name": "US: SHOW WITH REAL EPG", "category_id": "2133", "epg_channel_id": "Real.us"},
            {"name": "US: BET", "category_id": "77", "epg_channel_id": ""},
        ]
        result = extend_always_on_categories([], records)
        self.assertEqual(len(result), 2)
        self.assertEqual({r["name"] for r in result},
                         {"US: FALLOUT RAW", "GR: COSMOTE ACTION 1 4K"})
        self.assertEqual(result[0]["id"], "always." +
                         hashlib.sha256(b"US: FALLOUT RAW").hexdigest()[:20])
        self.assertEqual(result, extend_always_on_categories(result, records))

    def test_actual_full_inventory_adds_missing_category_placeholders(self):
        root = Path(__file__).resolve().parent
        with (root / "data" / "inventory.csv").open(encoding="utf-8-sig", newline="") as source:
            inventory = list(csv.DictReader(source))
        with (root / "data" / "always_on.csv").open(encoding="utf-8-sig", newline="") as source:
            existing = list(csv.DictReader(source))
        extended = extend_always_on_categories(existing, inventory)
        new = extended[len(existing):]
        self.assertGreaterEqual(len(new), 160)
        targets = {x["name"] for x in new}
        self.assertIn("GR: COSMOTE FILM 1 4K", targets)
        self.assertIn("US: FALLOUT ᴿᴬᵂ", targets)

    def test_preserve_already_known_placeholders(self):
        original = {"id": "always.existing", "name": "US: FALLOUT", "category_id": "2213"}
        rows = [
            {"name": "## 24/7 PRIME VIDEO", "category_id": "2213", "epg_channel_id": ""},
            {"name": "US: FALLOUT", "category_id": "2213", "epg_channel_id": ""},
        ]
        self.assertEqual(extend_always_on_categories([original], rows), [original])


if __name__ == "__main__":
    unittest.main()
