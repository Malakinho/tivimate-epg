"""Regression tests for safe matching across PRIME/JOYN/DE and spacing variants."""
import csv
import unittest
from pathlib import Path

from build import clean_name


class EpgNameNormalizationTests(unittest.TestCase):
    def test_zdfinfo_matches_with_or_without_space_and_platform_prefix(self):
        expected = "ZDFINFO"
        for name in (
            "JOYN: ZDF INFO ᴿᴬᵂ",
            "PRIME: ZDFINFO ᴿᴬᵂ",
            "DE: ZDFINFO HD",
            "ZDF INFO",
            "ZDFinfo",
        ):
            with self.subTest(name=name):
                self.assertEqual(clean_name(name), expected)

    def test_zdfneo_matches_spaced_and_unspaced(self):
        for name in ("JOYN: ZDF NEO ᴿᴬᵂ", "PRIME: ZDFNEO ᴿᴬᵂ",
                     "DE: ZDF NEO HD", "ZDFneo"):
            with self.subTest(name=name):
                self.assertEqual(clean_name(name), "ZDFNEO")

    def test_distinct_channels_are_not_merged(self):
        self.assertEqual(clean_name("JOYN: ZDF RAW"), "ZDF")
        self.assertEqual(clean_name("JOYN: ZDF INFO EXTRA RAW"), "ZDFINFO EXTRA")
        self.assertEqual(clean_name("JOYN: ZDF NEO 2 RAW"), "ZDFNEO 2")
        self.assertEqual(len({
            clean_name("PRIME: ZDF RAW"),
            clean_name("JOYN: ZDF INFO RAW"),
            clean_name("PRIME: ZDFNEO RAW"),
        }), 3)

    def test_real_inventory_contains_the_identical_programme_channels(self):
        root = Path(__file__).resolve().parent
        with (root / "data" / "channels.csv").open(encoding="utf-8-sig", newline="") as file:
            rows = {row["name"]: row["epg_channel_id"]
                    for row in csv.DictReader(file)}
        self.assertIn("JOYN: ZDF INFO ᴿᴬᵂ", rows)
        self.assertEqual(clean_name("JOYN: ZDF INFO ᴿᴬᵂ"),
                         clean_name("DE: ZDFINFO HD"))
        self.assertEqual(rows["DE: ZDFINFO HD"].casefold(), "zdfinfo.de")
        self.assertEqual(clean_name("PRIME: ZDFINFO ᴿᴬᵂ"),
                         clean_name("JOYN: ZDF INFO ᴿᴬᵂ"))


if __name__ == "__main__":
    unittest.main()
