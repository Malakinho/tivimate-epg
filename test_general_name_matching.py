"""Generic cross-provider EPG matching without guessing unrelated channels."""
import csv
import hashlib
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

from build import clean_name, market_of, id_valid
from dashboard_data import make_dashboard
from name_matching import compact_index, matching_candidates


class GeneralNameMatchingTests(unittest.TestCase):
    def test_general_name_variants_across_multiple_broadcasters(self):
        pairs = (
            ("JOYN: TAGESSCHAU 24 ᴿᴬᵂ", "DE: TAGESSCHAU24 HD"),
            ("PRIME: TELE5 ᴿᴬᵂ", "DE: TELE 5 HEVC"),
            ("JOYN: NTV RAW", "DE: N-TV HD"),
            ("DE: REDBULLTV", "PRIME: RED BULL TV RAW"),
            ("GR: NOVACINEMA 1 UHD", "GR: NOVA CINEMA 1 HD"),
            ("JOYN: ZDF INFO RAW", "DE: ZDFINFO HD"),
        )
        for playlist, guide in pairs:
            with self.subTest(playlist=playlist):
                normalized_guide = clean_name(guide)
                index = compact_index({normalized_guide: [guide]})
                self.assertEqual(matching_candidates(clean_name(playlist),
                                                     {normalized_guide: [guide]}, index),
                                 [guide])

    def test_do_not_guess_similar_or_numbered_channels(self):
        by_name = {
            "ZDF": ["zdf.de"], "ZDFINFO": ["zdfinfo.de"],
            "ZDFNEO": ["zdfneo.de"],
            "SKY SPORT 1": ["sport1"],
            "SKY SPORT 2": ["sport2"],
            "SPORT 10": ["sport10"],
        }
        index = compact_index(by_name)
        self.assertEqual(matching_candidates("ZDFINFO", by_name, index), ["zdfinfo.de"])
        self.assertEqual(matching_candidates("ZDF NEU", by_name, index), ())
        self.assertEqual(matching_candidates("SKY SPORT 3", by_name, index), ())
        self.assertEqual(matching_candidates("SPORT 1 0", by_name, index), ())
        self.assertEqual(matching_candidates("SKY SPORT 2", by_name, index), ["sport2"])

    def test_ambiguous_compact_keys_are_not_auto_assigned(self):
        by_name = {"AB CDE": ["first"], "ABC DE": ["second"]}
        index = compact_index(by_name)
        self.assertNotIn(("ABCDE", ()), index)
        self.assertEqual(matching_candidates("A BCDE", by_name, index), ())
        # Exact spelling must keep working even when the compact key is ambiguous.
        self.assertEqual(matching_candidates("AB CDE", by_name, index), ["first"])

    def test_dashboard_and_xmltv_use_the_same_matching_fallback(self):
        source = SimpleNamespace(id="Tagesschau24.de",
                                 names=("tagesschau24",),
                                 feed=0, norm_names=frozenset({"TAGESSCHAU24"}))
        by_name = {"TAGESSCHAU24": [source]}
        index = compact_index(by_name)
        name = "JOYN: TAGESSCHAU 24 ᴿᴬᵂ"
        alias = "personal." + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
        with tempfile.TemporaryDirectory() as directory:
            inv, output = Path(directory) / "inventory.csv", Path(directory) / "dashboard.json"
            with inv.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["name", "epg_channel_id", "category_id"])
                writer.writeheader()
                writer.writerow({"name": "## JOYN RAW", "epg_channel_id": "", "category_id": "1357"})
                writer.writerow({"name": name, "epg_channel_id": "", "category_id": "1357"})
            report = make_dashboard(
                inventory_path=inv, output_path=output,
                ppv_rows=[], always_on_rows=[], named_rows=[],
                by_id={"tagesschau24.de": source}, by_name=by_name,
                by_compact=index, aliases={alias: source},
                feeds=[SimpleNamespace(label="DE-EPG", market="DE")],
                clean_name=clean_name, market_of=market_of,
                best=lambda candidates, market, feeds, provider_id: candidates[0] if candidates else None,
                id_valid=id_valid, live_ppv=False,
            )
            self.assertEqual(report["rows"][0][4], alias)
            self.assertEqual(report["rows"][0][5], "programm")


if __name__ == "__main__":
    unittest.main()
