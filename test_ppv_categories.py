"""Regression tests: US and DE PPV events use live channel names as EPG titles."""
import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import xtream_ppv

ACCESS = "https://example.test/get.php?username=test-user&password=test-password&type=m3u_plus"
CHANNELS = [
    {"name": "US (ESPN+ 001) | Event A", "stream_id": "11", "category_id": "911"},
    {"name": "FloSport : Hockey Final", "stream_id": "12", "category_id": "2014"},
    {"name": "US (Peacock 001) | Event B", "stream_id": "13", "category_id": "1394"},
    {"name": "DE: DAZN PPV 1", "stream_id": "14", "category_id": "433"},
    {"name": "US: BET", "stream_id": "15", "category_id": "2"},
]
CATEGORIES = [
    {"category_id": "911", "category_name": "ESPN+ PPV"},
    {"category_id": "2014", "category_name": "FLO PPV"},
    {"category_id": "1394", "category_name": "PEACOCK PPV"},
    {"category_id": "433", "category_name": "DAZN PPV DE"},
    {"category_id": "2", "category_name": "US Entertainment"},
]


def mock_fetch(url, timeout=90):
    action = parse_qs(urlsplit(url.full_url).query).get("action", [""])[0]
    if action == "get_live_streams":
        return io.BytesIO(json.dumps(CHANNELS).encode())
    if action == "get_live_categories":
        return io.BytesIO(json.dumps(CATEGORIES).encode())
    raise AssertionError("Unexpected API request")


class TestPPVCategories(unittest.TestCase):
    @patch("xtream_ppv.urlopen", side_effect=mock_fetch)
    def test_usa_ppv_identified_by_category(self, unused):
        rows = xtream_ppv.current_ppv_rows(ACCESS, {"911", "2014", "1394", "433"})
        self.assertEqual(len(rows), 4)
        self.assertEqual(
            {r["name"] for r in rows},
            {"US (ESPN+ 001) | Event A", "FloSport : Hockey Final",
             "US (Peacock 001) | Event B", "DE: DAZN PPV 1"},
        )
        self.assertEqual(next(r["region"] for r in rows if "Peacock" in r["name"]), "US")
        self.assertTrue(all(r["id"].startswith("ppv.live.") for r in rows))

    @patch("xtream_ppv.urlopen")
    def test_category_api_failure_uses_static_categories(self, mock_urlopen):
        def fake(url, timeout=90):
            if "get_live_categories" in url.full_url:
                raise OSError("Category endpoint unavailable")
            return mock_fetch(url, timeout)
        mock_urlopen.side_effect = fake
        rows = xtream_ppv.current_ppv_rows(ACCESS, {"911", "2014", "1394", "433"})
        self.assertEqual(len(rows), 4)

    def test_compact_and_stylized_ppv_names(self):
        from dashboard_data import PPV as DASHBOARD_PPV
        for title in ("RO: PRIMA SPORT PPV1", "RO: PRIMA SPORT PPV2",
                      "RO: PRIMA SPORT PPV3", "GR: ERT SPORTS 4 ᴾᴾⱽ"):
            self.assertTrue(xtream_ppv.PPV_LABEL.search(title), title)
            self.assertTrue(DASHBOARD_PPV.search(title), title)
        self.assertIsNone(xtream_ppv.PPV_LABEL.search("US: BET"))

    @patch("xtream_ppv.urlopen", side_effect=mock_fetch)
    def test_relabel_keeps_same_epg_id(self, unused):
        initial = xtream_ppv.current_ppv_rows(ACCESS, {"911"})
        old_id = next(r["id"] for r in initial if r["name"].startswith("US (ESPN"))
        with patch.dict(CHANNELS[0], {"name": "US (ESPN+ 001) | Brand new event"}):
            updated = xtream_ppv.current_ppv_rows(ACCESS, {"911"})
        new_id = next(r["id"] for r in updated if r["name"].startswith("US (ESPN"))
        self.assertEqual(old_id, new_id)
        self.assertIn("Brand new event", updated[0]["name"])


if __name__ == "__main__":
    unittest.main()
