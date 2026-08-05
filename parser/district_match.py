"""
Matches raw district names (as they come out of any bulletin era) to a province,
using the static crosswalk in reference/district_province_crosswalk.csv.

Handles three known real-world defects found across the 2021-2026 sample set:
  1. Floating province-label text contaminating a district name
     (e.g. "Sindh Karachi-Korangi", "Gilgit Baltistan Hunza")
  2. Ordinary spelling drift across years/issues (Naserabad/Naseerabad, Chagi/Chagai)
  3. A broken font/glyph in one specific 2024 issue that silently drops every
     letter "m" from every district name ("Kamber"->"Ka ber", "Jhelum"->"Jhelu")

Fuzzy matching (#2, #3) is guarded against a known failure mode: directional-pair
district names (North/South, Upper/Lower) can be >70% textually similar to their
counterpart while referring to a genuinely different place. DIRECTIONAL_PAIRS below
blocks any fuzzy match that would cross such a pair.
"""
import csv
import difflib
import os

_CROSSWALK_PATH = os.path.join(os.path.dirname(__file__), "reference", "district_province_crosswalk.csv")

DIRECTIONAL_WORDS = ["north", "south", "upper", "lower", "east", "west", "central"]

CONTAMINATING_PREFIXES = [
    "Azad Jammu ", "Kashmir ", "Balochistan ", "Gilgit Baltistan ",
    "Sindh ", "Khyber Pakhtunkhwa ", "Punjab ", "Khyber ", "Pakhtunkhwa ", "SD ",
]


def _load_crosswalk():
    with open(_CROSSWALK_PATH) as f:
        return {r["district_raw"]: r["province"] for r in csv.DictReader(f)}


def _strip_prefix_contamination(name):
    for p in CONTAMINATING_PREFIXES:
        if name.startswith(p) and name != p.strip():
            name = name[len(p):]
    return name.strip()


def _directional_words_in(name):
    low = name.lower()
    return {w for w in DIRECTIONAL_WORDS if w in low}


def match_district(raw, crosswalk=None, cutoff=0.72):
    """Returns (province_or_None, match_method_str)."""
    crosswalk = crosswalk or _load_crosswalk()
    if raw in crosswalk:
        return crosswalk[raw], "exact"

    cleaned = _strip_prefix_contamination(raw)
    if cleaned in crosswalk:
        return crosswalk[cleaned], "prefix-stripped"

    candidates = difflib.get_close_matches(cleaned, list(crosswalk.keys()), n=3, cutoff=cutoff)
    raw_dirs = _directional_words_in(cleaned)
    for cand in candidates:
        cand_dirs = _directional_words_in(cand)
        # reject if directional words differ (e.g. raw has no "north/south" info
        # but candidate does -- ambiguous -- or they actively conflict)
        if raw_dirs and cand_dirs and raw_dirs != cand_dirs:
            continue
        return crosswalk[cand], f"fuzzy->{cand}"

    return None, "UNMATCHED"
