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
from dashboard_data import make_dashboard

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "channels.csv"
PPV_DATA = ROOT / "data" / "ppv.csv"
ALWAYS_ON_DATA = ROOT / "data" / "always_on.csv"
OUT_DIR = ROOT / "site"
SOURCES = [
    ("DE-EPG", "DE", "https://iptv-epg.org/files/epg-de.xml"),
    ("GR-EPG", "GR", "https://iptv-epg.org/files/epg-gr.xml"),
    ("GR-GREEKTV", "GR", "https://ext.greektv.app/epg/epg.xml.gz"),
    ("GR-COSMOTE", "GR", "https://iptv-org.github.io/epg/guides/el/cosmote.gr.xml"),
    ("GR-NOVASPORTS", "GR", "https://iptv-org.github.io/epg/guides/el/novasports.gr.xml"),
    ("DE-MASTER", "DE", "https://kaimandura.github.io/epg-de/DE-MASTER.xml.gz"),
]
# These are design hints only. Do not infer the actual licence for TV metadata.
MAX_DOWNLOAD = 180 * 1024 * 1024
BAD_IDS = {"", "TS", "N/A", "NONE", "NULL", "0", "-"}
PREFIX = re.compile(r"^(?:DE GO|DE|GR|PRIME|JOYN|WOW|SKYGO)\s*[:\-]\s*", re.I)
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
    value = re.sub(r"\bNOVACINEMA\s+([1-4])\b", r"NOVACINEMA\1", value)
    value = re.sub(r"\bANT1\s+COMMEDY\b", "ANT1 COMEDY", value)
    value = re.sub(r"\bERT\s+NEWS\b", "ERTNEWS", value)
    return value


def market_of(name: str) -> str:
    prefix = name.split(":", 1)[0].strip().upper()
    if prefix == "GR":
        return "GR"
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
                                                   frozenset(filter(None, (clean_name(n) for n in names))))
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
    if not candidates:
        return None
    result = sorted(candidates, key=lambda c: (
        feeds[c.feed].market == market,
        c.id.lower() == provider_id.lower() if provider_id else False,
        c.id.lower().endswith("." + market.lower()),
        winner_score(c, feeds),
    ), reverse=True)
    return result[0]


def build() -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with DATA.open("r", encoding="utf-8-sig", newline="") as csvfile:
        input_rows = list(csv.DictReader(csvfile))
    xtream_url = os.environ.get("XTREAM_M3U_URL", "").strip()
    if xtream_url:
        # Live channel names always override the static PPV export.
        # On API failure the build stops, leaving the last published EPG online.
        ppv_rows = current_ppv_rows(xtream_url)
        logging.info("Live PPV names retrieved: %s channels", len(ppv_rows))
    else:
        # Until the user enters the GitHub secret, keep the working static guide.
        with PPV_DATA.open("r", encoding="utf-8-sig", newline="") as csvfile:
            ppv_rows = list(csv.DictReader(csvfile))
        logging.info("Using static PPV fallback: configure XTREAM_M3U_URL secret for live updates")
    # PPV placeholders remain unchanged. 24/7 entries are only added
    # where the provider supplied no EPG ID.
    with ALWAYS_ON_DATA.open("r", encoding="utf-8-sig", newline="") as csvfile:
        always_on_rows = list(csv.DictReader(csvfile))
    placeholder_rows = ppv_rows + always_on_rows
    with tempfile.TemporaryDirectory() as temp:
        temp_dir = Path(temp)
        feeds: list[Feed] = []
        for name, market, url in SOURCES:
            file_name = f"{name}.xml.gz" if url.endswith(".gz") else f"{name}.xml"
            destination = temp_dir / file_name
            try:
                download(url, destination)
                feeds.append(Feed(name, market, url, destination))
            except Exception as error:
                logging.warning("Skipping source %s: %s", name, error)
        if not feeds:
            raise RuntimeError("No live EPG source could be downloaded. No output overwritten.")

        per_feed = [inspect_feed(feed, i) for i, feed in enumerate(feeds)]
        by_id: dict[str, Channel] = {}
        for feed_channels in per_feed:
            for ch in feed_channels.values():
                if ch.programmes <= 0:
                    continue
                prev = by_id.get(ch.id.casefold())
                if prev is None or winner_score(ch, feeds) > winner_score(prev, feeds):
                    by_id[ch.id.casefold()] = ch
        if not by_id:
            raise RuntimeError("Downloaded feeds have no programmes. No output overwritten.")

        by_name: defaultdict[str, list[Channel]] = defaultdict(list)
        for ch in by_id.values():
            for n in ch.norm_names:
                by_name[n].append(ch)

        for feed_channels in per_feed:
            matches = [(ch.id, ch.names[:2], ch.programmes) for ch in feed_channels.values()
                       if any("COSMOTE SPORT" in clean_name(n) or "MAGENTA SPORT" in clean_name(n) for n in ch.names)]
            if matches:
                logging.info("SPORT-PROBE %s: %s examples=%s", feeds[next(iter(feed_channels.values())).feed].label, len(matches), matches[:28])
        aliases: dict[str, Channel] = {}
        display_aliases: dict[str, set[str]] = defaultdict(set)
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
            named = best(by_name.get(normalized, []), market, feeds, user_id)
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
            if id_valid(user_id):
                # Do not hijack an ID that is already published for a different real channel.
                if user_id.casefold() in by_id:
                    if user_id.casefold() != chosen.id.casefold():
                        metrics["id_conflict_requires_playlist_fix"] += 1
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

        temporary_out = OUT_DIR / "epg.xml.gz.tmp"
        total_programmes = 0
        with temporary_out.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", filename="epg.xml", compresslevel=6) as zipout:
                zipout.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
                zipout.write(b'<tv generator-info-name="DE-GR-TiviMate-Custom-EPG">\n')
                for ch in sorted(by_id.values(), key=lambda c: c.id.casefold()):
                    zipout.write(ET.tostring(ch.element, encoding="utf-8"))
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
                # PPV placeholder channels: full provider name as EPG title.
                # Never imply that these are real broadcast event times.
                for item in placeholder_rows:
                    cid = item["id"]
                    title = item["name"]
                    if not cid or not title:
                        continue
                    channel = ET.Element("channel", {"id": cid})
                    ET.SubElement(channel, "display-name").text = title
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
                                    for alias in target_aliases.get((index, original), []):
                                        elem.set("channel", alias)
                                        zipout.write(ET.tostring(elem, encoding="utf-8"))
                                        zipout.write(b"\n")
                                        total_programmes += 1
                                    elem.set("channel", original)
                                if root is not None:
                                    root.clear()
                            elif event == "end" and elem.tag == "channel" and root is not None:
                                root.clear()
                from datetime import timedelta
                today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                for item in placeholder_rows:
                    cid = item["id"]
                    title = item["name"]
                    if not cid or not title:
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
        result = {
            "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "feeds_ok": [feed.label for feed in feeds],
            "base_channels": len(by_id),
            "custom_alias_channels": len(aliases),
            "programme_entries": total_programmes,
            "input_channels": len(input_rows),
            "ppv_placeholder_channels": len(ppv_rows),
            "ppv_name_source": "live_xtream" if xtream_url else "static_export",
            "always_on_placeholder_channels": len(always_on_rows),
            "match_statistics": dict(metrics),
            "limitations": "Missing or conflicting tvg-id in the provider playlist can require manual TiviMate assignment. Programmes depend on the upstream providers.",
        }
        dashboard = make_dashboard(
            inventory_path=ROOT / "data" / "inventory.csv",
            output_path=OUT_DIR / "dashboard.json",
            ppv_rows=ppv_rows,
            always_on_rows=always_on_rows,
            by_id=by_id, by_name=by_name, aliases=aliases, feeds=feeds,
            clean_name=clean_name, market_of=market_of, best=best, id_valid=id_valid,
            live_ppv=bool(xtream_url),
        )
        result["dashboard_channels"] = dashboard["total"]
        result["dashboard_counts"] = dashboard["counts"]
        shutil.copyfile(ROOT / "dashboard" / "index.html", OUT_DIR / "index.html")
        (OUT_DIR / "status.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logging.info("DONE: %s channels + %s aliases, %s programme entries, %s bytes", len(by_id), len(aliases), total_programmes, (OUT_DIR / 'epg.xml.gz').stat().st_size)
        return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        print(json.dumps(build(), ensure_ascii=False, indent=2))
    except Exception:
        logging.exception("EPG update failed (existing published guide remains untouched)")
        sys.exit(1)