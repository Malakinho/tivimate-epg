"""A placeholder must follow the Xtream name while retaining its XMLTV ID."""
import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dashboard_data import make_dashboard
from build import clean_name, market_of, id_valid
from placeholder_history import write_bindings
from xtream_ppv import current_ppv_rows

ACCESS = "https://iptv.example/get.php?username=user&password=pass&type=m3u_plus"


class PlaceholderRenameTest(unittest.TestCase):
    def test_rename_all_placeholder_types_and_keep_preexisting_ids(self):
        streams = [
            {"stream_id": 10, "name": "DE: BOX ACTION 4K", "category_id": "332"},
            {"stream_id": 11, "name": "GR: COSMOTE FILM 1 4K", "category_id": "1470", "epg_channel_id": ""},
            {"stream_id": 12, "name": "PRIME: DATELINE 24/7 RAW", "category_id": "1339"},
            {"stream_id": 13, "name": "Event: Team A vs Team B", "category_id": "33"},
            {"stream_id": 14, "name": "DE: LIVE REGULAR TV", "category_id": "88", "epg_channel_id": "regular.de"},
        ]
        categories = [
            {"category_id": "332", "category_name": "VIP BOX FILME"},
            {"category_id": "1470", "category_name": "COSMOTE FILM 24/7"},
            {"category_id": "1339", "category_name": "PRIME RAW"},
            {"category_id": "33", "category_name": "DAZN PPV"},
            {"category_id": "88", "category_name": "NORMAL TV"},
        ]
        def mock_open(req, timeout=0):
            class Resp:
                def __init__(self, data): self.data = data
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self, n=-1): return self.data
            data = categories if "get_live_categories" in req.full_url else streams
            return Resp(json.dumps(data).encode())

        legacy = {
            ("named", "332", "DE: BOX ACTION 4K"): "named.static.oldbox",
            ("always", "1470", "GR: COSMOTE FILM 1 4K"): "always.oldcosmote",
            ("always", "1339", "PRIME: DATELINE 24/7 RAW"): "always.olddateline",
        }
        with patch("xtream_ppv.urlopen", side_effect=mock_open):
            ppv, named, always = current_ppv_rows(
                ACCESS, {"33"}, {"332"}, {"1470"},
                always_names={("1339", "PRIME: DATELINE 24/7 RAW")},
                legacy_ids=legacy)
            self.assertEqual(len(ppv), 1)
            self.assertEqual(len(named), 1)
            self.assertEqual(len(always), 2)
            self.assertEqual(named[0]["id"], "named.static.oldbox")
            self.assertEqual({r["id"] for r in always},
                             {"always.oldcosmote", "always.olddateline"})

            # The historical map matches the same stream after renaming, even
            # when the updated title no longer contains "24/7" at all.
            previous = {r["fingerprint"]: {"kind": "always", "id": r["id"]}
                        for r in always}
            previous[named[0]["fingerprint"]] = {
                "kind": "named", "id": named[0]["id"]}
            streams[0]["name"] = "DE: BOX NEW FILM 4K"
            streams[1]["name"] = "GR: COSMOTE FILM 1 4K – UPDATE"
            streams[2]["name"] = "PRIME: DATELINE CLASSICS"
            streams[3]["name"] = "Event: Team C vs Team D"
            ppv2, named2, always2 = current_ppv_rows(
                ACCESS, {"33"}, {"332"}, {"1470"},
                previous_bindings=previous)
            self.assertEqual(named2[0]["id"], named[0]["id"])
            self.assertEqual(named2[0]["name"], "DE: BOX NEW FILM 4K")
            self.assertEqual([(r["name"], r["id"]) for r in always2],
                             [("GR: COSMOTE FILM 1 4K – UPDATE", "always.oldcosmote"),
                              ("PRIME: DATELINE CLASSICS", "always.olddateline")])
            self.assertEqual(ppv2[0]["id"], ppv[0]["id"])
            self.assertEqual(ppv2[0]["name"], "Event: Team C vs Team D")

            with tempfile.TemporaryDirectory() as tmp:
                filename = Path(tmp) / "placeholder-bindings.json"
                count = write_bindings(filename, previous, named2, always2)
                self.assertEqual(count, 3)
                raw = filename.read_text()
                self.assertNotIn("stream_id", raw)
                self.assertNotIn("password", raw)
                self.assertNotIn("DATELINE CLASSICS", raw)

    def test_avoid_leaking_real_tv_epg_channels_to_placeholders(self):
        streams = [
            {"stream_id": 1, "category_id": "1470", "name": "GR: FILM REAL EPG", "epg_channel_id": "film.gr"},
            {"stream_id": 2, "category_id": "1470", "name": "GR: FILM PLACEHOLDER", "epg_channel_id": "TS"},
            {"stream_id": 3, "category_id": "88", "name": "DE: NORMAL CHANNEL", "epg_channel_id": ""},
            {"stream_id": 4, "category_id": "33", "name": "DE: DAZN PPV", "epg_channel_id": ""},
        ]
        categories = [
            {"category_id": "1470", "category_name": "24/7 FILMS"},
            {"category_id": "88", "category_name": "NORMAL"},
            {"category_id": "33", "category_name": "PPV"},
        ]
        def fake(req, timeout=0):
            import io
            data = categories if "get_live_categories" in req.full_url else streams
            return io.BytesIO(json.dumps(data).encode())
        with patch("xtream_ppv.urlopen", side_effect=fake):
            ppv, named, always = current_ppv_rows(ACCESS, {"33"}, set(), {"1470"})
        self.assertEqual(len(ppv), 1)
        self.assertFalse(named)
        self.assertEqual([r["name"] for r in always], ["GR: FILM PLACEHOLDER"])

    def test_dashboard_keeps_static_placeholders_for_unavailable_categories(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            inv = Path(tmp) / "inventory.csv"
            output = Path(tmp) / "dashboard.json"
            with inv.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=["name", "epg_channel_id", "category_id"])
                writer.writeheader()
                for name, cat in (("## 24/7 MOVIES", "619"),
                                  ("US: OLD FILM", "619"),
                                  ("## VIP BOX FILME", "332"),
                                  ("DE: BOX OLD", "332")):
                    writer.writerow({"name": name, "epg_channel_id": "", "category_id": cat})
            report = make_dashboard(
                inventory_path=inv, output_path=output, ppv_rows=[],
                always_on_rows=[{"id": "always.old", "name": "US: OLD FILM",
                                 "category_id": "619"}],
                named_rows=[{"id": "named.static.old", "name": "DE: BOX OLD",
                             "category_id": "332", "source": "static"}],
                by_id={}, by_name={}, aliases={}, feeds=[], by_compact={},
                clean_name=clean_name, market_of=market_of,
                best=lambda *args: None, id_valid=id_valid, live_ppv=True,
                live_all_placeholders=True, named_category_ids={"332"},
                static_always_names={"US: OLD FILM"})
            self.assertEqual({row[0] for row in report["rows"]},
                             {"US: OLD FILM", "DE: BOX OLD"})
            self.assertTrue(all(row[5] == "platzhalter" for row in report["rows"]))

    def test_dashboard_does_not_show_stale_legacy_24_7_title(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            inv = Path(tmp) / "inventory.csv"
            output = Path(tmp) / "dashboard.json"
            with inv.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=["name", "epg_channel_id", "category_id"])
                writer.writeheader()
                for name, cat in (("## 24/7 MOVIES", "619"),
                                  ("US: OLD FILM", "619"),
                                  ("## VIP BOX FILME", "332"),
                                  ("DE: BOX OLD", "332")):
                    writer.writerow({"name": name, "epg_channel_id": "", "category_id": cat})
            report = make_dashboard(
                inventory_path=inv, output_path=output, ppv_rows=[],
                always_on_rows=[{"id": "always.old", "name": "US: FILM NEW",
                                 "category_id": "619", "category": "24/7 MOVIES",
                                 "source": "live"}],
                named_rows=[{"id": "named.static.old", "name": "DE: BOX NEW",
                             "category_id": "332", "source": "live"}],
                by_id={}, by_name={}, aliases={}, feeds=[], by_compact={},
                clean_name=clean_name, market_of=market_of,
                best=lambda *args: None, id_valid=id_valid, live_ppv=True,
                live_all_placeholders=True, named_category_ids={"332"},
                static_always_names={"US: OLD FILM"})
            self.assertEqual(report["total"], 2)
            names = {row[0] for row in report["rows"]}
            self.assertEqual(names, {"US: FILM NEW", "DE: BOX NEW"})


if __name__ == "__main__":
    unittest.main()
