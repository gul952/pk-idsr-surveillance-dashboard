"""
NIH Pakistan Weekly IDSR Bulletin — table extractors.

Covers bulletin formats from 2023-2026 ("modern" era) and 2021-2022 ("legacy" era).
Each extractor works from pdfplumber page objects. Grid-mode (find_tables) is used
only to recover correctly-ordered column headers from wrapped multi-line labels;
all data rows are pulled from plain text extraction, which -- unlike grid mode --
stays reliably aligned for numeric cells in these documents.

STATUS (validated against 12 real bulletins spanning Week 25/2021 - Week 25/2026):
  - compliance table (Table 6, district IDSR reporting %): validated, all 12 files, 2 formats
  - province summary (Table 1, disease x province): validated on 2026 sample, sum-check passes
  - district-disease tables (Table 2/3/4..., disease x district per province):
        validated 2023-2026 (10/12 files). 2021/2022 legacy format NOT yet supported.
        Punjab's district table is absent from every sample file checked -- confirmed
        structural gap, not an extraction bug (see also: province summary + compliance
        table both mark Punjab "NR" in the same weeks).
  - lab confirmation table (Table 5): ~20/25 standard rows working; nested respiratory
        sub-panel (Covid-19/Influenza A/B x ILI/SARI) not yet handled -- lower priority.

Known cosmetic issue: a small number of disease/test names that wrap across 2 print
lines can get mis-attributed to the following row (e.g. "Chickenpox/" + "Varicella").
Values are unaffected. Fix planned via a static disease-name lookup table, since the
wrapping is identical every week.
"""
import re

def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


_NARRATIVE_WORDS = {
    "the", "a", "an", "this", "that", "did", "does", "from", "data", "week",
    "percent", "all", "health", "facilities", "report", "reported", "not",
    "district", "districts", "of", "in", "for", "and", "was", "were",
}


def _looks_like_a_name(candidate, max_words=4):
    """Rejects narrative-prose false positives (e.g. 'District Umerkot did not
    report Kech') that can otherwise satisfy a loose row regex by accident.
    Careful with punctuation: real district names can contain tight
    abbreviation-style periods ('D.I. Khan' -- single-letter initials, no
    full word before the period), which must not be confused with prose
    sentence boundaries ('reporting. Malakand' -- a full word before the
    period). Only the latter pattern is rejected."""
    import re as _re
    if _re.search(r",\s|\s&\s", candidate):
        return False
    if _re.search(r"[a-zA-Z]{2,}\.\s", candidate):  # 2+-letter word then period+space
        return False
    words = candidate.strip().split()
    if not words or len(words) > max_words:
        return False
    if any(w.lower() in _NARRATIVE_WORDS for w in words):
        return False
    return True


# ---------- Table 6: district compliance ----------

PAT_COMPLIANCE_MODERN = re.compile(r"^(.*?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d{1,3})%\s*$")
# some issues (confirmed real, e.g. Weekly Report-16-2023) have an extra
# "Number of Agreed Reporting Sites" column between total and reported:
# District Total Agreed Reported Rate% (4 numbers, not 3). Verified which
# field is which by checking the compliance-rate arithmetic directly against
# real data (Umerkot: 98 total, 41 agreed, 34 reported, 83% -- only
# 34/41 = 83% works, confirming rate is reported/agreed, not reported/total).
# 'total' in this variant is dropped -- 'agreed' becomes our total_sites, to
# stay arithmetically consistent with compliance_rate the way every other
# format already is.
PAT_COMPLIANCE_MODERN_4COL = re.compile(r"^(.*?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d{1,3})%\s*$")
PAT_COMPLIANCE_LEGACY = re.compile(r"^(.*?)\s+(\d[\d,]*)/(\d[\d,]*)\s+(\d[\d,]*)\s*\((\d{1,3})%\)\s*$")

def _rows_from_page_compliance(text):
    modern, legacy = [], []
    for line in text.split("\n"):
        line = line.strip()
        m4 = PAT_COMPLIANCE_MODERN_4COL.match(line)
        if m4:
            d, total, agreed, rep, rate = m4.groups()
            # sanity check: does reported/agreed actually match the stated rate?
            # if not, this 4-number match is probably coincidental noise, not
            # a real 4-column row -- fall through to try the 3-column pattern
            # on the same line instead of trusting a bad parse.
            agreed_n, rep_n, rate_n = int(agreed.replace(",", "")), int(rep.replace(",", "")), int(rate)
            implied = round(100 * rep_n / agreed_n) if agreed_n else None
            if implied is not None and abs(implied - rate_n) <= 1:
                modern.append({"district": d.strip(), "total_sites": agreed_n,
                                "reported_sites": rep_n, "compliance_rate": rate_n})
                continue
        m = PAT_COMPLIANCE_MODERN.match(line)
        if m:
            d, tot, rep, rate = m.groups()
            modern.append({"district": d.strip(), "total_sites": int(tot.replace(",", "")),
                            "reported_sites": int(rep.replace(",", "")), "compliance_rate": int(rate)})
            continue
        m2 = PAT_COMPLIANCE_LEGACY.match(line)
        if m2:
            d, ars, total, rep, rate = m2.groups()
            if not _looks_like_a_name(d):
                continue
            legacy.append({"district": d.strip(), "total_sites": int(total.replace(",", "")),
                            "reported_sites": int(rep.replace(",", "")), "compliance_rate": int(rate),
                            "ars": int(ars.replace(",", ""))})
    return modern, legacy


def extract_compliance_table(pdf, min_rows_to_confirm=5):
    """Scans a full pdfplumber.PDF for the district-compliance table (Table 6 in
    modern bulletins, Table 5 in 2021/2022). Returns dict with format/pages/rows."""
    n = len(pdf.pages)
    for i in range(n):
        text = pdf.pages[i].extract_text() or ""
        modern, legacy = _rows_from_page_compliance(text)
        if len(modern) >= min_rows_to_confirm or len(legacy) >= min_rows_to_confirm:
            fmt = "modern" if len(modern) >= len(legacy) else "legacy"
            all_rows = modern if fmt == "modern" else legacy
            end = i + 1
            empty_streak = 0
            for j in range(i + 1, n):
                t2 = pdf.pages[j].extract_text() or ""
                if re.search(r"table\s*7|tertiary", t2, re.IGNORECASE):
                    break
                m2, o2 = _rows_from_page_compliance(t2)
                rows2 = m2 if fmt == "modern" else o2
                if len(rows2) == 0:
                    empty_streak += 1
                    if empty_streak >= 2:
                        break
                else:
                    empty_streak = 0
                    all_rows.extend(rows2)
                    end = j + 1
            return {"format": fmt, "start_page": i + 1, "end_page": end, "rows": all_rows}
    return {"format": None, "start_page": None, "end_page": None, "rows": []}


# ---------- Table 1: province-wise national summary ----------

_KNOWN_PROVINCE_TOKENS = {
    "ajk", "azad", "jammu", "kashmir", "balochistan", "gilgit", "baltistan",
    "gb", "ict", "islamabad", "khyber", "pakhtunkhwa", "kp", "punjab",
    "sindh", "total",
}


def _find_province_header(page):
    """Reconstructs Table 1's header from the grid, trying increasing numbers
    of header rows -- province names wrap across 2 lines in the modern
    format but 3 in the 2021/2022 legacy format ('Azad Jamu' / 'and' /
    'Kashmir' on three separate grid rows). Stops growing the header the
    moment a row contains purely-numeric cells, since that means the row is
    actual data (e.g. the ILI row), not a header continuation -- verified
    against real data rather than trusted blindly, since an earlier looser
    check accepted an incomplete 2-row reconstruction ('Azad Jamu and',
    missing 'Kashmir') because 'and' happened to be in the province
    vocabulary too."""
    tabs = page.find_tables()
    num_pat = re.compile(r"^[\d,]+$")
    for t in tabs:
        data = t.extract()
        if not data or not data[0]:
            continue
        # "Diseases" can be the literal first cell (legacy format) or appear
        # a few cells in with leading empty cells (modern format) -- check
        # the first few cells, not strictly index 0
        first_cells = [(c or "").strip() for c in data[0][:3]]
        if "Diseases" not in first_cells:
            continue
        max_rows = 1
        for row in data[1:5]:
            if any(c and num_pat.match(c.strip()) for c in row):
                break
            max_rows += 1
        header = _reconstruct_header(t, n_header_rows=max_rows)
        if not header or header[0] != "Diseases":
            continue
        provinces = header[1:]
        if not (6 <= len(provinces) <= 9):
            continue
        plausible = all(
            any(tok in _KNOWN_PROVINCE_TOKENS for tok in re.split(r"\s+", p.lower()))
            for p in provinces
        )
        if plausible:
            return provinces
    return None


_KNOWN_DISEASE_NAMES = [
    "AD (Non-Cholera)", "Malaria", "ILI", "ALRI < 5 years", "TB", "B. Diarrhea",
    "VH (B, C & D)", "Dog Bite", "Typhoid", "SARI", "AVH (A & E)", "Measles",
    "CL", "AWD (S. Cholera)", "Chickenpox/Varicella", "Mumps", "Chikungunya",
    "Dengue", "Pertussis", "AFP", "Meningitis", "Gonorrhea", "HIV/AIDS",
    "Syphilis", "Brucellosis", "Diphtheria (Probable)", "NT", "Leprosy",
    "Rubella (CRS)", "Rabies", "Anthrax", "S. Cholera", "AVH",
]


def _extract_known_disease_suffix(contaminated_name):
    """Recovers the real disease name from a narrative-contaminated string.
    Some legacy pages use a two-column layout (narrative sidebar beside the
    table); linear text extraction interleaves the columns, so a data row's
    'name' portion can pick up a whole paragraph as a prefix. Values stay
    correct regardless (captured positionally from the string's tail), but
    the name needs recovery. The real disease name reliably survives as the
    tail of the contaminated string, so this tries known names as suffixes,
    longest first, rather than accepting the raw blob."""
    low = contaminated_name.lower()
    for d in sorted(_KNOWN_DISEASE_NAMES, key=len, reverse=True):
        if low.endswith(d.lower()):
            return d
    return contaminated_name


_PROVINCE_NAME_NORMALIZE = {
    "Azad Jamu and Kashmir": "AJK",
    "Gilgit Baltistan": "GB",
    "Khyber Pakhtun khwa": "KP",
}


def extract_province_summary(page):
    """Extracts the Diseases x Province national summary table (Table 1)."""
    text = page.extract_text() or ""
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    provinces = _find_province_header(page)
    if provinces is None:
        return None
    provinces = [_PROVINCE_NAME_NORMALIZE.get(p, p) for p in provinces]
    n = len(provinces)

    val_pat = re.compile(r"^(?:NR|[\d,]+)$")
    rows = []
    pending_name = ""
    start_idx = 0
    for i, l in enumerate(lines):
        if re.match(r"^Table\s*\d+:", l):
            start_idx = i + 1  # skip narrative highlights before the table --
            break               # otherwise pending_name accumulates garbage
    for l in lines[start_idx:]:
        if l.startswith("Page"):
            continue
        tokens = l.split()
        if len(tokens) >= n and all(val_pat.match(t) for t in tokens[-n:]):
            name_tokens = tokens[:-n]
            name = (pending_name + " " + " ".join(name_tokens)).strip()
            pending_name = ""
            if not name:
                continue
            if len(name) > 30 or len(name.split()) > 4:
                name = _extract_known_disease_suffix(name)
            name = _TABLE1_NAME_FIXES.get(name, name)
            vals = tokens[-n:]
            row = {"disease": name}
            for p, v in zip(provinces, vals):
                row[p] = None if v == "NR" else int(v.replace(",", ""))
            rows.append(row)
        else:
            pending_name = (pending_name + " " + l).strip()
    return {"provinces": provinces, "rows": rows}


# ---------- Table 5: laboratory confirmation ----------

LAB_PROVINCES = ["Sindh", "Balochistan", "KPK", "ISL", "GB", "Punjab", "AJK"]

# The wrapped 2-line disease/test names in Table 5 and Table 1 follow an
# identical layout every week (static table structure), so rather than solve
# the general "orphan continuation line" problem algorithmically, these are
# corrected with a fixed lookup once verified against real bulletins.
_TABLE1_NAME_FIXES = {
    "Chickenpox/": "Chickenpox/Varicella",
}
_TABLE5_NAME_FIXES = {
    "Stool culture &": "Stool culture & Sensitivity",
}
# orphan fragment that trails a fixed name and would otherwise contaminate
# the *next* row's name if not explicitly consumed
_TABLE5_ORPHAN_SKIP = {
    "Stool culture &": "Sensitivity",
}


def extract_lab_confirmation_table(page):
    """Extracts the Table 5 lab-confirmation grid (Test/Positive per province,
    per disease). Returns None if this page doesn't contain it. Only the
    standard single-line-name rows are covered; the nested respiratory
    sub-panel (Covid-19/Influenza A/B x ILI/SARI) at the bottom of the table
    is intentionally not parsed here -- lower priority, structurally a
    2-level hierarchy rather than a flat disease list."""
    text = page.extract_text() or ""
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    started = False
    header_seen_provinces = False
    n = len(LAB_PROVINCES) * 2
    val_pat = re.compile(r"^(?:-|[\d,]+)$")
    rows = []
    pending_name = ""
    skip_next_orphan = None
    for l in lines:
        if l.startswith("Table 5"):
            started = True
            continue
        if not started:
            continue
        if l == "Page" or l.startswith("Page"):
            continue
        if l.strip() == " ".join(LAB_PROVINCES) or l.strip() == "Diseases":
            header_seen_provinces = True
            continue
        if set(l.replace(" ", "")) <= set("TotalTestPos"):
            continue
        if l.strip() in ("ILI", "SARI", "Covid-19", "Influenza A", "Influenza B"):
            break  # reached the nested respiratory sub-panel -- stop here

        tokens = l.split()
        if len(tokens) >= n and all(val_pat.match(t) for t in tokens[-n:]):
            name_tokens = tokens[:-n]
            name = (pending_name + " " + " ".join(name_tokens)).strip()
            pending_name = ""
            if not name:
                continue
            orphan = _TABLE5_ORPHAN_SKIP.get(name)
            name = _TABLE5_NAME_FIXES.get(name, name)
            skip_next_orphan = orphan
            vals = tokens[-n:]
            row = {"disease": name}
            for i, p in enumerate(LAB_PROVINCES):
                test_v, pos_v = vals[i * 2], vals[i * 2 + 1]
                row[f"{p}_test"] = None if test_v == "-" else int(test_v.replace(",", ""))
                row[f"{p}_pos"] = None if pos_v == "-" else int(pos_v.replace(",", ""))
            rows.append(row)
        else:
            if skip_next_orphan is not None and l.strip() == skip_next_orphan:
                skip_next_orphan = None
                continue
            skip_next_orphan = None
            pending_name = (pending_name + " " + l).strip()

    if not header_seen_provinces or len(rows) < 5:
        return None
    return {"rows": rows}

def _reconstruct_header(table_obj, n_header_rows=2):
    data = table_obj.extract()
    header_rows = data[:n_header_rows]
    ncols = max(len(r) for r in header_rows)
    combined = []
    for i in range(ncols):
        parts = [_norm(r[i]) for r in header_rows if i < len(r) and r[i]]
        combined.append(" ".join(parts).strip())
    return [c for c in combined if c]


def extract_district_disease_table(page):
    """Extracts one province's district x disease case table. Returns None if this
    page doesn't contain one. Province name comes from the table caption when present;
    callers should also cross-reference district names against a known province
    crosswalk, since captions aren't always detected cleanly."""
    text = page.extract_text() or ""
    cap_match = re.search(
        r"Table\s*(\d+):\s*District\s*wise\s*distribution.*?Week\s*(\d+),\s*([A-Za-z &]+)\.", text)
    caption = cap_match.groups() if cap_match else None

    tabs = page.find_tables()
    if not tabs:
        return None
    header = _reconstruct_header(tabs[0])
    if not header or header[0].lower() != "districts":
        return None
    diseases = header[1:]
    n = len(diseases)

    num_pat = re.compile(r"[\d,]+")
    rows = []
    for line in text.split("\n"):
        line = line.strip()
        nums = num_pat.findall(line)
        if len(nums) != n:
            continue
        first_num_pos = line.find(nums[0])
        name = line[:first_num_pos].strip()
        if not name or any(ch.isdigit() for ch in name):
            continue
        if name.lower() in ("total", "totals", "province total", "grand total"):
            continue  # per-province summary/footer row, not a real district
        if name.lower() in ("sindh labs", "labs", "laboratory"):
            continue  # lab/testing facility row, not a district
        try:
            values = {diseases[i]: int(nums[i].replace(",", "")) for i in range(n)}
        except ValueError:
            continue
        rows.append({"district": name, **values})

    if len(rows) < 5:
        return None
    return {"caption": caption, "diseases": diseases, "rows": rows}


def extract_all_district_disease_tables(pdf):
    """Walks every page of the PDF and returns all district-disease tables found."""
    results = []
    for pno, page in enumerate(pdf.pages):
        try:
            res = extract_district_disease_table(page)
        except Exception:
            res = None
        if res:
            results.append({"page": pno + 1, **res})
    return results


# ---------- Legacy (2021/2022) district-disease tables: NOT implemented ----------
#
# DELIBERATELY DEFERRED, not just unattempted -- reasoning documented here so
# a future session (or contributor) doesn't have to re-derive it:
#
# 2021/2022 bulletins DO have a district-level disease table per province
# (e.g. "Table 2: District wise distribution of most frequently reported
# cases during week 25, Sindh"), structurally similar to Table 1 (diseases as
# rows, entities as columns) rather than the modern format's (districts as
# rows, diseases as columns).
#
# Two real problems, not just inconvenience:
#   1. Only covers a CURATED "top N" subset of districts per province per
#      week (e.g. KP week 16/2022 shows 10 of KP's 37 districts) -- not
#      comprehensive, so even a working extractor wouldn't support a fair
#      district-to-district comparison the way 2023+ data does.
#   2. Multi-word district names in the header ("Karachi East", "Naushero
#      Feroze") wrap across lines with real column-order ambiguity. The
#      grid-mode-header + text-mode-data technique that solved this exact
#      problem for the modern format doesn't transfer cleanly here --
#      pdfplumber's table detection picks up surrounding chart/paragraph
#      noise on these pages (70x17 "table" detected where the real one is
#      ~12x10). Getting a column order wrong here would silently attribute
#      one district's case counts to a different district -- the same class
#      of bug as the North/South Waziristan mismatch caught in
#      district_match.py, but with no equivalent safeguard yet designed.
#
# Given (1) caps the value even if solved, and (2) carries real silent-
# corruption risk without a safeguard as clear as the directional-pair guard,
# this was deferred rather than force-fixed. If revisited: the safeguard
# would need to be an explicit district-name-order verification (e.g. cross-
# checking recovered column headers against known per-province district
# lists via the crosswalk) before trusting any column mapping.
