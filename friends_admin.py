"""GitHub-only EPG friend administration via repository-owner Issues.

This is NOT IP locking or confidential access: public GitHub Pages,
public repository data and public Actions artifacts may expose link paths.
Only owner-created Issues are allowed to mutate the public friend registry.
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]
EVENT = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
ISSUE = EVENT["issue"]
OWNER = REPO.split("/", 1)[0]
API_ROOT = "https://api.github.com/repos/" + REPO
ISSUE_TITLE = "EPG-Freund verwalten"
MAX_FRIENDS = 18


def api(path, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        API_ROOT + path,
        method=method,
        data=data,
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "GitHub-EPG-Freundeverwaltung",
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=60) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def post_comment(message):
    api("/issues/" + str(ISSUE["number"]) + "/comments", "POST", {"body": message})


def close_issue():
    # The owner might manually close the issue before Actions finishes.
    # Avoid marking an otherwise successful creation as failed in this case.
    path = "/issues/" + str(ISSUE["number"])
    if api(path).get("state") == "closed":
        return
    try:
        api(path, "PATCH", {"state": "closed"})
    except HTTPError as error:
        if error.code == 422 and api(path).get("state") == "closed":
            return
        raise


def parse_request():
    if ISSUE["user"]["login"].casefold() != OWNER.casefold():
        raise ValueError("Nur der Repository-Inhaber kann Freunde verwalten.")
    if ISSUE["title"].strip() != ISSUE_TITLE:
        raise ValueError("Nicht die erwartete Verwaltungsanfrage.")
    if len(ISSUE.get("body") or "") > 500:
        raise ValueError("Verwaltungsanfrage ist zu lang.")
    data = {}
    for raw in (ISSUE.get("body") or "").splitlines():
        if not raw.strip():
            continue
        if "=" not in raw:
            raise ValueError("Ungültiges Eingabeformat.")
        k, v = raw.split("=", 1)
        if k.strip() not in {"action", "label", "id"} or k.strip() in data:
            raise ValueError("Unbekanntes oder doppeltes Eingabefeld.")
        data[k.strip()] = v.strip()
    action = data.get("action", "")
    if action not in {"create", "revoke", "rotate", "reactivate"}:
        raise ValueError("Unbekannte Aktion.")
    if action == "create":
        if set(data) != {"action", "label"}:
            raise ValueError("Zum Anlegen wird ein Name benötigt.")
        name = data["label"]
        if not 1 <= len(name) <= 45 or not name.strip() or re.search(r"[\x00-\x1f\x7f]", name):
            raise ValueError("Der Name darf höchstens 45 Zeichen umfassen.")
    else:
        if set(data) != {"action", "id"} or not re.fullmatch(r"(?:[0-9a-f]{14}|owner)", data["id"]):
            raise ValueError("Ungültige Freund-ID.")
    return data


def apply_change(data):
    path = "/contents/data/friends.json"
    current = api(path)
    registry = json.loads(base64.b64decode(current["content"]).decode("utf-8"))
    friends = registry.get("friends")
    if not isinstance(friends, list):
        raise ValueError("Die Freundeliste ist beschädigt.")
    action = data["action"]
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if action == "create":
        if len(friends) >= MAX_FRIENDS:
            raise ValueError("Aktuell maximal 18 Links möglich, damit die Pages-Größe nicht zu groß wird.")
        label = data["label"]
        if any(row["label"].casefold() == label.casefold() for row in friends):
            raise ValueError("Diesen Freundnamen gibt es schon. Bitte eine eindeutige Bezeichnung wählen.")
        friends.append({
            "id": secrets.token_hex(7),
            "label": label,
            "token": secrets.token_urlsafe(30),
            "active": True,
            "created_at": when,
            "changed_at": when,
        })
        result = "✅ Freund **" + label + "** angelegt. Der neue Link ist nach der GitHub-Veröffentlichung in der Webapp sichtbar."
    else:
        item = next((row for row in friends if row.get("id") == data["id"]), None)
        if item is None:
            raise ValueError("Dieser Freund existiert nicht mehr.")
        if action == "revoke":
            if not item.get("active"):
                raise ValueError("Der persönliche Link ist schon gesperrt.")
            item["active"] = False
            result = "⛔ **" + item["label"] + "** gesperrt. Der Link wird nach der Veröffentlichung nicht mehr ausgeliefert."
        elif action == "rotate":
            item["token"] = secrets.token_urlsafe(30)
            result = "🔁 Link von **" + item["label"] + "** erneuert. Der alte Pfad wird nach der Veröffentlichung entfernt."
        else:
            if item.get("active"):
                raise ValueError("Der Link ist bereits aktiv.")
            item["active"] = True
            result = "✅ **" + item["label"] + "** reaktiviert."
        item["changed_at"] = when

    api(path, "PUT", {
        "message": "Manage friend EPG link (" + action + ")",
        "content": base64.b64encode((json.dumps(registry, ensure_ascii=False, indent=2) + "\n").encode("utf-8")).decode("ascii"),
        "sha": current["sha"],
        "branch": "main",
    })
    # A GITHUB_TOKEN commit does not trigger a separate push workflow.
    api("/actions/workflows/update.yml/dispatches", "POST", {"ref": "main"})
    return result


if __name__ == "__main__":
    try:
        data = parse_request()
        message = apply_change(data)
    except Exception as exc:
        # No tokens are included in error messages or issue comments.
        message = "❌ **Nicht ausgeführt:** " + str(exc)[:380]
    post_comment(message)
    close_issue()
