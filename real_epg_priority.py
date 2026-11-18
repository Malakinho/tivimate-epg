"""Prefer real XMLTV programmes over channel-name placeholders when safe.

Do not guess by similar names, channel numbers, or a show's title.
Only use a same-market, unambiguous TV guide channel, or an actual provider
EPG ID with programme coverage. The caller keeps the original XMLTV ID.
"""
from __future__ import annotations

import re
from name_matching import matching_candidates

COUNTRIES = {"DE": "DE", "GR": "GR", "FR": "FR", "IT": "IT",
             "PL": "PL", "HR": "HR", "NL": "NL", "US": "US",
             "UK": "GB"}
GERMAN_PLATFORMS = {"PRIME", "JOYN", "WOW", "SKYGO", "DE GO"}


def placeholder_market(item: dict) -> str | None:
    """Never assume unknown international IPTV brands are German."""
    name = str(item.get("name") or "").strip()
    prefix = name.partition(":")[0].upper().strip()
    if prefix in COUNTRIES:
        return COUNTRIES[prefix]
    if prefix in GERMAN_PLATFORMS:
        return "DE"
    if re.match(r"^US\s*\(", name, re.I):
        return "US"
    region = str(item.get("region") or "").strip().upper()
    if region in COUNTRIES:
        return COUNTRIES[region]
    if region == "GB":
        return "GB"
    return None


def genuine_source(item, *, by_id, by_name, by_compact, feeds,
                   clean_name, id_valid):
    """Find one provably relevant public guide channel with real programmes."""
    normalized = clean_name(str(item.get("name") or ""))
    market = placeholder_market(item)
    if not market or len(normalized) < 3:
        return None
    provider_id = str(item.get("epg_channel_id") or "").strip()
    direct = by_id.get(provider_id.casefold()) if id_valid(provider_id) else None

    def same_station(channel):
        if (channel.feed < 0 or channel.feed >= len(feeds)
                or feeds[channel.feed].market != market
                or channel.programmes <= 0):
            return False
        return any(
            key == normalized
            or (key.replace(" ", "") == normalized.replace(" ", "")
                and re.findall(r"\d+", key) == re.findall(r"\d+", normalized))
            for key in channel.norm_names
        )

    if direct and same_station(direct):
        return direct
    candidates = matching_candidates(normalized, by_name, by_compact)
    # If two distinct station IDs could be correct, do not choose one.
    safe = {ch.id.casefold(): ch for ch in candidates if same_station(ch)}
    return next(iter(safe.values())) if len(safe) == 1 else None


def resolve_placeholder_programmes(placeholders, *, by_id, by_name,
                                   by_compact, feeds, clean_name, id_valid,
                                   provider_fallback_ids=None):
    """Return (published ID -> public source Channel, ID -> provider source ID)."""
    public, provider = {}, {}
    fallback = provider_fallback_ids or {}
    for item in placeholders:
        channel_id = str(item.get("id") or "").strip()
        if not channel_id or channel_id in public or channel_id in provider:
            continue
        source = genuine_source(item, by_id=by_id, by_name=by_name,
                                by_compact=by_compact, feeds=feeds,
                                clean_name=clean_name, id_valid=id_valid)
        if source:
            public[channel_id] = source
            continue
        provider_id = str(item.get("epg_channel_id") or "").strip()
        if id_valid(provider_id) and provider_id.casefold() in fallback:
            provider[channel_id] = fallback[provider_id.casefold()]
    return public, provider
