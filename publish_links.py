"""Publish friend-specific static XMLTV URLs from an already published guide.

Do not fetch source feeds, execute build.py, or expose Xtream credentials.
GitHub Pages provides path-based publication, NOT private access or IP limits.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
REPO = os.environ.get("GITHUB_REPOSITORY", "Malakinho/tivimate-epg")
OWNER, PROJECT = REPO.split("/", 1)
BASE = f"https://{OWNER.lower()}.github.io/{PROJECT}/"
MAX_ACTIVE = 18


def get_bytes(relative_path: str, limit: int) -> bytes:
    """Fetch only our own published GitHub Pages files, with bounded retries."""
    url = BASE + relative_path
    last_error = None
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": "EPG-friend-link-publisher/1.0", "Cache-Control": "no-cache"})
            with urlopen(req, timeout=45) as response:
                data = response.read(limit + 1)
                if len(data) > limit:
                    raise ValueError("Public EPG input exceeds the allowed file size")
                return data
        except Exception as error:
            last_error = type(error).__name__
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Existing published EPG could not be retrieved ({last_error}); old public website remains unchanged.")


def valid_rows(rows):
    if not isinstance(rows, list) or len(rows) > MAX_ACTIVE:
        raise ValueError("Invalid friend registry or too many recipients")
    tokens, ids = set(), set()
    for friend in rows:
        token = friend.get("token", "")
        friend_id = friend.get("id", "")
        if (not re.fullmatch(r"[A-Za-z0-9_-]{30,120}", token)
                or not re.fullmatch(r"(?:[0-9a-f]{14}|owner)", friend_id)
                or token in tokens or friend_id in ids):
            raise ValueError("Invalid or duplicated friend link record")
        tokens.add(token)
        ids.add(friend_id)


def main():
    current = json.loads((ROOT / "data" / "friends.json").read_text(encoding="utf-8"))
    friends = current["friends"]
    valid_rows(friends)

    # Use the last successful website as the source of truth. New links always
    # point to identical XMLTV content, not a separate extraction of programmes.
    published = json.loads(get_bytes("friends.json", 500_000))
    old_friends = published.get("friends", [])
    valid_published_paths = [
        row["path"] for row in old_friends
        if row.get("active")
        and isinstance(row.get("path"), str)
        and re.fullmatch(r"epg/[A-Za-z0-9_-]{30,120}/epg[.]xml[.]gz", row["path"])
    ]
    owner = next((f for f in old_friends if f.get("id") == "owner"), None)
    primary = owner["path"] if owner and owner.get("path") in valid_published_paths else (
        valid_published_paths[0] if valid_published_paths else ""
    )

    SITE.mkdir(exist_ok=True)
    for filename in ("dashboard.json", "status.json"):
        data = get_bytes(filename, 20_000_000)
        json.loads(data)
        (SITE / filename).write_bytes(data)

    if any(row.get("active") for row in friends):
        if not primary:
            raise RuntimeError("No previously published active EPG exists; a full EPG build is needed.")
        master = SITE / "source-epg.gz"
        master.write_bytes(get_bytes(primary, 200_000_000))
        if master.stat().st_size < 1024 or master.read_bytes()[:2] != b"\x1f\x8b":
            raise RuntimeError("Existing published XMLTV is not a valid gzip file.")
        for row in friends:
            if row.get("active"):
                target = SITE / "epg" / row["token"] / "epg.xml.gz"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(master, target)
        master.unlink()

    public_rows = [
        {"id": row["id"], "label": row["label"],
         "active": bool(row.get("active")),
         "path": "epg/" + row["token"] + "/epg.xml.gz" if row.get("active") else ""}
        for row in friends
    ]
    (SITE / "friends.json").write_text(
        json.dumps({"friends": public_rows, "public_notice": "GitHub Pages paths are publicly discoverable."},
                   ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    shutil.copyfile(ROOT / "dashboard" / "index.html", SITE / "index.html")
    shutil.copyfile(ROOT / "dashboard" / "m3u.html", SITE / "m3u.html")
    print("Published", sum(f["active"] for f in public_rows), "active personal URLs from one existing guide.")
    print("No EPG source providers contacted; no channel data regenerated.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Do not reveal user tokens or provider URLs in public Action logs.
        print("Friend link publishing failed:", type(error).__name__, str(error).split("https://")[0][:180], file=sys.stderr)
        raise SystemExit(1)
