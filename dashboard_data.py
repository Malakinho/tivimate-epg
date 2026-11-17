"""Create a public, read-only dashboard data snapshot from the EPG build.

No credentials, stream URLs, subscription details or Xtream stream IDs are exported.
"""
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HEADER = re.compile(r"^#{2,}")
PPV = re.compile(r"\bPPV\b", re.I)


def make_dashboard(*, inventory_path: Path, output_path: Path, ppv_rows,
                   always_on_rows, by_id, by_name, aliases, feeds,
                   clean_name, market_of, best, id_valid, live_ppv: bool):
    """Data is informational; a source match does not guarantee player assignment."""
    with inventory_path.open(encoding="utf-8-sig", newline="") as handle:
        inventory = list(csv.DictReader(handle))

    headings = {}
    for record in inventory:
        name = (record.get("name") or "").strip()
        group = (record.get("category_id") or "").strip()
        if HEADER.match(name) and group not in headings:
            headings[group] = re.sub(r"^[#\s]+|[#\s]+$", "", name).strip()

    by_always = {(r.get("name") or "").strip(): r.get("id") or ""
                 for r in always_on_rows}
    by_ppv_static = {(r.get("name") or "").strip(): r.get("id") or ""
                     for r in ppv_rows}
    alias_keys = {a.casefold(): a for a in aliases}
    rows = []
    counters = Counter()

    def add(name, category, category_id, user_id, epg_id, status, region, kind):
        rows.append([name, category, category_id, user_id, epg_id, status, region, kind])
        counters[status] += 1

    for rec in inventory:
        name = (rec.get("name") or "").strip()
        category_id = (rec.get("category_id") or "").strip()
        if not name or HEADER.match(name):
            continue
        if live_ppv and PPV.search(name):
            # The current provider's PPV names supersede the old exported inventory.
            continue
        cat = headings.get(category_id, "Kategorie " + category_id)
        provider_id = (rec.get("epg_channel_id") or "").strip()
        is_ppv = bool(PPV.search(name))
        is_always = name in by_always
        market = market_of(name)
        if name.startswith("GR:") or any("\u0370" <= c <= "\u03ff" for c in cat):
            market = "GR"
        kind = "PPV" if is_ppv else "24/7" if is_always else "Sender"
        if is_ppv and name in by_ppv_static:
            add(name, cat, category_id, provider_id, by_ppv_static[name],
                "platzhalter", market, kind)
            continue
        if is_always:
            add(name, cat, category_id, provider_id, by_always[name],
                "platzhalter", market, kind)
            continue

        norm = clean_name(name)
        actual = None
        display_id = ""
        if id_valid(provider_id):
            direct = by_id.get(provider_id.casefold())
            if direct:
                # Guard against knowingly wrong provider IDs.
                source_keys = set(direct.norm_names)
                t = set(norm.split())
                src_tokens = set(" ".join(source_keys).split())
                overlap = len(t & src_tokens) / max(len(t), 1)
                wrong_sky = (("BUNDESLIGA" in norm) != any("BUNDESLIGA" in n for n in source_keys)
                             and ("SKY" in norm or any("SKY" in n for n in source_keys)))
                if overlap >= .5 and not wrong_sky:
                    actual = direct
                    display_id = provider_id

        if actual is None and norm:
            named = best(by_name.get(norm, []), market, feeds, provider_id)
            if named:
                actual = named
                own_alias = "personal." + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
                if own_alias.casefold() in alias_keys:
                    display_id = alias_keys[own_alias.casefold()]
                elif id_valid(provider_id) and provider_id.casefold() in alias_keys:
                    display_id = alias_keys[provider_id.casefold()]
                else:
                    # A real XMLTV source ID exists, but it may not match IPTV's ID.
                    display_id = named.id

        if actual is not None:
            add(name, cat, category_id, provider_id, display_id,
                "programm", market, kind)
        else:
            add(name, cat, category_id, provider_id, "", "ohne", market, kind)

    if live_ppv:
        # New provider names are from the current secure Xtream API request.
        # The opaque hashed EPG IDs are public; private stream IDs are not.
        for ppv in ppv_rows:
            name = (ppv.get("name") or "").strip()
            epg_id = (ppv.get("id") or "").strip()
            if name and epg_id:
                add(name, "PPV – Live vom Anbieter", "live-ppv", "", epg_id,
                    "platzhalter", "DE/International", "PPV")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "live_xtream" if live_ppv else "static_export",
        "counts": dict(counters),
        "total": len(rows),
        "fields": ["name", "category", "category_id", "provider_id",
                   "xmltv_id", "status", "region", "kind"],
        "rows": rows,
        "note": "Treffer beziehen sich auf unsere XMLTV-Daten, nicht auf automatische Zuordnung im IPTV-Player.",
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")),
                           encoding="utf-8")
    return report
