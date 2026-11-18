"""Keep placeholder IDs stable when Xtream stream names change.

A public snapshot contains only a one-way stream fingerprint and the already
public XMLTV ID, never the provider's stream ID, access credentials or URLs.
The previous public dashboard is used once to migrate existing manual EPG
assignments from named.static.* and always.* without changing their IDs.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.request import Request, urlopen

BASE = "https://malakinho.github.io/tivimate-epg"
TYPES = {"named": ("named.live.", "named.static."),
         "always": ("always.",)}


def _fetch_json(filename: str, limit: int):
    req = Request(BASE + "/" + filename, headers={"User-Agent": "EPG-Placeholder-Sync/1.0",
                                                  "Cache-Control": "no-cache"})
    try:
        with urlopen(req, timeout=15) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Public placeholder data too large")
        return json.loads(data)
    except Exception as error:
        logging.info("Previous placeholder state unavailable (%s): %s",
                     filename, type(error).__name__)
        return None


def previous_state() -> tuple[dict, dict]:
    """Return (fingerprint -> binding, (kind, category, name) -> XMLTV ID)."""
    existing = {}
    payload = _fetch_json("placeholder-bindings.json", 2 * 1024 * 1024)
    if isinstance(payload, dict) and isinstance(payload.get("bindings"), list):
        for item in payload["bindings"]:
            if not isinstance(item, dict):
                continue
            key = item.get("fingerprint")
            kind = item.get("kind")
            xmltv_id = item.get("id")
            if (isinstance(key, str) and re.fullmatch("[0-9a-f]{22}", key)
                    and kind in TYPES and isinstance(xmltv_id, str)
                    and xmltv_id.startswith(TYPES[kind]) and len(xmltv_id) < 100):
                existing[key] = {"kind": kind, "id": xmltv_id}

    legacy = {}
    dashboard = _fetch_json("dashboard.json", 16 * 1024 * 1024)
    if isinstance(dashboard, dict) and isinstance(dashboard.get("rows"), list):
        columns = dashboard.get("fields", [])
        required = ("name", "category_id", "xmltv_id", "status")
        if isinstance(columns, list) and all(name in columns for name in required):
            indexes = [columns.index(name) for name in required]
            for row in dashboard["rows"]:
                if not isinstance(row, list) or max(indexes) >= len(row):
                    continue
                name, category, xmltv_id, status = (row[i] for i in indexes)
                if not all(isinstance(x, str) for x in (name, category, xmltv_id, status)):
                    continue
                if status != "platzhalter":
                    continue
                kind = "named" if xmltv_id.startswith(("named.static.", "named.live.")) \
                       else "always" if xmltv_id.startswith("always.") else ""
                if kind:
                    legacy[(kind, category, name)] = xmltv_id
    return existing, legacy


def write_bindings(path: Path, old: dict, named_rows: list, always_rows: list):
    """Publish existing and new opaque stream-to-XMLTV-ID mappings."""
    merged = dict(old)
    for kind, rows in (("named", named_rows), ("always", always_rows)):
        for row in rows:
            key = row.get("fingerprint")
            epg_id = row.get("id")
            if (isinstance(key, str) and re.fullmatch("[0-9a-f]{22}", key)
                    and isinstance(epg_id, str) and epg_id.startswith(TYPES[kind])):
                merged[key] = {"kind": kind, "id": epg_id}
    entries = [{"fingerprint": key, **item} for key, item in sorted(merged.items())
               if isinstance(item, dict) and item.get("kind") in TYPES
               and isinstance(item.get("id"), str)]
    path.write_text(json.dumps({"version": 1, "bindings": entries},
                               ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return len(entries)
