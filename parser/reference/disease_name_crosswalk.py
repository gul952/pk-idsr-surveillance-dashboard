"""
Canonical disease-name normalization for district-level disease case tables.

Multi-line wrapped disease-name headers (e.g. "AD (Non-" / "Cholera)" on
separate grid rows) don't reconstruct consistently across every table
instance -- confirmed real: the same source file can produce different
truncated variants for the same disease across its different province
tables (Sindh's Table 2 vs Balochistan's Table 3 vs KP's Table 4), and
some PDFs have a font-rendering quirk that inserts spurious spaces mid-word
("ALRI <5 years" -> "A LRI <5 Years", "ALR I < 5 years").

Rather than chase every possible header-reconstruction edge case (the
compliance and province-summary tables already needed several rounds of
this and it's genuinely open-ended), this normalizes AFTER extraction,
the same pattern used for district names in district_province_crosswalk.csv.
"""

"""
Canonical disease-name normalization for district-level disease case tables.

Multi-line wrapped disease-name headers don't reconstruct consistently across
every table instance, and some PDFs have a font-rendering quirk that inserts
a spurious space somewhere inside a word -- confirmed real, and confirmed to
recur in genuinely new positions with every new file scraped ("Malaria" ->
"Malar ia" in one file, "Malari a" in another, "Den gue" / "Dengu e" for
Dengue). A hand-maintained lookup of exact strings can never keep up with
this -- it's whack-a-mole against an open-ended set of corruption positions.

Fix: normalize by stripping ALL internal whitespace and lowercasing before
comparing to a canonical list. This makes the exact position of an inserted
space irrelevant. Truncated names (a table's header only ever captured part
of the word, e.g. "B. Diar" missing "rhea") are handled with a prefix check
on the same space-stripped, lowercased form.
"""

CANONICAL_DISEASES = [
    "AD (Non-Cholera)", "Malaria", "ILI", "ALRI <5 years", "TB", "B. Diarrhea",
    "VH (B, C & D)", "Animal/Dog Bite", "Typhoid", "SARI", "AVH (A & E)",
    "Measles", "CL", "AWD (S. Cholera)", "Chickenpox/Varicella", "Chickenpox",
    "Mumps", "Chikungunya", "Dengue", "Pertussis", "AFP", "Meningitis",
    "Gonorrhea", "HIV/AIDS", "Syphilis", "Brucellosis", "Diphtheria (Probable)",
    "NT", "Leprosy", "Rubella (CRS)", "Rubella", "Rabies", "Anthrax",
    "S. Cholera", "Mpox", "COVID-19", "CCHF", "VL",
]


def _squash(s):
    return "".join(ch for ch in s.lower() if ch.isalnum())


_CANONICAL_SQUASHED = {_squash(d): d for d in CANONICAL_DISEASES}
# longest first, so e.g. "Chickenpox/Varicella" is tried before "Chickenpox"
# when checking prefixes -- avoids truncating a longer real name to a shorter one
_CANONICAL_BY_LENGTH = sorted(CANONICAL_DISEASES, key=lambda d: -len(_squash(d)))


def normalize_disease_name(name):
    squashed = _squash(name)
    if not squashed:
        return name
    if squashed in _CANONICAL_SQUASHED:
        return _CANONICAL_SQUASHED[squashed]
    # truncation: the observed name is a (long-enough) fragment of a real one
    # (could be missing a prefix, like "Dog Bite" for "Animal/Dog Bite", or a
    # suffix, like "B. Diar" for "B. Diarrhea") -- substring check, not just
    # prefix. Length-gated at 4 chars to avoid short fragments matching too
    # eagerly (protects short real abbreviations like "TB", "CL", "NT").
    for canon in _CANONICAL_BY_LENGTH:
        canon_sq = _squash(canon)
        if len(squashed) >= 3 and squashed in canon_sq:
            return canon
        if len(canon_sq) >= 3 and canon_sq in squashed:
            return canon
    return name
