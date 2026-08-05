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
    report Kech') that can otherwise satisfy a loose row regex by accident."""
    words = candidate.strip().split()
    if not words or len(words) > max_words:
        return False
    if any(w.lower() in _NARRATIVE_WORDS for w in words):
        return False
    return True


# ---------- Table 6: district compliance ----------

PAT_COMPLIANCE_MODERN = re.compile(r"^(.*?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d{1,3})%\s*$")
PAT_COMPLIANCE_LEGACY = re.compile(r"^(.*?)\s+(\d[\d,]*)/(\d[\d,]*)\s+(\d[\d,]*)\s*\((\d{1,3})%\)\s*$")

def _rows_from_page_compliance(text):
    modern, legacy = [], []
    for line in text.split("\n"):
        line = line.strip()
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

def extract_province_summary(page):
    """Extracts the Diseases x Province national summary table (Table 1)."""
    text = page.extract_text() or ""
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    header_idx = None
    for i, l in enumerate(lines):
        if l.startswith("Diseases") and "Total" in l:
            header_idx = i
            break
    if header_idx is None:
        return None
    provinces = lines[header_idx].split()[1:]
    n = len(provinces)

    val_pat = re.compile(r"^(?:NR|[\d,]+)$")
    rows = []
    pending_name = ""
    for l in lines[header_idx + 1:]:
        if l.startswith("Page"):
            continue
        tokens = l.split()
        if len(tokens) >= n and all(val_pat.match(t) for t in tokens[-n:]):
            name_tokens = tokens[:-n]
            name = (pending_name + " " + " ".join(name_tokens)).strip()
            pending_name = ""
            if not name:
                continue
            vals = tokens[-n:]
            row = {"disease": name}
            for p, v in zip(provinces, vals):
                row[p] = None if v == "NR" else int(v.replace(",", ""))
            rows.append(row)
        else:
            pending_name = (pending_name + " " + l).strip()
    return {"provinces": provinces, "rows": rows}


# ---------- District-level disease case tables (Table 2/3/4...) ----------

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
