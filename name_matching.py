"""Conservative, provider-independent channel-name comparison.

All keys are formed from names already normalized by build.clean_name.
Exact matches win. Compact matches merge spacing differences only if one
source spelling exists for that compact key and its digit groups agree.
Unknown near matches, abbreviations and ambiguous keys are not guessed.
"""
import re
from collections import defaultdict


def compact_key(normalized: str):
    """Keep every non-space character and the exact groups of digits."""
    return normalized.replace(" ", ""), tuple(re.findall(r"\d+", normalized))


def compact_index(by_name: dict):
    """Build an index only for unique source spellings.

    Several guide sources may publish one channel under the same normalized
    name. That is fine: the usual country/coverage ranking selects one.
    Two different spellings collapsing to the same compact key are ambiguous,
    however, so they must never be auto-matched through this fallback.
    """
    keys = defaultdict(dict)
    for normalized, channels in by_name.items():
        if not normalized:
            continue
        key = compact_key(normalized)
        if len(key[0]) < 5:
            continue
        keys[key][normalized] = channels
    return {key: next(iter(variants.values()))
            for key, variants in keys.items() if len(variants) == 1}


def matching_candidates(normalized: str, by_name: dict, by_compact: dict):
    """Exact normalized spelling first, then unambiguous compact spelling."""
    exact = by_name.get(normalized, ())
    if exact:
        return exact
    key = compact_key(normalized)
    if len(key[0]) < 5:
        return ()
    return by_compact.get(key, ())
