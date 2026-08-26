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

DISEASE_NAME_NORMALIZE = {
    "A LRI <5 Years": "ALRI <5 years",
    "A LRI < 5 years": "ALRI <5 years",
    "ALR I < 5 years": "ALRI <5 years",
    "ALRI < 5": "ALRI <5 years",
    "ALRI < 5 years": "ALRI <5 years",
    "ALRI <5 Years": "ALRI <5 years",

    "AD (Non-": "AD (Non-Cholera)",
    "AD (Non- Cholera)": "AD (Non-Cholera)",
    "AD (non- cholera)": "AD (Non-Cholera)",
    "AD Non-": "AD (Non-Cholera)",
    "AD Non- Cholera)": "AD (Non-Cholera)",

    "AVH": "AVH (A & E)",
    "AVH (A&E)": "AVH (A & E)",

    "AWD": "AWD (S. Cholera)",
    "AWD (S.Cholera)": "AWD (S. Cholera)",

    "B.": "B. Diarrhea",
    "B.Diarrhea": "B. Diarrhea",

    "Dog": "Animal/Dog Bite",
    "Dog Bite": "Animal/Dog Bite",
    "Animal / Dog Bite": "Animal/Dog Bite",

    "VH (B, C": "VH (B, C & D)",

    "Brucell osis": "Brucellosis",
    "Mump s": "Mumps",
    "Typh oid": "Typhoid",
    "Typhoi d": "Typhoid",
}


def normalize_disease_name(name):
    if name in DISEASE_NAME_NORMALIZE:
        return DISEASE_NAME_NORMALIZE[name]
    # case-insensitive fallback, since some files differ only in capitalization
    # (confirmed real: "AD (Non-Cholera)" vs "AD (non- cholera)")
    lname = name.strip().lower()
    for k, v in DISEASE_NAME_NORMALIZE.items():
        if k.strip().lower() == lname:
            return v
    return name
