#!/usr/bin/env python3
"""Build one XMLTV guide from publicly available German and Greek guides.

No credentials or IPTV stream URLs are read, downloaded or published.
Use: python build.py  (Python 3.10+)
"""
from __future__ import annotations

import copy
import csv
import gzip
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from xtream_ppv import current_ppv_rows
from named_categories import named_category_ids, static_named_rows
from name_matching import compact_index, matching_candidates
from real_epg_priority import resolve_placeholder_programmes
from diagnostics import begin_source_report, mark_source, public_source_report
from placeholder_history import previous_state, write_bindings
from dashboard_data import make_dashboard
from provider_epg import fetch_provider_xmltv, provider_coverage, emit_provider_programmes

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "channels.csv"
PPV_DATA = ROOT / "data" / "ppv.csv"
ALWAYS_ON_DATA = ROOT / "data" / "always_on.csv"
FRIENDS_DATA = ROOT / "data" / "friends.json"
OUT_DIR = ROOT / "site"
SOURCES = [
    ("DE-EPG", "DE", "https://iptv-epg.org/files/epg-de.xml"),
    ("GR-EPG", "GR", "https://iptv-epg.org/files/epg-gr.xml"),
    ("GR-GREEKTV", "GR", "https://ext.greektv.app/epg/epg.xml.gz"),
    ("GR-COSMOTE", "GR", "https://iptv-org.github.io/epg/guides/el/cosmote.gr.xml"),
    ("GR-NOVASPORTS", "GR", "https://iptv-org.github.io/epg/guides/el/novasports.gr.xml"),
    ("DE-MASTER", "DE", "https://kaimandura.github.io/epg-de/DE-MASTER.xml.gz"),
    # Supplemental, publicly accessible XMLTV feeds. Never supersede an
    # existing channel ID from our established sources.
    ("DE-FREEEPG", "DE", "https://free-epg.de/api/epg/de.xml.gz"),
    ("GR-FREEEPG", "GR", "https://free-epg.de/api/epg/gr.xml.gz"),
    ("DE-XMLTVLIVE", "DE", "https://xmltv.live/epg/de.xml.gz"),
    ("GR-XMLTVLIVE", "GR", "https://xmltv.live/epg/gr.xml.gz"),
    # Country-specific optional feeds. Only genuinely relevant channels from
    # these large sources are selected into the final merged XMLTV.
    ("FR-IPTV", "FR", "https://iptv-epg.org/files/epg-fr.xml.gz"),
    ("IT-IPTV", "IT", "https://iptv-epg.org/files/epg-it.xml.gz"),
    ("PL-IPTV", "PL", "https://iptv-epg.org/files/epg-pl.xml.gz"),
    ("HR-IPTV", "HR", "https://iptv-epg.org/files/epg-hr.xml.gz"),
    ("NL-IPTV", "NL", "https://iptv-epg.org/files/epg-nl.xml.gz"),
    ("US-IPTV", "US", "https://iptv-epg.org/files/epg-us.xml.gz"),
]
EXTRA_FEED_LABELS = {"DE-FREEEPG", "GR-FREEEPG", "DE-XMLTVLIVE", "GR-XMLTVLIVE",
                     "FR-IPTV", "IT-IPTV", "PL-IPTV", "HR-IPTV", "NL-IPTV", "US-IPTV"}
SELECTIVE_COUNTRY_FEEDS = {"FR-IPTV", "IT-IPTV", "PL-IPTV", "HR-IPTV", "NL-IPTV", "US-IPTV"}
# These are design hints only. Do not infer the actual licence for TV metadata.
MAX_DOWNLOAD = 180 * 1024 * 1024
BAD_IDS = {"", "TS", "N/A", "NONE", "NULL", "0", "-"}
PREFIX = re.compile(r"^(?:DE GO|DE|GR|PRIME|JOYN|WOW|SKYGO|FR|IT|PL|HR|NL|US|UK)\s*[:\-]\s*", re.I)
QUALITY = re.compile(
    r"\b(?:RAW|HEVC|H265|H264|SD|FHD|HD|UHD|4K|8K|720P|1080P|2160P|"
    r"50FPS|60FPS|30FPS|3840P|EON|WEB|MOBILE|MOBIL|BACKUP|BK|LOW|"
    r"BITRATE|STREAM|DOLBY|HDR)\b", re.I
)


def clean_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).upper()
    value = PREFIX.sub("", value)
    value = re.sub(r"\([^)]*(?:WEB|SAT|KABEL|MOBIL|BK|BACKUP|720|1080|2160|HEVC)[^)]*\)", " ", value)
    value = re.sub(r"[◉●◆★☆■▣⭐]", " ", value)
    value = QUALITY.sub(" ", value)
    value = re.sub(r"[^A-Z0-9ÄÖÜΑ-ΩΆΈΉΊΌΎΏ&+]", " ", value)
    value = " ".join(value.split())
    # Only very narrow, well-known aliases. No speculative Sky or DAZN numbering.
    value = re.sub(r"\bDAZN\s+([12])\b", r"DAZN\1", value)
    value = re.sub(r"\bERT\s+([123])\b", r"ERT\1", value)
    value = re.sub(r"\bANT\s+1\b", "ANT1", value)
    value = re.sub(r"\b3\s+SAT\b", "3SAT", value)
    # Whitelisted whitespace variants of the same public TV channel.
    # In particular: JOYN: ZDF INFO RAW == PRIME: ZDFINFO RAW == ZDFinfo.de.
    # Keep ZDF, ZDFinfo, ZDFneo and any numbered variants distinct.
    value = re.sub(r"\bZDF\s+INFO\b", "ZDFINFO", value)
    value = re.sub(r"\bZDF\s+NEO\b", "ZDFNEO", value)
    value = re.sub(r"\bSKY\s+SPORT\s+BUNDESLIGA\b", "SKY BUNDESLIGA", value)
    # Same Greek sports channel under differently formatted source names.
    value = re.sub(r"\bMAGENTA SPORT LEAGUE ([1-4])\b", r"MAGENTA SPORT \1 LEAGUE", value)
    value = re.sub(r"\bNOVA\s*SPORTS?\b", "NOVASPORTS", value)
    value = re.sub(
        r"\bNOVASPORTS(START|NEWS|PRIME|EXTRA|PREMIERLEAGUE)\b",
        lambda m: "NOVASPORTS " + m.group(1), value
    )
    value = value.replace("NOVASPORTS PREMIERE LEAGUE", "NOVASPORTS PREMIER LEAGUE")
    # Known spelling and typography variants for the very same channels.
    # Preserve distinct sports channel numbers: never fuzzy-match them.
    value = re.sub(r"\bRTL\s+UP\b", "RTLUP", value)
    value = re.sub(r"\bVOX\s+UP\b", "VOXUP", value)
    value = re.sub(r"\bRTL\s+ZWEI\b", "RTLZWEI", value)
    value = re.sub(r"\bBON\s+GUSTO\b", "BONGUSTO", value)
    value = re.sub(r"\bJUKE\s+BOX\b", "JUKEBOX", value)
    value = re.sub(r"\bKABEL\s+1\b", "KABEL EINS", value)
    value = re.sub(r"\bMY\s+TEAM\s+TV\b", "MYTEAMTV", value)
    value = re.sub(r"\bRED\s+BULL\s+TV\b", "REDBULLTV", value)
    value = re.sub(r"\bEDGE\s+SPORT\b", "EDGESPORT", value)
    value = re.sub(r"\bMAGENTA MUSIC\b", "MAGENTA MUSIK", value)
    value = re.sub(r"\bHIGH\s+TV\b", "HIGHTV", value)
    value = re.sub(r"\bRISE\s+TV\b", "RISETV", value)
    value = re.sub(r"\bDELTA\s+TV\b", "DELTATV", value)
    value = re.sub(r"\bTV\s+100(?:\s+SKG)?\b", "TV100", value)
    value = re.sub(r"\bNOVA\s*LIFE\b", "NOVALIFE", value)
    value = re.sub(r"\bEPIRUS\s+TV\s+1\b", "EPIRUSTV1", value)
    value = re.sub(r"\bPANIK\s+TV\b", "PANIKTV", value)
    value = re.sub(r"\bSTART\s+TV\b", "STARTTV", value)
    value = re.sub(r"\bCORFU\s+TV\b", "CORFUTV", value)
    value = re.sub(r"\bGNOMI\s+TV\b", "GNOMITV", value)
    value = re.sub(r"\bDION\s+TV\b", "DIONTV", value)
    value = re.sub(r"\bEURO\s+TV\b", "EUROTV", value)
    value = re.sub(r"\bART\s+TV\b", "ARTTV", value)
    value = re.sub(r"\bEXTRA\s+CHANNEL\b", "EXTRACHANNEL", value)
    value = re.sub(r"\bNAFTEMPORIKI\s+TV\b", "NAFTEMPORIKITV", value)
    value = re.sub(r"\bSYROS\s+TV1\b", "SYROSTV1", value)
    value = re.sub(r"\bSAMIAKI\s+TV\b", "SAMIAKITV", value)
    value = re.sub(r"\bIDJ\s+KIDS\b", "IDJKIDS", value)
    value = re.sub(r"\bNOVA\s+CINEMA\s*([1-4])\b", r"NOVACINEMA\1", value)
    value = re.sub(r"\bNOVACINEMA\s+([1-4])\b", r"NOVACINEMA\1", value)
    value = re.sub(r"\bANT1\s+COMMEDY\b", "ANT1 COMEDY", value)
    value = re.sub(r"\bERT\s+NEWS\b", "ERTNEWS", value)
    # Narrow confirmed spelling variants; do not generalize sports numbering.
    value = re.sub(r"^NTRO$", "NITRO", value)
    value = re.sub(r"^RTL NITRO$", "NITRO", value)
    value = re.sub(r"^SPORT 1$", "SPORT1", value)
    value = re.sub(r"^QVC (?:2|ZWEI)$", "QVC2", value)
    value = re.sub(r"^E ENTERTAINM$", "E ENTERTAINMENT", value)
    value = re.sub(r"^AUTO MOTOR SPORT CHANNEL$", "AUTO MOTOR SPORT", value)
    value = re.sub(r"^MY\s*ZEN TV$", "MYZEN TV", value)
    value = re.sub(r"^(?:ATTICA|ATTICA TV|ATTICATV)$", "ATTICATV", value)
    value = re.sub(r"^(?:AXELWOS|AXELWOSTV)$", "AXELWOSTV", value)
    value = re.sub(r"^(?:TV100 SKG|TV100)$", "TV100", value)
    value = re.sub(r"^(?:MEGA CHANNEL|MEGA)$", "MEGA", value)
    value = re.sub(r"^(?:BEST TV|BESTTV)$", "BESTTV", value)
    value = re.sub(r"^(?:ONE TV|ONETV)$", "ONETV", value)
    value = re.sub(r"^(?:OPEN BEYOND TV|OPEN BEYOND|OPENBEYOND)$", "OPENBEYOND", value)
    value = re.sub(r"^ERT SPORTS ([23])$", r"ERTSPORTS\1", value)
    value = re.sub(r"^NOVASPORTS (START|PREMIER LEAGUE) SAT$", r"NOVASPORTS \1", value)
    value = re.sub(r"^CRIME\s*\+\s*INVESTIGATION$", "CRIME INVESTIGATION", value)
    return value


def market_of(name: str) -> str:
    prefix = name.split(":", 1)[0].strip().upper()
    if prefix == "UK":
        return "GB"
    if prefix in {"GR", "DE", "FR", "IT", "PL", "HR", "NL", "US"}:
        return prefix
    return "DE"


def id_valid(v: str) -> bool:
    return v.strip().upper() not in BAD_IDS


@dataclass
class Feed:
    label: str
    market: str
    url: str
    path: Path


@dataclass
class Channel:
    feed: int
    id: str
    element: ET.Element
    names: tuple[str, ...]
    norm_names: frozenset[str]
    programmes: int = 0


def download(url: str, path: Path) -> None:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            req = Request(url, headers={"User-Agent": "TiviMate-Ein-EPG/1.0 (XMLTV merger)"})
            with urlopen(req, timeout=100) as response, path.open("wb") as out:
                total = 0
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_DOWNLOAD:
                        raise RuntimeError(f"Download exceeds {MAX_DOWNLOAD} bytes")
                    out.write(chunk)
            if path.stat().st_size < 100:
                raise ValueError("Source file is unexpectedly small")
            return
        except Exception as ex:
            last_error = ex
            logging.warning("Download %s attempt %s failed: %s", url, attempt, type(ex).__name__)
            if attempt < 3:
                time.sleep(attempt * 3)
    raise RuntimeError(f"Download failed for {url}: {type(last_error).__name__}") from last_error


def open_xml(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rb")
    return path.open("rb")


def greek_rebrand_names(normalized: str) -> set[str]:
    """Only equivalent Greek linear channels; never merge Sport with League."""
    match = re.fullmatch(
        r"(?:MAGENTA|COSMOTE) SPORTS? (?:LEAGUE ([1-4])|([1-9])(?: (LEAGUE|L))?|HIGHLIGHTS?)",
        normalized,
    )
    if match:
        lead_league, number, suffix = match.groups()
        league = lead_league or (number if suffix else None)
        if league and int(league) > 4:
            return set()
        if league:
            endings = (f"LEAGUE {league}", f"{league} LEAGUE", f"{league} L")
        elif number:
            endings = (number,)
        else:
            endings = ("HIGHLIGHT", "HIGHLIGHTS")
        return {f"{brand} {sport} {ending}"
                for brand in ("MAGENTA", "COSMOTE")
                for sport in ("SPORT", "SPORTS") for ending in endings}
    match = re.fullmatch(r"(?:MAGENTA|COSMOTE) (CINEMA [1-3]|SERIES|HISTORY)", normalized)
    if match:
        return {f"{brand} {match.group(1)}" for brand in ("MAGENTA", "COSMOTE")}
    return set()


def feed_normalized_names(names, market: str) -> frozenset[str]:
    keys = {clean_name(name) for name in names}
    keys.discard("")
    if market == "GR":
        for key in tuple(keys):
            keys.update(greek_rebrand_names(key))
    return frozenset(keys)


def inspect_feed(feed: Feed, idx: int) -> dict[str, Channel]:
    channels: dict[str, Channel] = {}
    counts: Counter[str] = Counter()
    with open_xml(feed.path) as file:
        parser = ET.iterparse(file, events=("start", "end"))
        root = None
        for event, elem in parser:
            if event == "start" and root is None:
                root = elem
            elif event == "end":
                if elem.tag == "channel":
                    xml_id = elem.get("id", "").strip()
                    names = tuple((n.text or "").strip() for n in elem.findall("display-name") if (n.text or "").strip())
                    if xml_id and names:
                        channels[xml_id] = Channel(idx, xml_id, copy.deepcopy(elem), names,
                                                   feed_normalized_names(names, feed.market))
                elif elem.tag == "programme":
                    cid = elem.get("channel", "")
                    if cid:
                        counts[cid] += 1
                # XMLTV uses channels before programme; do not let the tree grow with programmes.
                if elem.tag in {"channel", "programme"} and root is not None:
                    root.clear()
    for id_, ch in channels.items():
        ch.programmes = counts[id_]
    logging.info("%s: %s channels, %s with schedule, %s programmes", feed.label, len(channels),
                 sum(bool(ch.programmes) for ch in channels.values()), sum(counts.values()))
    return channels


def winner_score(channel: Channel, feeds: list[Feed]) -> tuple[int, int]:
    # Prefer actual programme coverage, with Germany's specialised guide preferred on ties.
    priority = {"DE-MASTER": 3, "DE-EPG": 2, "GR-EPG": 2}
    return channel.programmes, priority.get(feeds[channel.feed].label, 0)


def best(candidates: list[Channel], market: str, feeds: list[Feed], provider_id: str = "") -> Channel | None:
    # The Greek COSMOTE/MAGENTA rebrand is market-specific. Do not let
    # synthetic Greek name keys match an ordinary German Magenta service.
    candidates = [c for c in candidates
                  if feeds[c.feed].market == market
                  or not any(greek_rebrand_names(n) for n in c.norm_names)
                  or (market != "GR" and feeds[c.feed].market != "GR")]
    if not candidates:
        return None
    result = sorted(candidates, key=lambda c: (
        feeds[c.feed].market == market,
        c.id.lower() == provider_id.lower() if provider_id else False,
        c.id.lower().endswith("." + market.lower()),
        winner_score(c, feeds),
    ), reverse=True)
    return result[0]


def publish_personal_guides(master: Path) -> int:
    """Publish one distinct static XMLTV file for each active friend.

    GitHub Pages is PUBLIC: paths in the repository/artifacts are discoverable.
    This supports manual link management, not authenticated access or IP locks.
    The previous publicly shared /epg.xml.gz path is removed from the output.
    """
    with FRIENDS_DATA.open("r", encoding="utf-8") as source:
        registry = json.load(source)
    friends = registry.get("friends", [])
    if not isinstance(friends, list):
        raise ValueError("Friend registry must contain a friends list.")
    unique = set()
    public_rows = []
    active_count = 0
    for record in friends:
        uid = record["id"]
        label = record["label"]
        token = record["token"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{30,120}", token):
            raise ValueError("Invalid personal-link token in friends registry.")
        if token in unique:
            raise ValueError("Duplicate EPG friend token.")
        unique.add(token)
        active = bool(record.get("active"))
        if active:
            active_count += 1
            personal_file = OUT_DIR / "epg" / token / "epg.xml.gz"
            personal_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(master, personal_file)
        public_rows.append({
            "id": uid, "label": label, "active": active,
            "path": "epg/" + token + "/epg.xml.gz" if active else "",
        })
    # Remove stale, freely accessible public copy.  Do not leave any master
    # download route even if all friend links have been revoked.
    master.unlink(missing_ok=True)
    (OUT_DIR / "friends.json").write_text(
        json.dumps({"friends": public_rows, "public_notice":
                    "All GitHub Pages links and repository metadata are public; "
                    "links are not confidential and IP/device limits do not apply."},
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    logging.info("PERSONAL-LINKS: %d active of %d; old shared /epg.xml.gz removed",
                 active_count, len(friends))
    return active_count


def selected_country_names() -> dict[str, set[str]]:
    """Target only ordinary playlist channels, never PPV or 24/7 catalogues.

    Particularly for US, downloading the whole EPG is useful for coverage but
    copying *every* provider programme would make each friend file huge.
    """
    inv_path = ROOT / "data" / "inventory.csv"
    with inv_path.open("r", encoding="utf-8-sig", newline="") as file:
        inventory = list(csv.DictReader(file))
    headings: dict[str, str] = {}
    for row in inventory:
        title = (row.get("name") or "").strip()
        category_id = (row.get("category_id") or "").strip()
        if title.startswith("##") and category_id not in headings:
            headings[category_id] = title
    result: defaultdict[str, set[str]] = defaultdict(set)
    for row in inventory:
        title = (row.get("name") or "").strip()
        if not title or title.startswith("##"):
            continue
        market = market_of(title)
        if market not in {"FR", "IT", "PL", "HR", "NL", "US"}:
            continue
        category = headings.get((row.get("category_id") or "").strip(), "")
        if re.search(r"\bPPV\b|(?<!\d)24\s*[/\-]\s*7(?!\d)", title + " " + category, re.I):
            continue
        name = clean_name(title)
        if len(name) >= 3:
            result[market].add(name)
    logging.info("Country-specific EPG targets: %s", {k: len(v) for k,v in result.items()})
    return result


def extend_always_on_categories(always_on_rows: list[dict], input_rows: list[dict]) -> list[dict]:
    """Add name-as-programme placeholders for 24/7 *categories* with no EPG ID.

    Previously only 24/7 text in the channel name was exported.  Some entire
    category groups (e.g. 24/7 Prime Video, Disney+ or COSMOTE Film) have
    ordinary-looking channel names, so they incorrectly appeared as no EPG.
    Do not replace real provider programme IDs.
    """
    category_titles: dict[str, str] = {}
    for row in input_rows:
        name = (row.get("name") or "").strip()
        category_id = (row.get("category_id") or "").strip()
        if name.startswith("##") and category_id not in category_titles:
            category_titles[category_id] = name

    result = list(always_on_rows)
    existing = {(row.get("name") or "").strip() for row in result}
    added = 0
    for row in input_rows:
        name = (row.get("name") or "").strip()
        category_id = (row.get("category_id") or "").strip()
        provider_id = (row.get("epg_channel_id") or "").strip()
        category = category_titles.get(category_id, "")
        if (not name or name.startswith("##") or name in existing
                or id_valid(provider_id)
                or not re.search(r"(?<!\d)24\s*[/\-]\s*7(?!\d)", category, re.I)
                or re.search(r"\bPPV\b", category + " " + name, re.I)):
            continue
        result.append({
            "id": "always." + hashlib.sha256(name.encode("utf-8")).hexdigest()[:20],
            "name": name,
            "category_id": category_id,
        })
        existing.add(name)
        added += 1
    logging.info("24/7 category-based placeholders added: %d", added)
    return result


def build() -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with DATA.open("r", encoding="utf-8-sig", newline="") as csvfile:
        input_rows = list(csv.DictReader(csvfile))
    with (ROOT / "data" / "inventory.csv").open("r", encoding="utf-8-sig", newline="") as csvfile:
        full_inventory = list(csv.DictReader(csvfile))
    named_ids = named_category_ids(full_inventory)
    # Whole 24/7 categories are followed by stream ID, even when individual
    # stream names do not contain 24/7 (e.g. COSMOTE FILM 1).
    always_category_ids = {
        str(row.get("category_id") or "").strip()
        for row in full_inventory
        if (row.get("name") or "").strip().startswith("##")
        and re.search(r"(?<!\d)24\s*[/\-]\s*7(?!\d)", row.get("name") or "", re.I)
    }
    xtream_url = os.environ.get("XTREAM_M3U_URL", "").strip()
    with PPV_DATA.open("r", encoding="utf-8-sig", newline="") as csvfile:
        static_ppv_rows = list(csv.DictReader(csvfile))
    # Static CSV data is a fallback if no authenticated live provider exists.
    # Once live Xtream data is available, never publish stale CSV titles as
    # if they were current sender names.
    with ALWAYS_ON_DATA.open("r", encoding="utf-8-sig", newline="") as csvfile:
        always_on_static = extend_always_on_categories(list(csv.DictReader(csvfile)),
                                                       full_inventory)
    previous_bindings, previous_aliases = {}, {}
    if xtream_url:
        previous_bindings, previous_aliases = previous_state()
        legacy_ids = {
            ("always", str(row.get("category_id") or ""), row["name"]): row["id"]
            for row in always_on_static if row.get("name") and row.get("id")
        }
        legacy_ids.update({
            ("named", row["category_id"], row["name"]): row["id"]
            for row in static_named_rows(full_inventory, named_ids)
        })
        # Prefer the ID actually offered by the last published XMLTV guide.
        legacy_ids.update(previous_aliases)
        known_ppv_categories = {
            str(item.get("category_id") or "").strip()
            for item in static_ppv_rows if item.get("category_id")
        }
        ppv_rows, live_named_rows, live_always_rows = current_ppv_rows(
            xtream_url, known_ppv_categories, named_ids,
            always_category_ids=always_category_ids,
            always_names={(str(row.get("category_id") or ""), row["name"])
                          for row in always_on_static if row.get("name")},
            legacy_ids=legacy_ids, previous_bindings=previous_bindings,
        )
        # Prefer current stream names. Retain old static placeholders only
        # for whole categories missing from the live API so previously
        # published EPG entries do not suddenly vanish.
        live_named_categories = {r["category_id"] for r in live_named_rows}
        live_always_categories = {r["category_id"] for r in live_always_rows}
        published_always_ids = {r["id"] for r in live_always_rows}
        named_static_fallback = static_named_rows(
            full_inventory, named_ids - live_named_categories)
        always_static_fallback = [
            r for r in always_on_static
            if str(r.get("category_id") or "") not in live_always_categories
            and r.get("id") not in published_always_ids
        ]
        named_rows = live_named_rows + named_static_fallback
        always_on_rows = live_always_rows + always_static_fallback
        logging.info("Current placeholder names: PPV=%d, named=%d live + %d fallback, 24/7=%d live + %d fallback",
                     len(ppv_rows), len(live_named_rows), len(named_static_fallback),
                     len(live_always_rows), len(always_static_fallback))
    else:
        live_named_rows, live_always_rows = [], []
        ppv_rows = static_ppv_rows
        always_on_rows = always_on_static
        named_rows = static_named_rows(full_inventory, named_ids)
        logging.info("Static fallback: no XTREAM_M3U_URL configured")
    placeholder_rows = ppv_rows + always_on_rows + named_rows
    with tempfile.TemporaryDirectory() as temp:
        temp_dir = Path(temp)
        feeds: list[Feed] = []
        source_report = begin_source_report(SOURCES)
        for name, market, url in SOURCES:
            file_name = f"{name}.xml.gz" if url.endswith(".gz") else f"{name}.xml"
            destination = temp_dir / file_name
            try:
                download(url, destination)
                feeds.append(Feed(name, market, url, destination))
            except Exception as error:
                mark_source(source_report, name, "failed", "download")
                logging.warning("Skipping source %s: %s", name, type(error).__name__)
        if not feeds:
            raise RuntimeError("No live EPG source could be downloaded. No output overwritten.")

        per_feed = []
        working_feeds = []
        for feed in feeds:
            try:
                channels = inspect_feed(feed, len(working_feeds))
                programme_count = sum(ch.programmes for ch in channels.values())
                if not programme_count and feed.label in EXTRA_FEED_LABELS:
                    mark_source(source_report, feed.label, "empty", "no_programmes",
                                len(channels), 0)
                    logging.warning("Skipping source without programmes: %s", feed.label)
                    continue
                mark_source(source_report, feed.label,
                            "ok" if programme_count else "empty",
                            "" if programme_count else "no_programmes",
                            len(channels), programme_count)
                per_feed.append(channels)
                working_feeds.append(feed)
            except Exception as error:
                mark_source(source_report, feed.label, "failed", "invalid_feed")
                if feed.label not in EXTRA_FEED_LABELS:
                    raise
                logging.warning("Skipping unusable optional EPG %s: %s", feed.label, type(error).__name__)
        feeds = working_feeds
        targets_by_country = selected_country_names()
        allowed_ids_by_feed: dict[str, set[str]] = {}
        for feed, channels in zip(feeds, per_feed):
            if feed.label not in SELECTIVE_COUNTRY_FEEDS:
                continue
            requested = targets_by_country.get(feed.market, set())
            by_name_source: defaultdict[str, set[str]] = defaultdict(set)
            for channel in channels.values():
                if not channel.programmes:
                    continue
                for clean in channel.norm_names & requested:
                    by_name_source[clean].add(channel.id)
            accepted = {next(iter(ids)) for ids in by_name_source.values() if len(ids) == 1}
            allowed_ids_by_feed[feed.label] = accepted
            logging.info("%s selected only %s relevant EPG channels", feed.label, len(accepted))
        by_id: dict[str, Channel] = {}
        for feed_channels in per_feed:
            for ch in feed_channels.values():
                if ch.programmes <= 0:
                    continue
                if feeds[ch.feed].label in SELECTIVE_COUNTRY_FEEDS and ch.id not in allowed_ids_by_feed[feeds[ch.feed].label]:
                    continue
                prev = by_id.get(ch.id.casefold())
                existing_is_extra = prev is not None and feeds[prev.feed].label in EXTRA_FEED_LABELS
                candidate_is_extra = feeds[ch.feed].label in EXTRA_FEED_LABELS
                if (prev is None
                        or (existing_is_extra and not candidate_is_extra)
                        or (existing_is_extra == candidate_is_extra
                            and winner_score(ch, feeds) > winner_score(prev, feeds))):
                    by_id[ch.id.casefold()] = ch
        if not by_id:
            raise RuntimeError("Downloaded feeds have no programmes. No output overwritten.")
        extra_channels = sum(feeds[ch.feed].label in EXTRA_FEED_LABELS for ch in by_id.values())
        logging.info("Supplementary guides: %s extra-only XMLTV channels", extra_channels)

        by_name: defaultdict[str, list[Channel]] = defaultdict(list)
        for ch in by_id.values():
            for n in ch.norm_names:
                by_name[n].append(ch)
        by_compact = compact_index(by_name)

        for feed_channels in per_feed:
            matches = [(ch.id, ch.names[:2], ch.programmes) for ch in feed_channels.values()
                       if any("COSMOTE SPORT" in clean_name(n) or "MAGENTA SPORT" in clean_name(n) for n in ch.names)]
            if matches:
                logging.info("SPORT-PROBE %s: %s examples=%s", feeds[next(iter(feed_channels.values())).feed].label, len(matches), matches[:28])
        aliases: dict[str, Channel] = {}
        display_aliases: dict[str, set[str]] = defaultdict(set)
        canonical_display_aliases: dict[str, set[str]] = defaultdict(set)
        candidate_ids: defaultdict[str, set[str]] = defaultdict(set)
        metrics = Counter()
        for row in input_rows:
            label = row.get("name", "").strip()
            user_id = row.get("epg_channel_id", "").strip()
            normalized = clean_name(label)
            if not normalized or len(normalized) < 3:
                continue
            market = market_of(label)
            direct = by_id.get(user_id.casefold()) if id_valid(user_id) else None
            if (direct and market == "GR" and greek_rebrand_names(normalized)
                    and feeds[direct.feed].market != "GR"):
                direct = None
            exact_candidates = by_name.get(normalized, [])
            compact_candidates = matching_candidates(normalized, by_name, by_compact)
            named = best(compact_candidates, market, feeds, user_id)
            if named and not exact_candidates:
                metrics["unambiguous_spacing_match"] += 1
            if not named and market == "GR" and normalized.startswith("MAGENTA SPORT"):
                # Greek COSMOTE Sport was rebranded as Magenta Sport.
                old_brand = normalized.replace("MAGENTA SPORT", "COSMOTE SPORT", 1)
                alternate = [old_brand]
                league_match = re.fullmatch(r"MAGENTA SPORT LEAGUE ([1-4])", normalized)
                if league_match:
                    alternate.extend(("COSMOTE SPORT " + league_match.group(1) + " LEAGUE",
                                      "COSMOTE SPORT " + league_match.group(1) + " L"))
                for old_name in alternate:
                    named = best([c for c in by_name.get(old_name, []) if feeds[c.feed].market == "GR"], market, feeds, user_id)
                    if named:
                        metrics["greek_magenta_rebrand_match"] += 1
                        break
            chosen = None
            if named:
                chosen = named
                metrics["name_match"] += 1
                if direct and direct.id.casefold() != chosen.id.casefold():
                    metrics["disputed_ids_not_overwritten"] += 1
            elif direct:
                # Existing provider IDs may be wrong: reject if channel naming disagrees strongly.
                source_keys = set(direct.norm_names)
                norm_tokens = set(normalized.split())
                src_tokens = set(" ".join(source_keys).split())
                overlap = len(norm_tokens & src_tokens) / max(len(norm_tokens), 1)
                is_sport_conflict = (
                    ("BUNDESLIGA" in normalized) != any("BUNDESLIGA" in n for n in source_keys)
                    and ("SKY" in normalized or any("SKY" in n for n in source_keys))
                )
                if not is_sport_conflict and overlap >= 0.5:
                    chosen = direct
                    metrics["id_match"] += 1
                else:
                    metrics["potential_bad_ids"] += 1
            if not chosen:
                metrics["unmatched"] += 1
                continue
            metrics["matched_input_rows"] += 1
            if feeds[chosen.feed].label in EXTRA_FEED_LABELS:
                metrics["extra_source_input_matches"] += 1
            if id_valid(user_id):
                # Do not hijack an ID that is already published for a different real channel.
                if user_id.casefold() in by_id:
                    if user_id.casefold() != chosen.id.casefold():
                        metrics["id_conflict_requires_playlist_fix"] += 1
                    elif (named is not None and direct is not None
                          and feeds[chosen.feed].market == market
                          and label not in chosen.names
                          and label not in canonical_display_aliases[chosen.id]):
                        # Preserve the canonical ID and original first display-name.
                        # Exact playlist titles offer additional name-based matching.
                        canonical_display_aliases[chosen.id].add(label)
                        metrics["canonical_playlist_display_names_added"] += 1
                    continue
                alias_id = user_id
                # If provider uses one ID for two different real channels, refuse both.
                candidate_ids[alias_id.casefold()].add(chosen.id.casefold())
                aliases.setdefault(alias_id, chosen)
                display_aliases[alias_id].add(label)
            else:
                # TiviMate may need one-time manual EPG assignment when original tvg-id is absent.
                alias_id = "personal." + hashlib.sha256(label.encode("utf-8")).hexdigest()[:16]
                aliases[alias_id] = chosen
                display_aliases[alias_id].add(label)
        for key, ids in candidate_ids.items():
            if len(ids) > 1:
                for aid in list(aliases):
                    if aid.casefold() == key:
                        del aliases[aid]
                        display_aliases.pop(aid, None)
                metrics["alias_conflicts_skipped"] += 1
        # Source IDs with different case may collide with a user alias; do not emit duplicates.
        aliases = {key: value for key, value in aliases.items() if key.casefold() not in by_id}
        target_aliases: defaultdict[tuple[int, str], list[str]] = defaultdict(list)
        for alias, ch in aliases.items():
            target_aliases[(ch.feed, ch.id)].append(alias)
        chosen_from_feed: defaultdict[int, set[str]] = defaultdict(set)
        for ch in by_id.values():
            chosen_from_feed[ch.feed].add(ch.id)

        # Only fallback for valid playlist EPG IDs that are not yet served
        # by a real, selected public programme source. Do not overwrite.
        with (ROOT / "data" / "inventory.csv").open("r", encoding="utf-8-sig", newline="") as inv:
            provider_ids = {row.get("epg_channel_id", "").strip() for row in csv.DictReader(inv)}
        covered_ids = set(by_id) | {aid.casefold() for aid in aliases}
        # Some live streams have guide IDs missing from old inventory snapshots.
        provider_ids.update(str(item.get("epg_channel_id") or "").strip()
                            for item in placeholder_rows)
        wanted_ids = {p.casefold() for p in provider_ids if id_valid(p) and p.casefold() not in covered_ids}
        provider_path = None
        provider_fallback_ids = {}
        provider_counts = Counter()
        if xtream_url and wanted_ids:
            try:
                provider_path = fetch_provider_xmltv(xtream_url, temp_dir)
                provider_canonical, provider_counts = provider_coverage(provider_path, wanted_ids)
                provider_fallback_ids = {
                    key: provider_canonical[key]
                    for key in wanted_ids
                    if provider_counts.get(key, 0) > 0 and key in provider_canonical
                }
                logging.info("Provider XMLTV fallback: %d eligible IDs with schedules", len(provider_fallback_ids))
            except Exception as error:
                # The provider XMLTV URL may not exist or be authorized.
                # Keep working public EPG sources and PPV placeholders.
                logging.warning("Provider XMLTV fallback skipped: %s", type(error).__name__)
                provider_path = None
                provider_fallback_ids = {}

        # First resolve genuine programme sources for EVERY placeholder
        # category. Preserve the already published XMLTV ID in all cases.
        public_real, provider_real = resolve_placeholder_programmes(
            placeholder_rows, by_id=by_id, by_name=by_name,
            by_compact=by_compact, feeds=feeds, clean_name=clean_name,
            id_valid=id_valid, provider_fallback_ids=provider_fallback_ids)
        real_placeholder_ids = set(public_real) | set(provider_real)
        # One XMLTV channel/programme set per published placeholder ID.
        placeholder_rows = list({
            str(item["id"]): item for item in placeholder_rows
            if item.get("id") and item.get("name")
        }.values())
        targets_for_public = defaultdict(set)
        targets_for_provider = defaultdict(set)
        for xmltv_id, source in public_real.items():
            targets_for_public[(source.feed, source.id)].add(xmltv_id)
        for xmltv_id, source_id in provider_real.items():
            targets_for_provider[source_id.casefold()].add(xmltv_id)
        logging.info("Real guide overrides: %d public, %d provider; %d name-only placeholders",
                     len(public_real), len(provider_real),
                     len(placeholder_rows) - len(real_placeholder_ids))

        temporary_out = OUT_DIR / "epg.xml.gz.tmp"
        total_programmes = 0
        with temporary_out.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", filename="epg.xml", compresslevel=6) as zipout:
                zipout.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
                zipout.write(b'<tv generator-info-name="DE-GR-TiviMate-Custom-EPG">\n')
                for ch in sorted(by_id.values(), key=lambda c: c.id.casefold()):
                    element = copy.deepcopy(ch.element)
                    # XMLTV requires display-name before icon/url. Insert after
                    # existing display names while retaining the original order.
                    names_end = sum(child.tag == "display-name" for child in element)
                    for offset, label in enumerate(sorted(canonical_display_aliases.get(ch.id, ()))):
                        node = ET.Element("display-name")
                        node.text = label
                        element.insert(names_end + offset, node)
                    zipout.write(ET.tostring(element, encoding="utf-8"))
                    zipout.write(b"\n")
                for alias, ch in sorted(aliases.items()):
                    elt = ET.Element("channel", {"id": alias})
                    for label in sorted(display_aliases.get(alias, [])):
                        ET.SubElement(elt, "display-name").text = label
                    for dn in ch.names[:2]:
                        ET.SubElement(elt, "display-name").text = dn
                    icon = ch.element.find("icon")
                    if icon is not None:
                        elt.append(copy.deepcopy(icon))
                    zipout.write(ET.tostring(elt, encoding="utf-8"))
                    zipout.write(b"\n")
                # Every provider fallback ID is the same ID already present
                # in the user's playlist, so clients can match it directly.
                for key, cid in sorted(provider_fallback_ids.items()):
                    ch = ET.Element("channel", {"id": cid})
                    ET.SubElement(ch, "display-name").text = cid
                    zipout.write(ET.tostring(ch, encoding="utf-8") + b"\n")
                # Retain the original XMLTV ID for manual TiviMate mapping.
                # Add real source display names if real programmes are served.
                for item in placeholder_rows:
                    cid = item["id"]
                    title = item["name"]
                    channel = ET.Element("channel", {"id": cid})
                    ET.SubElement(channel, "display-name").text = title
                    real_source = public_real.get(cid)
                    if real_source:
                        for source_name in real_source.names[:2]:
                            if source_name and source_name != title:
                                ET.SubElement(channel, "display-name").text = source_name
                        icon = real_source.element.find("icon")
                        if icon is not None:
                            channel.append(copy.deepcopy(icon))
                    zipout.write(ET.tostring(channel, encoding="utf-8") + b"\n")
                for index, feed in enumerate(feeds):
                    targets = chosen_from_feed[index]
                    if not targets:
                        continue
                    with open_xml(feed.path) as xml:
                        parser = ET.iterparse(xml, events=("start", "end"))
                        root = None
                        for event, elem in parser:
                            if event == "start" and root is None:
                                root = elem
                            elif event == "end" and elem.tag == "programme":
                                original = elem.get("channel", "")
                                if original in targets:
                                    zipout.write(ET.tostring(elem, encoding="utf-8"))
                                    zipout.write(b"\n")
                                    total_programmes += 1
                                    all_aliases = set(target_aliases.get((index, original), []))
                                    all_aliases.update(targets_for_public.get((index, original), ()))
                                    for alias in sorted(all_aliases):
                                        elem.set("channel", alias)
                                        zipout.write(ET.tostring(elem, encoding="utf-8"))
                                        zipout.write(b"\n")
                                        total_programmes += 1
                                    elem.set("channel", original)
                                if root is not None:
                                    root.clear()
                            elif event == "end" and elem.tag == "channel" and root is not None:
                                root.clear()
                if provider_path and provider_fallback_ids:
                    total_programmes += emit_provider_programmes(
                        provider_path, zipout, provider_fallback_ids,
                        aliases_by_source=targets_for_provider)
                from datetime import timedelta
                today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                for item in placeholder_rows:
                    cid = item["id"]
                    title = item["name"]
                    if cid in real_placeholder_ids:
                        continue
                    for offset in range(-1, 8):
                        start = today + timedelta(days=offset)
                        stop = start + timedelta(days=1)
                        programme = ET.Element("programme", {
                            "start": start.strftime("%Y%m%d%H%M%S +0000"),
                            "stop": stop.strftime("%Y%m%d%H%M%S +0000"),
                            "channel": cid,
                        })
                        ET.SubElement(programme, "title", {"lang": "de"}).text = title
                        zipout.write(ET.tostring(programme, encoding="utf-8") + b"\n")
                        total_programmes += 1
                zipout.write(b"</tv>\n")
        if total_programmes == 0:
            temporary_out.unlink(missing_ok=True)
            raise RuntimeError("No programmes written; old EPG kept.")
        # The full parse also catches a malformed output before replacing the old guide.
        with gzip.open(temporary_out, "rb") as f:
            ET.parse(f)
        temporary_out.replace(OUT_DIR / "epg.xml.gz")
        # Run only after a valid XMLTV was generated.
        active_friends = publish_personal_guides(OUT_DIR / "epg.xml.gz")
        result = {
            "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "feeds_ok": [feed.label for feed in feeds],
            "source_health": public_source_report(source_report),
            "source_health_summary": {
                "ok": sum(x["status"] == "ok" for x in source_report.values()),
                "failed": sum(x["status"] == "failed" for x in source_report.values()),
                "empty": sum(x["status"] == "empty" for x in source_report.values()),
            },
            "extra_only_source_channels": extra_channels,
            "base_channels": len(by_id),
            "active_personal_links": active_friends,
            "legacy_public_epg_url_disabled": True,
            "custom_alias_channels": len(aliases),
            "programme_entries": total_programmes,
            "input_channels": len(input_rows),
            "ppv_placeholder_channels": len(ppv_rows),
            "provider_fallback_channels": len(provider_fallback_ids),
            "provider_fallback_programmes": sum(provider_counts.get(k, 0) for k in provider_fallback_ids),
            "ppv_name_source": "live_xtream" if xtream_url else "static_export",
            "always_on_placeholder_channels": len(always_on_rows),
            "named_category_placeholder_channels": len(named_rows),
            "named_category_live_channels": len(live_named_rows),
            "always_on_static_fallback_channels": sum(
                row.get("source") != "live" for row in always_on_rows),
            "named_category_static_fallback_channels": sum(
                row.get("source") != "live" for row in named_rows),
            "real_programme_placeholder_channels": len(real_placeholder_ids),
            "real_programme_placeholder_public": len(public_real),
            "real_programme_placeholder_provider": len(provider_real),
            "remaining_name_only_placeholder_channels": len(placeholder_rows) - len(real_placeholder_ids),
            "match_statistics": dict(metrics),
            "limitations": "Missing or conflicting tvg-id in the provider playlist can require manual TiviMate assignment. Programmes depend on the upstream providers.",
        }
        with (ROOT / "data" / "manual_approvals.json").open("r", encoding="utf-8") as approval_file:
            manual_approvals = json.load(approval_file).get("approved", [])
        dashboard = make_dashboard(
            inventory_path=ROOT / "data" / "inventory.csv",
            output_path=OUT_DIR / "dashboard.json",
            ppv_rows=ppv_rows,
            always_on_rows=always_on_rows,
            named_rows=named_rows,
            by_id=by_id, by_name=by_name, by_compact=by_compact,
            aliases=aliases, feeds=feeds,
            clean_name=clean_name, market_of=market_of, best=best, id_valid=id_valid,
            live_ppv=bool(xtream_url),
            live_all_placeholders=bool(xtream_url), named_category_ids=named_ids,
            static_always_names={r["name"] for r in always_on_static if r.get("name")},
            provider_fallback_ids=provider_fallback_ids,
            real_placeholder_ids=real_placeholder_ids,
            manual_approvals=manual_approvals,
        )
        result["dashboard_channels"] = dashboard["total"]
        result["dashboard_counts"] = dashboard["counts"]
        # Informational only: distinguish exact tvg-id matches from name-based
        # matches; neither guarantees how any particular app will assign a guide.
        alignment = {}
        published_ids = ({ch.id for ch in by_id.values()} | set(aliases)
                         | set(provider_fallback_ids.values())
                         | {item["id"] for item in placeholder_rows})
        published_folded_ids = {value.casefold() for value in published_ids}
        for name, category, category_id, provider_id, xmltv_id, status, region, kind, claimable in dashboard["rows"]:
            if region not in ("DE", "GR") or kind != "Sender":
                continue
            summary = alignment.setdefault(region, Counter())
            summary["ordinary_channels"] += 1
            if status == "programm":
                summary["real_programme_channels"] += 1
                if (id_valid(provider_id) and provider_id.casefold() == xmltv_id.casefold()
                        and provider_id in published_ids):
                    summary["exact_published_id_matches"] += 1
                elif (id_valid(provider_id) and provider_id.casefold() == xmltv_id.casefold()
                      and provider_id.casefold() in published_folded_ids):
                    summary["case_insensitive_published_id_matches"] += 1
                elif not id_valid(provider_id):
                    summary["real_programme_no_provider_id"] += 1
                else:
                    summary["real_programme_different_id"] += 1
            elif status == "ohne":
                summary["without_programmes"] += 1
            elif status == "pruefen":
                summary["requires_review"] += 1
        result["de_gr_assignment_diagnostics"] = {k: dict(v) for k, v in alignment.items()}
        shutil.copyfile(ROOT / "dashboard" / "index.html", OUT_DIR / "index.html")
        shutil.copyfile(ROOT / "dashboard" / "m3u.html", OUT_DIR / "m3u.html")
        shutil.copyfile(ROOT / "dashboard" / "diagnostics.js", OUT_DIR / "diagnostics.js")
        if xtream_url:
            result["placeholder_stream_bindings"] = write_bindings(
                OUT_DIR / "placeholder-bindings.json",
                previous_bindings, live_named_rows, live_always_rows)
        else:
            # Do not publish an old stream-binding snapshot when live access
            # is disabled and no current stream verification was possible.
            (OUT_DIR / "placeholder-bindings.json").unlink(missing_ok=True)
        (OUT_DIR / "status.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logging.info("DONE: %s channels + %s aliases, %s programme entries, %s friend copies", len(by_id), len(aliases), total_programmes, active_friends)
        return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        print(json.dumps(build(), ensure_ascii=False, indent=2))
    except Exception:
        logging.exception("EPG update failed (existing published guide remains untouched)")
        sys.exit(1)