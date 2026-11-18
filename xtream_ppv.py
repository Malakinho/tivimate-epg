"""Read the current PPV sender names from the user's Xtream live channel API.

Credentials are read only from a GitHub Actions secret (XTREAM_M3U_URL).
Neither the credentials nor the provider URLs are written to the EPG.
"""
import hashlib
import json
import re

from named_categories import category_is_named

PPV_LABEL = re.compile(r"(?:\bPPV\b|\bPPV(?=\d)|ᴾᴾⱽ)", re.I)
ALWAYS_LABEL = re.compile(r"(?<!\d)24\s*[/\-]\s*7(?!\d)|\b24\s*H\b", re.I)
BAD_EPG_IDS = {"", "TS", "N/A", "NONE", "NULL", "0", "-"}
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


def current_ppv_rows(secret_url: str, known_ppv_category_ids=None, named_category_ids=None,
                     always_category_ids=None, always_names=None, legacy_ids=None,
                     previous_bindings=None):
    parsed = urlsplit(secret_url.strip())
    query = parse_qs(parsed.query, keep_blank_values=True)
    username = query.get("username", [""])[0]
    password = query.get("password", [""])[0]
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not username or not password:
        raise RuntimeError("XTREAM_M3U_URL must be an M3U URL including username and password.")
    base_path = parsed.path.rsplit("/", 1)[0]
    api_path = f"{base_path}/player_api.php"
    api_url = urlunsplit((parsed.scheme, parsed.netloc, api_path,
                         urlencode({"username": username,
                                    "password": password,
                                    "action": "get_live_streams"}), ""))
    try:
        with urlopen(Request(api_url, headers={"User-Agent": "Mozilla/5.0"}), timeout=90) as response:
            payload = response.read(64 * 1024 * 1024 + 1)
        if len(payload) > 64 * 1024 * 1024:
            raise ValueError("Xtream API response is too large")
        streams = json.loads(payload)
    except Exception:
        # Never include the URL in public Actions error logs.
        raise RuntimeError("Xtream channel list could not be fetched; existing published EPG remains unchanged.") from None
    if not isinstance(streams, list):
        raise RuntimeError("Xtream API did not return a live channel list. Published EPG remains unchanged.")

    # PPV can be signalled by the Xtream category even when no channel name
    # mentions PPV (e.g. Flo, Peacock and ESPN+ event streams).
    known = {str(v).strip() for v in (known_ppv_category_ids or ()) if str(v).strip()}
    category_names = {}
    categories_url = urlunsplit((parsed.scheme, parsed.netloc, api_path,
                                urlencode({"username": username, "password": password,
                                           "action": "get_live_categories"}), ""))
    try:
        with urlopen(Request(categories_url, headers={"User-Agent": "Mozilla/5.0"}), timeout=25) as response:
            category_payload = response.read(4 * 1024 * 1024 + 1)
        if len(category_payload) > 4 * 1024 * 1024:
            raise ValueError("Category response is too large")
        categories = json.loads(category_payload)
        if isinstance(categories, list):
            for item in categories:
                if isinstance(item, dict):
                    cid = str(item.get("category_id") or "").strip()
                    label = str(item.get("category_name") or "").strip()
                    if cid:
                        category_names[cid] = label
    except Exception:
        # Do not fail a previously working guide just because the optional
        # categories endpoint is unavailable. Use last known PPV categories.
        pass
    current_ppv_ids = {cid for cid, label in category_names.items()
                       if re.search(r"\bPPV\b", label, re.I)}

    result = []
    seen = set()
    named_rows, named_seen = [], set()
    always_rows, always_seen = [], set()
    used_placeholder_ids = set()
    named_ids = {str(x).strip() for x in (named_category_ids or ())}
    always_ids = {str(x).strip() for x in (always_category_ids or ())}
    always_names = set(always_names or ())
    legacy_ids = legacy_ids or {}
    previous_bindings = previous_bindings or {}

    def stable_id(kind, fingerprint, category_id, title):
        old = previous_bindings.get(fingerprint, {})
        old_id = old.get("id") if old.get("kind") == kind else None
        if not old_id:
            old_id = legacy_ids.get((kind, category_id, title))
        prefix = "named.live." if kind == "named" else "always.live."
        # Two distinct streams can share the same visible name. Only one may
        # reuse its old name-based static ID; the other receives a unique ID.
        candidate = old_id or (prefix + fingerprint)
        if candidate in used_placeholder_ids:
            candidate = prefix + fingerprint
        used_placeholder_ids.add(candidate)
        return candidate
    for channel in streams:
        if not isinstance(channel, dict):
            continue
        title = str(channel.get("name") or "").strip()
        stream_id = str(channel.get("stream_id") or "").strip()
        category_id = str(channel.get("category_id") or "").strip()
        category_name = category_names.get(category_id, "")
        if not title or not stream_id:
            continue
        is_ppv = bool(PPV_LABEL.search(title)
                      or category_id in current_ppv_ids
                      or (category_id in known and category_id not in category_names))
        # Stable for this Xtream stream ID even when the name changes.
        # Never publish raw stream IDs or credentials.
        opaque_id = hashlib.sha256((parsed.netloc.lower() + ":" + stream_id).encode("utf-8")).hexdigest()[:22]
        is_named = (named_category_ids is not None
                    and (category_id in named_ids or category_is_named(category_name)))
        saved = previous_bindings.get(opaque_id, {})
        no_provider_epg = str(channel.get("epg_channel_id") or "").strip().upper() in BAD_EPG_IDS
        is_always = (always_category_ids is not None and no_provider_epg
                     and (category_id in always_ids
                          or ALWAYS_LABEL.search(category_name)
                          or ALWAYS_LABEL.search(title)
                          or (category_id, title) in always_names
                          or saved.get("kind") == "always"))
        if not (is_ppv or is_named or is_always):
            continue
        is_us = (bool(re.search(r"^(?:US\s*[:(]|\(US\))", title, re.I))
                 or bool(re.search(r"\b(?:US|USA)\b", category_name, re.I)))
        if is_ppv and stream_id not in seen:
            seen.add(stream_id)
            result.append({
                "id": "ppv.live." + opaque_id, "name": title,
                "category": category_name or "PPV",
                "region": "US" if is_us else "DE/International",
            })
        elif is_named and stream_id not in named_seen:
            named_seen.add(stream_id)
            named_rows.append({
                "id": stable_id("named", opaque_id, category_id, title), "name": title,
                "category_id": category_id, "category": category_name or "Sendername",
                "region": "US" if is_us else "DE/International", "source": "live",
                "fingerprint": opaque_id,
            })
        elif is_always and stream_id not in always_seen:
            always_seen.add(stream_id)
            always_rows.append({
                "id": stable_id("always", opaque_id, category_id, title), "name": title,
                "category_id": category_id, "category": category_name or "24/7",
                "region": "US" if is_us else "DE/International", "source": "live",
                "fingerprint": opaque_id,
            })

    if not result:
        raise RuntimeError("Xtream API returned no PPV channels; published EPG remains unchanged.")
    if always_category_ids is not None:
        return result, named_rows, always_rows
    return (result, named_rows) if named_category_ids is not None else result
