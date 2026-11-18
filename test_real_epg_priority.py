"""Real schedules must beat placeholders under the same stable XMLTV ID."""
import csv
import io
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from build import clean_name, id_valid, market_of
from dashboard_data import make_dashboard
from name_matching import compact_index
from provider_epg import emit_provider_programmes
from real_epg_priority import genuine_source, resolve_placeholder_programmes


class RealEpgPriorityTests(unittest.TestCase):
    def setUp(self):
        self.feeds = [SimpleNamespace(label="DE", market="DE"),
                      SimpleNamespace(label="GR", market="GR"),
                      SimpleNamespace(label="US", market="US")]
        self.kika = self.channel("Kika.de", "KiKA", 0, 15)
        self.disney = self.channel("DisneyChannel.de", "Disney Channel", 0, 25)
        self.zdf = self.channel("zdf.de", "ZDF", 0, 20)
        self.us_disney = self.channel("DisneyChannel.us", "Disney Channel", 2, 20)
        self.by_id = {x.id.casefold(): x
                      for x in [self.kika, self.disney, self.zdf, self.us_disney]}
        self.by_name = defaultdict(list)
        for channel in self.by_id.values():
            for n in channel.norm_names:
                self.by_name[n].append(channel)
        self.by_compact = compact_index(self.by_name)

    @staticmethod
    def channel(xmltv_id, name, feed, programmes):
        return SimpleNamespace(
            id=xmltv_id, feed=feed, programmes=programmes,
            names=(name,), norm_names=frozenset({clean_name(name)}),
            element=ET.Element("channel", {"id": xmltv_id}))

    def match(self, name, provider_id="", region=""):
        return genuine_source({"name": name, "epg_channel_id": provider_id,
                               "region": region},
                              by_id=self.by_id, by_name=self.by_name,
                              by_compact=self.by_compact, feeds=self.feeds,
                              clean_name=clean_name, id_valid=id_valid)

    def test_named_kids_and_disney_use_real_broadcast_guide(self):
        self.assertIs(self.match("DE: KIKA HD"), self.kika)
        self.assertIs(self.match("DE: DISNEY CHANNEL HD"), self.disney)
        self.assertIs(self.match("PRIME: KIKA RAW"), self.kika)
        self.assertIs(self.match("JOYN: KIKA RAW"), self.kika)
        self.assertIs(self.match("US: DISNEY CHANNEL HD"), self.us_disney)
        self.assertIs(self.match("DE: KIKA HD", "Kika.de"), self.kika)

    def test_wrong_country_or_unrelated_channel_never_promoted(self):
        self.assertIsNone(self.match("GR: DISNEY CHANNEL HD"))
        self.assertIsNone(self.match("DE: DISNEY CHANNEL WEST HD"))
        self.assertIsNone(self.match("DE: ZDFINFO HD"))
        self.assertIsNone(self.match("AT&T: DISNEY CHANNEL HD"))
        # A wrong direct provider ID must not override an exact trusted name.
        self.assertIs(self.match("DE: DISNEY CHANNEL HD", "zdf.de"), self.disney)

    def test_ambiguous_real_guides_and_missing_programmes_stay_placeholder(self):
        duplicate = self.channel("KikaAlt.de", "KiKA", 0, 99)
        by_name = dict(self.by_name)
        by_name["KIKA"] = [self.kika, duplicate]
        source = genuine_source(
            {"name": "DE: KIKA HD"}, by_id=self.by_id, by_name=by_name,
            by_compact=compact_index(by_name), feeds=self.feeds,
            clean_name=clean_name, id_valid=id_valid)
        self.assertIsNone(source)
        no_schedule = self.channel("NoGuide.de", "TEST TV", 0, 0)
        source = genuine_source(
            {"name": "DE: TEST TV"}, by_id={"noguide.de": no_schedule},
            by_name={"TEST TV": [no_schedule]},
            by_compact=compact_index({"TEST TV": [no_schedule]}),
            feeds=self.feeds, clean_name=clean_name, id_valid=id_valid)
        self.assertIsNone(source)

    def test_preserve_existing_id_when_real_programme_found(self):
        rows = [
            {"id": "named.static.kika", "name": "DE: KIKA HD", "category_id": "334"},
            {"id": "named.live.disney", "name": "DE: DISNEY CHANNEL HD", "category_id": "334"},
            {"id": "always.old", "name": "US: DISNEY CHANNEL HD", "category_id": "999"},
            {"id": "ppv.live.unknown", "name": "DE: UNLISTED MATCH 2026", "category_id": "101"},
            {"id": "named.static.missing", "name": "DE: UNKNOWN FILM 4K", "category_id": "332"},
            {"id": "always.provider", "name": "GR: GUIDE WITH PROVIDER ID",
             "epg_channel_id": "provider.gr", "category_id": "1470"},
            {"id": "always.ts", "name": "GR: NO GUIDE", "epg_channel_id": "TS", "category_id": "1470"},
        ]
        public, provider = resolve_placeholder_programmes(
            rows, by_id=self.by_id, by_name=self.by_name,
            by_compact=self.by_compact, feeds=self.feeds,
            clean_name=clean_name, id_valid=id_valid,
            provider_fallback_ids={"provider.gr": "Provider.gr"})
        self.assertEqual(set(public), {"named.static.kika", "named.live.disney", "always.old"})
        self.assertEqual(public["named.static.kika"].id, "Kika.de")
        self.assertEqual(provider, {"always.provider": "Provider.gr"})
        self.assertNotIn("ppv.live.unknown", public)
        self.assertNotIn("named.static.missing", public)

    def test_dashboard_live_real_id_does_not_duplicate_old_inventory_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = Path(tmp) / "inventory.csv"
            out = Path(tmp) / "dashboard.json"
            with inv.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["name", "epg_channel_id", "category_id"])
                writer.writeheader()
                writer.writerow({"name": "## COSMOTE FILM 24/7", "epg_channel_id": "", "category_id": "1470"})
                writer.writerow({"name": "GR: MOVIE OLD", "epg_channel_id": "Provider.gr",
                                 "category_id": "1470"})
            report = make_dashboard(
                inventory_path=inv, output_path=out,
                ppv_rows=[],
                always_on_rows=[{
                    "id": "always.old", "name": "GR: MOVIE NEW",
                    "category_id": "1470", "source": "live",
                    "epg_channel_id": "Provider.gr"}],
                named_rows=[], by_id=self.by_id,
                by_name=self.by_name, by_compact=self.by_compact,
                aliases={}, feeds=self.feeds, clean_name=clean_name,
                market_of=market_of, best=lambda *args: None,
                id_valid=id_valid, live_ppv=True, live_all_placeholders=True,
                static_always_names={"GR: MOVIE OLD"},
                real_placeholder_ids={"always.old"})
            self.assertEqual(report["total"], 1)
            self.assertEqual(report["rows"][0][0], "GR: MOVIE NEW")
            self.assertEqual(report["rows"][0][4:6], ["always.old", "programm"])

    def test_verified_epg_spelling_variants_recover_only_two_checked_ids(self):
        from types import SimpleNamespace
        alpha = self.channel("ALPHA.gr", "GR - ALPHA", 1, 15)
        euro = self.channel("Eurosport1.de", "DE - Eurosport 1", 0, 20)
        with tempfile.TemporaryDirectory() as tmp:
            inv, out = Path(tmp) / "inventory.csv", Path(tmp) / "dashboard.json"
            entries = [
                ("GR: ALPH ᴴᴰ", "ALPHA.gr", "4"),
                ("DE: EURO SPORT 1 ᴿᴬᵂ", "Eurosport1.de", "2032"),
                ("DE: EURO SPORT 2 ᴿᴬᵂ", "Eurosport1.de", "2032"),
            ]
            with inv.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f, fieldnames=["name", "epg_channel_id", "category_id"])
                writer.writeheader()
                for name, epg_id, category in entries:
                    writer.writerow({"name": name, "epg_channel_id": epg_id,
                                     "category_id": category})
            report = make_dashboard(
                inventory_path=inv, output_path=out, ppv_rows=[],
                always_on_rows=[], named_rows=[],
                by_id={"alpha.gr": alpha, "eurosport1.de": euro},
                by_name={}, by_compact={}, aliases={}, feeds=self.feeds,
                clean_name=clean_name, market_of=market_of,
                best=lambda *args: None, id_valid=id_valid, live_ppv=False,
            )
            statuses = {r[0]: (r[4], r[5]) for r in report["rows"]}
            self.assertEqual(statuses["GR: ALPH ᴴᴰ"],
                             ("ALPHA.gr", "programm"))
            self.assertEqual(statuses["DE: EURO SPORT 1 ᴿᴬᵂ"],
                             ("Eurosport1.de", "programm"))
            self.assertEqual(statuses["DE: EURO SPORT 2 ᴿᴬᵂ"][1], "pruefen")

    def test_provider_xmltv_copies_only_real_titles_to_stable_alias(self):
        doc = (
            '<tv><channel id="Provider.gr"><display-name>Provider</display-name></channel>'
            '<programme channel="Provider.gr" start="20261118000000 +0000" '
            'stop="20261118010000 +0000"><title>Actual programme</title>'
            '<icon src="https://private.example/token"/></programme></tv>'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.xml"
            path.write_text(doc, encoding="utf-8")
            out = io.BytesIO()
            amount = emit_provider_programmes(
                path, out, {"provider.gr": "Provider.gr"},
                aliases_by_source={"provider.gr": {"always.oldprovider"}})
            self.assertEqual(amount, 2)
            result = ET.fromstring(b"<tv>" + out.getvalue() + b"</tv>")
            self.assertEqual(
                {x.get("channel") for x in result.findall("programme")},
                {"Provider.gr", "always.oldprovider"})
            self.assertTrue(all(p.findtext("title") == "Actual programme"
                                for p in result.findall("programme")))
            self.assertNotIn(b"private.example", out.getvalue())

    def test_webapp_counts_promoted_and_unpromoted_placeholders_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = Path(tmp) / "inventory.csv"
            out = Path(tmp) / "dashboard.json"
            with inv.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["name", "epg_channel_id", "category_id"])
                writer.writeheader()
                writer.writerow({"name": "## KIDS HD/4K", "epg_channel_id": "", "category_id": "334"})
                writer.writerow({"name": "DE: KIKA HD", "epg_channel_id": "Kika.de", "category_id": "334"})
                writer.writerow({"name": "DE: NOT ON TV", "epg_channel_id": "", "category_id": "334"})
            rows = [
                {"id": "named.static.kika", "name": "DE: KIKA HD", "category_id": "334", "source": "static"},
                {"id": "named.static.unknown", "name": "DE: NOT ON TV", "category_id": "334", "source": "static"},
            ]
            report = make_dashboard(
                inventory_path=inv, output_path=out,
                ppv_rows=[], always_on_rows=[], named_rows=rows,
                by_id=self.by_id, by_name=self.by_name, by_compact=self.by_compact,
                aliases={}, feeds=self.feeds, clean_name=clean_name,
                market_of=market_of, best=lambda *args: None,
                id_valid=id_valid, live_ppv=False,
                real_placeholder_ids={"named.static.kika"})
            statuses = {row[0]: (row[4], row[5]) for row in report["rows"]}
            self.assertEqual(statuses["DE: KIKA HD"], ("named.static.kika", "programm"))
            self.assertEqual(statuses["DE: NOT ON TV"],
                             ("named.static.unknown", "platzhalter"))
            self.assertEqual(report["counts"]["programm"], 1)
            self.assertEqual(report["counts"]["platzhalter"], 1)


if __name__ == "__main__":
    unittest.main()
