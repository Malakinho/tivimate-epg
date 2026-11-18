"""Match requested IPTV category headings for name-as-programme placeholders."""
from __future__ import annotations
import hashlib
import unicodedata

CATEGORY_NAMES = (
    "NETFLIX ON AIR", "ONEPLAY CINEMA", "PARAMOUNT+ ORIGINAL",
    "HULU ORIGINALS", "DISNEY+ ORIGINAL", "PEACOCK SERIE ORIGINAL",
    "HBO ORIGINAL", "CINEMANIA HOLLYWOOD", "CINEMANIA TV SHOWS",
    "NFL LIVE EVENTS", "NFL REPLAY", "NHL TEAMS", "NHL LEAGUE REPLAY",
)

def category_is_named(label: str) -> bool:
    normalized = unicodedata.normalize("NFKC", label).upper()
    return any(name in normalized for name in CATEGORY_NAMES)

def named_category_ids(inventory: list[dict]) -> set[str]:
    return {str(row.get("category_id") or "").strip()
            for row in inventory
            if (row.get("name") or "").strip().startswith("##")
            and category_is_named(row.get("name") or "")
            and str(row.get("category_id") or "").strip()}

def static_named_rows(inventory: list[dict], category_ids: set[str]) -> list[dict]:
    headings = {}
    for row in inventory:
        name = (row.get("name") or "").strip()
        cid = str(row.get("category_id") or "").strip()
        if name.startswith("##") and cid in category_ids and cid not in headings:
            headings[cid] = name.strip("# ").strip()
    rows, seen = [], set()
    for row in inventory:
        name = (row.get("name") or "").strip()
        cid = str(row.get("category_id") or "").strip()
        if not name or name.startswith("##") or cid not in category_ids or (cid, name) in seen:
            continue
        seen.add((cid, name))
        digest = hashlib.sha256((cid + "\0" + name).encode("utf-8")).hexdigest()[:22]
        rows.append({"id": "named.static." + digest, "name": name,
                     "category_id": cid, "category": headings.get(cid, "Sendername"),
                     "source": "static"})
    return rows
