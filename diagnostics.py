"""Public, credential-free diagnostics for upstream EPG feed availability.

Never publish feed URLs, request headers, private stream identifiers or
exception messages: diagnostics contain only checked labels and counts.
"""


def begin_source_report(sources):
    """Names and markets are a predefined public allowlist, URLs are discarded."""
    return {
        name: {
            "name": name,
            "market": market,
            "status": "failed",
            "reason": "not_checked",
            "channels": 0,
            "programmes": 0,
        }
        for name, market, _url in sources
    }


def mark_source(report, name, status, reason="", channels=0, programmes=0):
    if name not in report:
        raise KeyError("Unknown source")
    if status not in {"ok", "failed", "empty"}:
        raise ValueError("Unknown source status")
    if reason not in {"", "not_checked", "download", "invalid_feed", "no_programmes"}:
        raise ValueError("Unsafe source error label")
    item = report[name]
    item["status"] = status
    item["reason"] = reason
    item["channels"] = max(0, int(channels))
    item["programmes"] = max(0, int(programmes))


def public_source_report(report):
    """Defensive copy, stable order and public-only field allowlist."""
    keys = ("name", "market", "status", "reason", "channels", "programmes")
    return [{k: entry[k] for k in keys} for entry in report.values()]
