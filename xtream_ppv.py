"""Read the current PPV sender names from the user's Xtream live channel API.

Credentials are read only from a GitHub Actions secret (XTREAM_M3U_URL).
Neither the credentials nor the provider URLs are written to the EPG.
"""
import hashlib
import json
import re

PPV_LABEL = re.compile(r"(?:\bPPV\b|\bPPV(?=\d)|ᴾᴾⱽ)", re.I)
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


def current_ppv_rows(secret_url: str, known_ppv_category_ids=None):
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
    for channel in streams:
        if not isinstance(channel, dict):
            continue
        title = str(channel.get("name") or "").strip()
        stream_id = str(channel.get("stream_id") or "").strip()
        category_id = str(channel.get("category_id") or "").strip()
        category_name = category_names.get(category_id, "")
        if not title or not stream_id:
            continue
        if not (PPV_LABEL.search(title)
                or category_id in current_ppv_ids
                or (category_id in known and category_id not in category_names)):
            continue
        if stream_id in seen:
            continue
        seen.add(stream_id)
        # Stable for this Xtream stream ID, even when its name changes.
        # Do not publish raw stream IDs or credentials.
        opaque_id = hashlib.sha256((parsed.netloc.lower() + ":" + stream_id).encode("utf-8")).hexdigest()[:22]
        is_us = (bool(re.search(r"^(?:US\s*[:(]|\(US\))", title, re.I))
                 or bool(re.search(r"\b(?:US|USA)\b", category_name, re.I)))
        result.append({
            "id": "ppv.live." + opaque_id, "name": title,
            "category": category_name or "PPV",
            "region": "US" if is_us else "DE/International",
        })

    if not result:
        raise RuntimeError("Xtream API returned no PPV channels; published EPG remains unchanged.")
    return result
