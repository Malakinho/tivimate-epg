"""Handle owner-approved provider EPG-ID claims submitted via GitHub Issues.

Runs only from GitHub Actions with the repository's own GITHUB_TOKEN.
All submitted IDs are validated against inventory and *actual* published
programme entries. No tokens or credentials reach GitHub Pages.
"""
from __future__ import annotations

import base64
import csv
import gzip
import io
import json
import os
import re
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]
EVENT = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
ISSUE = EVENT["issue"]
ID_INVALID = {"", "TS", "N/A", "NONE", "NULL", "0", "-"}
TITLE = "EPG-ID übernehmen"
API = "https://api.github.com/repos/" + REPO
GITHUB_OWNER = REPO.split("/")[0]


def api(path, method="GET", data=None):
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode()
    req = Request(
        API + path, data=body, method=method,
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "EPG-Approval-Workflow",
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=60) as response:
        return json.load(response)


def comment(message):
    api("/issues/" + str(ISSUE["number"]) + "/comments", "POST", {"body": message})


def close():
    api("/issues/" + str(ISSUE["number"]), "PATCH", {"state": "closed"})


def validate_claim():
    if ISSUE["user"]["login"].casefold() != GITHUB_OWNER.casefold():
        raise ValueError("Nur der Repository-Inhaber darf EPG-IDs übernehmen.")
    if ISSUE["title"].strip() != TITLE:
        raise ValueError("Das ist keine EPG-ID-Freigabe.")
    raw = ISSUE.get("body") or ""
    lines = raw.strip().splitlines()
    fields = {}
    for line in lines:
        if "=" not in line:
            raise ValueError("Unzulässiges Format: erwartet Schlüssel=Wert")
        k, v = line.split("=", 1)
        if k not in {"name", "category_id", "provider_id"} or k in fields:
            raise ValueError("Ungültige oder doppelte Feldnamen")
        fields[k] = v.strip()
    if set(fields) != {"name", "category_id", "provider_id"}:
        raise ValueError("Es fehlen Pflichtfelder.")
    if any(not x or len(x) > 300 for x in fields.values()):
        raise ValueError("Ein Feld ist leer oder zu lang.")
    if (fields["provider_id"].upper() in ID_INVALID or
            not re.fullmatch(r"[\w.:-]{1,200}", fields["provider_id"], re.UNICODE)):
        raise ValueError("Ungültige Anbieter-ID.")
    if not re.fullmatch(r"\d{1,8}", fields["category_id"]):
        raise ValueError("Ungültige Kategorie-ID.")

    inventory = list(csv.DictReader(
        Path("data/inventory.csv").open(encoding="utf-8-sig", newline="")
    ))
    matched = [
        rec for rec in inventory
        if (rec.get("name") or "").strip() == fields["name"]
        and (rec.get("category_id") or "").strip() == fields["category_id"]
        and (rec.get("epg_channel_id") or "").strip().casefold() == fields["provider_id"].casefold()
    ]
    if len(matched) != 1:
        raise ValueError("Der Sender mit dieser Anbieter-ID wurde nicht eindeutig in der Senderliste gefunden.")

    # Validate programmes, not just <channel id="..."/> metadata.
    # The guide is public and downloaded without any provider secret.
    guide = "https://" + REPO.split("/")[0].lower() + ".github.io/" + REPO.split("/")[1] + "/epg.xml.gz"
    req = Request(guide, headers={"User-Agent": "EPG-Approval-Workflow"})
    with urlopen(req, timeout=120) as response:
        file_bytes = response.read(100 * 1024 * 1024 + 1)
    if len(file_bytes) > 100 * 1024 * 1024 or not file_bytes.startswith(b"\x1f\x8b"):
        raise ValueError("Die veröffentlichte EPG ist nicht verfügbar.")
    wanted = fields["provider_id"].casefold()
    found = False
    with gzip.GzipFile(fileobj=io.BytesIO(file_bytes)) as guidefile:
        # Bounded streaming parse: stop as soon as actual programme seen.
        parser = ET.iterparse(guidefile, events=("start", "end"))
        root = None
        for event, elem in parser:
            if event == "start" and root is None:
                root = elem
            elif event == "end" and elem.tag == "programme":
                if (elem.get("channel") or "").casefold() == wanted and elem.find("title") is not None:
                    found = True
                    break
                if root is not None:
                    root.clear()
            elif event == "end" and elem.tag == "channel" and root is not None:
                root.clear()
    if not found:
        raise ValueError("Unter dieser Anbieter-ID enthält unsere aktuelle EPG keine Programmdaten. Die ID kann nicht einfach übernommen werden.")
    return fields


def store(fields):
    path = "/contents/data/manual_approvals.json"
    current = api(path)
    existing = json.loads(base64.b64decode(current["content"]).decode("utf-8"))
    approvals = existing.get("approved", [])
    key = (fields["name"], fields["category_id"])
    for entry in approvals:
        if (entry.get("name"), entry.get("category_id")) == key:
            if entry.get("provider_id", "").casefold() == fields["provider_id"].casefold():
                return False
            raise ValueError("Dieser Sender hat bereits eine andere manuelle Zuordnung. Erst die bestehende Freigabe prüfen.")
    approvals.append(fields)
    approvals.sort(key=lambda p: (p["category_id"], p["name"]))
    updated = json.dumps({"approved": approvals}, ensure_ascii=False, indent=2) + "\n"
    api(path, "PUT", {
        "message": "Approve provider EPG ID for " + fields["name"][:70],
        "content": base64.b64encode(updated.encode("utf-8")).decode("ascii"),
        "sha": current["sha"],
        "branch": "main",
    })
    return True


if __name__ == "__main__":
    try:
        claim = validate_claim()
        changed = store(claim)
        message = (
            "✅ **EPG-ID freigegeben.** Die nächste GitHub-Pages-Veröffentlichung übernimmt "
            "die bestätigte Anbieter-ID in der Webapp. Der EPG-Link bleibt gleich."
            if changed else
            "ℹ️ Diese Zuordnung war bereits freigegeben."
        )
    except Exception as exc:
        # No credential/URL is ever included in the error text.
        message = "❌ **Nicht übernommen:** " + str(exc)[:400]
    comment(message)
    close()
