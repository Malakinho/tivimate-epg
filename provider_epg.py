"""Safely load the authenticated Xtream XMLTV feed as a fallback.

Only programme entries for explicitly requested provider tvg-ids are re-emitted.
Credentials and provider-origin media URLs are NEVER copied to public output.
"""
from __future__ import annotations

import copy
import gzip
import io
import logging
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

MAX_COMPRESSED_BYTES = 256 * 1024 * 1024
ALLOWED_PROGRAMME_TAGS = {
    "title", "sub-title", "desc", "category", "date", "episode-num",
    "previously-shown", "new", "premiere", "last-chance", "rating",
    "star-rating", "country", "language", "orig-language", "length",
    "credits", "video", "audio", "subtitles",
}


def fetch_provider_xmltv(m3u_url: str, dest_dir: Path) -> Path:
    """Download authenticated provider XMLTV. Never expose the URL in logs."""
    parsed = urlsplit(m3u_url.strip())
    query = parse_qs(parsed.query, keep_blank_values=True)
    username = query.get("username", [""])[0]
    password = query.get("password", [""])[0]
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not username or not password:
        raise RuntimeError("Ungültige Xtream-Secret-Konfiguration")
    base_path = parsed.path.rsplit("/", 1)[0]
    url = urlunsplit((
        parsed.scheme, parsed.netloc, base_path + "/xmltv.php",
        urlencode({"username": username, "password": password}), "",
    ))
    destination = dest_dir / "provider.xmltv.download"
    try:
        with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0",
                                           "Accept-Encoding": "identity"}), timeout=120) as response:
            with destination.open("wb") as out:
                total = 0
                head = b""
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    if not head:
                        head = chunk[:4096]
                    total += len(chunk)
                    if total > MAX_COMPRESSED_BYTES:
                        raise ValueError("Provider guide exceeds size limit")
                    out.write(chunk)
        if total < 100:
            raise ValueError("Provider guide is empty")
        is_gzip = head.startswith(b"\x1f\x8b")
        is_xml = head.lstrip().startswith((b"<?xml", b"<tv", b"\xef\xbb\xbf<?xml"))
        if not (is_gzip or is_xml):
            raise ValueError("Provider response is not XMLTV")
        if is_gzip:
            destination = destination.rename(dest_dir / "provider.xmltv.gz")
        else:
            destination = destination.rename(dest_dir / "provider.xmltv.xml")
        return destination
    except Exception:
        # urllib errors can contain the authenticated URL.
        raise RuntimeError("Anbieter-EPG konnte nicht geladen werden (Zugangsdaten bleiben verborgen)") from None


def open_provider(path: Path):
    return gzip.open(path, "rb") if path.suffix == ".gz" else path.open("rb")


def provider_coverage(path: Path, wanted: set[str]) -> tuple[dict[str, str], Counter]:
    """Return actual XMLTV source ID by casefold and programme counts for wanted IDs.

    Uses streaming XML parse to handle big provider guides.
    """
    canonical: dict[str, str] = {}
    counts = Counter()
    try:
        with open_provider(path) as raw:
            parser = ET.iterparse(raw, events=("start", "end"))
            root = None
            for event, elem in parser:
                if event == "start" and root is None:
                    root = elem
                elif event == "end" and elem.tag == "channel":
                    cid = (elem.get("id") or "").strip()
                    if cid and cid.casefold() in wanted:
                        canonical.setdefault(cid.casefold(), cid)
                    if root is not None:
                        root.clear()
                elif event == "end" and elem.tag == "programme":
                    cid = (elem.get("channel") or "").strip()
                    if cid and cid.casefold() in wanted:
                        counts[cid.casefold()] += 1
                        canonical.setdefault(cid.casefold(), cid)
                    if root is not None:
                        root.clear()
    except Exception:
        raise RuntimeError("Anbieter-EPG ist kein gültiges XMLTV") from None
    return canonical, counts


def emit_provider_programmes(path: Path, zipout, matched: dict[str, str]) -> int:
    """Only write selected schedules and safe metadata (no authenticated URLs).

    matched maps source ID casefold to public playlist EPG ID.
    """
    n = 0
    with open_provider(path) as raw:
        parser = ET.iterparse(raw, events=("start", "end"))
        root = None
        for event, elem in parser:
            if event == "start" and root is None:
                root = elem
            elif event == "end" and elem.tag == "programme":
                source_id = (elem.get("channel") or "").strip().casefold()
                target = matched.get(source_id)
                if target:
                    attrs = {"channel": target}
                    for key in ("start", "stop"):
                        if elem.get(key):
                            attrs[key] = elem.get(key)
                    if "start" in attrs and "stop" in attrs:
                        out = ET.Element("programme", attrs)
                        for child in elem:
                            if child.tag in ALLOWED_PROGRAMME_TAGS:
                                # Copy metadata but deliberately remove external src/href
                                # and any nested image URLs, which might contain tokens.
                                clean = copy.deepcopy(child)
                                for descendant in clean.iter():
                                    for key in list(descendant.attrib):
                                        if key.lower() in {"src", "href", "url", "icon"}:
                                            del descendant.attrib[key]
                                out.append(clean)
                        if out.find("title") is not None:
                            zipout.write(ET.tostring(out, encoding="utf-8") + b"\n")
                            n += 1
                if root is not None:
                    root.clear()
            elif event == "end" and elem.tag == "channel" and root is not None:
                root.clear()
    return n
