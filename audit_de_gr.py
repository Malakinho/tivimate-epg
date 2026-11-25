"""Read-only DE/GR candidate EPG audit. No schedules or player credentials published.

Run after build.py has produced site/dashboard.json. Results are Actions-only
and must be reviewed before a new source is admitted to the published guide.
"""
import gzip
import json
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from build import clean_name, excluded_us_row, feed_normalized_names, id_valid

CANDIDATES = (
    ("DE-EPGSHARE", "DE", "https://epgshare01.online/epgshare01/epg_ripper_DE1.xml.gz"),
    ("GR-EPGSHARE", "GR", "https://epgshare01.online/epgshare01/epg_ripper_GR1.xml.gz"),
)
MAX_BYTES = 32 * 1024 * 1024
GENERIC = {"", "NO INFORMATION", "NO PROGRAMME", "NO PROGRAM", "NO EPG",
           "NO DATA", "KEINE INFORMATION", "KEINE PROGRAMMINFORMATION",
           "PROGRAMM NICHT VERFUGBAR", "PROGRAMM NICHT VERFÜGBAR", "OFF AIR",
           "TBA", "TO BE ANNOUNCED", "DUMMY", "PLACEHOLDER"}


def timestamp(value):
    value = str(value or "").strip()
    for fmt in ("%Y%m%d%H%M%S %z", "%Y%m%d%H%M%S"):
        try:
            result = datetime.strptime(value, fmt)
            return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def fetch(url, path):
    with urlopen(Request(url, headers={"User-Agent": "DE-GR-EPG-Coverage-Audit/1.0"}),
                 timeout=25) as response, path.open("wb") as out:
        total = 0
        while chunk := response.read(256 * 1024):
            total += len(chunk)
            if total > MAX_BYTES:
                raise ValueError("Candidate source exceeds audit size limit")
            out.write(chunk)


def inspect(path, market, now):
    """Keep only channel metadata and counts of non-placeholder near-future programmes."""
    channels, counts = {}, Counter()
    with gzip.open(path, "rb") as source:
        root = None
        for event, node in ET.iterparse(source, events=("start", "end")):
            if root is None:
                root = node
                if root.tag != "tv":
                    raise ValueError("Not an XMLTV file")
            if event != "end":
                continue
            if node.tag == "channel":
                cid = str(node.get("id") or "").strip()
                names = [x.text or "" for x in node.findall("display-name")]
                if cid and names and not cid.casefold().endswith((".us", ".usa")):
                    channels[cid] = feed_normalized_names(names, market)
                root.clear()
            elif node.tag == "programme":
                cid = node.get("channel", "")
                start, stop = timestamp(node.get("start")), timestamp(node.get("stop"))
                title = clean_name(node.findtext("title") or "")
                if (cid in channels and start and stop and start < stop
                        and stop > now and start < now + timedelta(hours=48)
                        and title not in GENERIC and title not in channels[cid]):
                    counts[cid] += 1
                root.clear()
    return {cid: names for cid, names in channels.items() if counts[cid]}


def compare(rows, market, channels):
    """Never guess between different source IDs or conflicting provider IDs."""
    by_name = defaultdict(set)
    for cid, names in channels.items():
        for name in names:
            by_name[name].add(cid)
    by_folded_id = defaultdict(set)
    for cid in channels:
        by_folded_id[cid.casefold()].add(cid)
    stats = Counter()
    findings = []
    for row in rows:
        name, category, category_id, provider_id, xmltv_id, status, region, kind, *_ = row
        if (region != market or kind != "Sender"
                or excluded_us_row({"name": name, "category": category, "category_id": category_id, "region": region})):
            continue
        if status not in ("ohne", "pruefen", "programm"):
            continue
        if status == "programm" and (not id_valid(provider_id) or provider_id == xmltv_id):
            continue
        stats["checked_rows"] += 1
        key = clean_name(name)
        if not key or len(key) < 3:
            continue
        candidates = set(by_name.get(key, ()))
        if not candidates:
            continue
        direct = by_folded_id.get(provider_id.casefold(), set()) if id_valid(provider_id) else set()
        compatible = direct & candidates
        if direct and not compatible:
            stats["conflicting_id"] += 1
            continue
        if len(compatible) == 1:
            chosen = next(iter(compatible))
            method = "exact_id_and_name" if chosen == provider_id else "casefold_id_and_name"
        elif len(candidates) == 1:
            chosen = next(iter(candidates))
            method = "unique_name"
        else:
            stats["ambiguous_name"] += 1
            continue
        if status == "programm" and method == "unique_name":
            continue  # already covered; no proven ID improvement
        if status == "ohne":
            bucket = "new_programme_candidates"
        elif status == "pruefen":
            bucket = "review_candidates"
        else:
            bucket = "existing_id_alignment_candidates"
        stats[bucket] += 1
        stats[bucket + "_" + method] += 1
        findings.append({"name": name, "category_id": category_id, "provider_id": provider_id,
                         "candidate_id": chosen, "method": method, "bucket": bucket})
    return dict(stats), findings


def main():
    dashboard = Path(sys.argv[1] if len(sys.argv) > 1 else "site/dashboard.json")
    output = Path(sys.argv[2] if len(sys.argv) > 2 else "audit-results/coverage.json")
    rows = json.loads(dashboard.read_text(encoding="utf-8"))["rows"]
    now = datetime.now(timezone.utc)
    report = {"checked_utc": now.isoformat(), "mode": "read-only, not published",
              "dashboard_rows": len(rows), "sources": {}, "unique_new_candidates": 0}
    unique_new = set()
    with tempfile.TemporaryDirectory() as folder:
        for label, market, url in CANDIDATES:
            try:
                path = Path(folder) / (label + ".xml.gz")
                fetch(url, path)
                channels = inspect(path, market, now)
                stats, matches = compare(rows, market, channels)
                report["sources"][label] = {"status": "ok" if channels else "empty",
                    "fresh_channels": len(channels), "stats": stats, "findings": matches}
                for match in matches:
                    if match["bucket"] == "new_programme_candidates":
                        unique_new.add((market, match["category_id"], match["name"], match["provider_id"]))
                print(label, json.dumps({"fresh_channels": len(channels), **stats}, ensure_ascii=False))
            except Exception as error:
                report["sources"][label] = {"status": "failed", "error_type": type(error).__name__}
                print(label, "failed:", type(error).__name__)
    report["unique_new_candidates"] = len(unique_new)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Unique new programme candidates:", len(unique_new))
    print("Candidates are NOT automatically published; review rights, channel identity and schedule quality first.")


if __name__ == "__main__":
    main()
