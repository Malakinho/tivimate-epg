"""Read the current PPV sender names from the user's Xtream live channel API.

Credentials are read only from a GitHub Actions secret (XTREAM_M3U_URL).
Neither the credentials nor the provider URLs are written to the EPG.
"""
import hashlib
import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


def current_ppv_rows(secret_url: str):
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

    result = []
    seen = set()
    for channel in streams:
        if not isinstance(channel, dict):
            continue
        title = str(channel.get("name") or "").strip()
        stream_id = str(channel.get("stream_id") or "").strip()
        if not title or not stream_id or not re.search(r"\bPPV\b", title, re.I):
            continue
        if stream_id in seen:
            continue
        seen.add(stream_id)
        # Stable for this Xtream stream ID, even when its name changes.
        # Do not publish raw stream IDs or credentials.
        opaque_id = hashlib.sha256((parsed.netloc.lower() + ":" + stream_id).encode("utf-8")).hexdigest()[:22]
        result.append({"id": "ppv.live." + opaque_id, "name": title})

    if not result:
        raise RuntimeError("Xtream API returned no PPV channels; published EPG remains unchanged.")
    return result
