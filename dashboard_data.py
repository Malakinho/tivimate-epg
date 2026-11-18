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

from name_matching import matching_candidates

HEADER = re.compile(r"^#{2,}")
PPV = re.compile(r"(?:\bPPV\b|\bPPV(?=\d)|ᴾᴾⱽ)", re.I)
ALWAYS_ON = re.compile(r"(?<!\d)24\s*[/\-]\s*7(?!\d)", re.I)


def make_dashboard(*, inventory_path: Path, output_path: Path, ppv_rows,
                   always_on_rows, named_rows, by_id, by_name, aliases, feeds,
                   by_compact=None,
                   clean_name, market_of, best, id_valid, live_ppv: bool,
                   provider_fallback_ids=None, manual_approvals=None,
                   live_all_placeholders=False, named_category_ids=None,
                   static_always_names=None, real_placeholder_ids=None):
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
    by_named_static = {((r.get("name") or "").strip(), str(r.get("category_id") or "").strip()): r.get("id") or ""
                       for r in named_rows if r.get("source") == "static"}
    live_named_ids = {str(r.get("category_id") or "").strip()
                      for r in named_rows if r.get("source") == "live"}
    live_always_ids = {str(r.get("category_id") or "").strip()
                       for r in always_on_rows if r.get("source") == "live"}
    static_always_names = static_always_names or set()
    real_placeholder_ids = real_placeholder_ids or set()
    def placeholder_status(xmltv_id):
        return "programm" if xmltv_id in real_placeholder_ids else "platzhalter"
    alias_keys = {a.casefold(): a for a in aliases}
    # Whitelist is limited to checked discrepancies in the
    # provider-ID-present / XMLTV-ID-empty cohort. Other channels are untouched.
    verified_alias_patterns = {
        "skycinemapremieren.de": (r"\bSKY CINEMA PREMIEREN\b", "SKY CINEMA PREMIERE"),
        "ard.de": (r"\bDAS ERSTE\b", "DAS ERSTE"),
        "novasportsextra.gr": (r"\b(?:NOVASPORTS EXTRA 1|NOVA EXTRA SPORTS 1)\b", "NOVASPORTS EXTRA 1"),
        # Inspected against published XMLTV source display-names:
        # GR: ALPH HD == ALPHA.gr (ALPHA), DE: EURO SPORT 1 RAW
        # == Eurosport1.de (Eurosport 1). Only these exact spellings pass.
        "alpha.gr": (r"^ALPH$", "ALPHA"),
        "eurosport1.de": (r"^EURO SPORT 1$", "EUROSPORT 1"),
    }
    rows = []
    counters = Counter()
    provider_fallback_ids = provider_fallback_ids or {}
    manual_approvals = manual_approvals or []
    approved = {(item["name"], str(item["category_id"]), item["provider_id"].casefold())
                for item in manual_approvals}
    # These IDs have actual programmes in the merged XMLTV output.
    available_ids = set(by_id) | {x.casefold() for x in aliases} | set(provider_fallback_ids)

    def add(name, category, category_id, user_id, epg_id, status, region, kind):
        claimable = (status in ("ohne", "pruefen") and id_valid(user_id)
                     and user_id.casefold() in available_ids and bool(category_id))
        rows.append([name, category, category_id, user_id, epg_id, status, region, kind, claimable])
        counters[status] += 1

    for rec in inventory:
        name = (rec.get("name") or "").strip()
        category_id = (rec.get("category_id") or "").strip()
        if not name or HEADER.match(name):
            continue
        cat = headings.get(category_id, "Kategorie " + category_id)
        if category_id in live_named_ids:
            # Live Xtream channel names replace the static category snapshot.
            continue
        if live_ppv and (PPV.search(name) or PPV.search(cat)):
            # The live Xtream API supersedes all static PPV-category records,
            # including US event channels without PPV in their visible names.
            continue
        provider_id = (rec.get("epg_channel_id") or "").strip()
        if (live_all_placeholders
                and category_id in live_always_ids
                and (name in static_always_names
                     or ALWAYS_ON.search(name) or ALWAYS_ON.search(cat))):
            # Real-time 24/7 names supersede stale static inventory names.
            # A channel with a genuine provider EPG ID remains inspectable.
            continue
        # Classify by both the sender name and its provider category.
        # Some event/24/7 streams have generic titles without those words.
        is_ppv = bool(PPV.search(name) or PPV.search(cat))
        is_always = bool(name in by_always or ALWAYS_ON.search(name) or ALWAYS_ON.search(cat))
        is_named = (name, category_id) in by_named_static
        market = market_of(name)
        if name.startswith("GR:") or any("\u0370" <= c <= "\u03ff" for c in cat):
            market = "GR"
        kind = "PPV" if is_ppv else "Sendername" if is_named else "24/7" if is_always else "Sender"
        if is_ppv and name in by_ppv_static:
            cid = by_ppv_static[name]
            add(name, cat, category_id, provider_id, cid,
                placeholder_status(cid), market, kind)
            continue
        if is_always and name in by_always:
            cid = by_always[name]
            add(name, cat, category_id, provider_id, cid,
                placeholder_status(cid), market, kind)
            continue
        if is_named:
            cid = by_named_static[(name, category_id)]
            add(name, cat, category_id, provider_id, cid,
                placeholder_status(cid), market, "Sendername")
            continue

        norm = clean_name(name)
        # A prior decision confirmed by the GitHub repository owner is applied
        # only when the same channel/category/provider ID still exists and
        # that ID currently has real programme data.
        if ((name, category_id, provider_id.casefold()) in approved
                and id_valid(provider_id) and provider_id.casefold() in available_ids):
            add(name, cat, category_id, provider_id, provider_id, "programm", market, kind)
            continue
        actual = None
        display_id = ""
        uncertain_id = ""
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
                else:
                    uncertain_id = provider_id

        if actual is None and norm:
            named = best(matching_candidates(norm, by_name, by_compact or {}),
                         market, feeds, provider_id)
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

        # A narrow correction for verified variants whose provider ID has
        # real XMLTV programmes, but the strict dashboard overlap rejected.
        # This does not modify channels without a provider ID.
        if actual is None and id_valid(provider_id):
            rule = verified_alias_patterns.get(provider_id.casefold())
            direct_channel = by_id.get(provider_id.casefold())
            if (rule and direct_channel and re.search(rule[0], norm)
                    and any(rule[1] in n for n in direct_channel.norm_names)):
                actual = direct_channel
                display_id = provider_id
        if actual is not None:
            add(name, cat, category_id, provider_id, display_id,
                "programm", market, kind)
        elif id_valid(provider_id) and provider_id.casefold() in provider_fallback_ids:
            # Real programme entries from the authenticated provider XMLTV
            # are now included under this playlist EPG ID.
            add(name, cat, category_id, provider_id,
                provider_fallback_ids[provider_id.casefold()],
                "programm", market, kind)
        elif id_valid(provider_id) and provider_id.casefold() in alias_keys:
            # This provider EPG-ID already has programmes in our published
            # XMLTV, but the dashboard was only checking source IDs.
            alias_id = alias_keys[provider_id.casefold()]
            linked = aliases[alias_id]
            rule = verified_alias_patterns.get(provider_id.casefold())
            if (rule and re.search(rule[0], norm)
                    and any(rule[1] in n for n in linked.norm_names)):
                add(name, cat, category_id, provider_id, alias_id,
                    "programm", market, kind)
            else:
                # XMLTV exists under the ID, but could be another channel
                # (such as 01TV.fr or an incorrectly mapped UK stream).
                # Leave XMLTV ID blank until verified.
                add(name, cat, category_id, provider_id, "",
                    "pruefen", market, kind)
        elif uncertain_id:
            # The ID has a published schedule, but the source title is at odds
            # with the channel name. Flag it instead of calling it missing.
            add(name, cat, category_id, provider_id, uncertain_id,
                "pruefen", market, kind)
        else:
            add(name, cat, category_id, provider_id, "", "ohne", market, kind)

    if live_ppv:
        # New provider names are from the current secure Xtream API request.
        # The opaque hashed EPG IDs are public; private stream IDs are not.
        for ppv in ppv_rows:
            name = (ppv.get("name") or "").strip()
            epg_id = (ppv.get("id") or "").strip()
            if name and epg_id:
                add(name, (ppv.get("category") or "PPV – Live vom Anbieter"),
                    "live-ppv", ppv.get("epg_channel_id") or "", epg_id,
                    placeholder_status(epg_id),
                    (ppv.get("region") or "DE/International"), "PPV")

    # Append live 24/7 placeholders. Their XMLTV IDs remain stable even
    # if Xtream later changes the stream name.
    for entry in always_on_rows:
        if entry.get("source") != "live":
            continue
        name = (entry.get("name") or "").strip()
        epg_id = (entry.get("id") or "").strip()
        if name and epg_id:
            add(name, entry.get("category") or "24/7",
                str(entry.get("category_id") or ""), entry.get("epg_channel_id") or "",
                epg_id, placeholder_status(epg_id),
                entry.get("region") or market_of(name), "24/7")

    # Append current named-category streams only: static records are superseded.
    for entry in named_rows:
        if entry.get("source") != "live":
            continue
        name = (entry.get("name") or "").strip()
        epg_id = (entry.get("id") or "").strip()
        if name and epg_id:
            add(name, entry.get("category") or "Sendername",
                str(entry.get("category_id") or ""), entry.get("epg_channel_id") or "",
                epg_id, placeholder_status(epg_id),
                entry.get("region") or market_of(name), "Sendername")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "live_xtream" if live_ppv else "static_export",
        "counts": dict(counters),
        "total": len(rows),
        "fields": ["name", "category", "category_id", "provider_id",
                   "xmltv_id", "status", "region", "kind", "claimable"],
        "rows": rows,
        "note": "Treffer beziehen sich auf unsere XMLTV-Daten, nicht auf automatische Zuordnung im IPTV-Player.",
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")),
                           encoding="utf-8")
    return report
