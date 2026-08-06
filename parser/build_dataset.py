"""
Consolidates all three per-bulletin extractors into unified long-format tables.

Output tables (one row per district/province/disease/week observation):
  - district_disease_cases : district x disease case counts, per week
  - district_compliance    : district-level IDSR reporting compliance, per week
  - province_summary       : national province x disease rollup, per week

DATE CONVENTION:
  NIH's main weekly bulletins only ever state "Week N, YYYY" -- no calendar date
  range appears in those documents. However, NIH's separate "Provincial Reports"
  series does state real calendar ranges (e.g. "week 29-2024 (15 July-21 July,
  2024)"), which was used to verify the convention: ISO-8601 week numbering
  (Monday of ISO week N) matches exactly -- confirmed via
  date.fromisocalendar(2024, 29, 1) == 2024-07-15. `week_start_date` below can
  now be treated as accurate, not merely approximate.
"""
import os
import re
import pdfplumber
import pandas as pd

from extractors import extract_compliance_table, extract_province_summary, extract_all_district_disease_tables
from district_match import match_district, _load_crosswalk

FNAME_PAT = re.compile(r"(\d{1,2})-(\d{4})")


def parse_week_year(filename):
    m = FNAME_PAT.search(filename)
    if not m:
        return None, None
    week, year = int(m.group(1)), int(m.group(2))
    if not (1 <= week <= 53 and 2015 <= year <= 2035):
        return None, None
    return week, year


def week_start_date(week, year):
    import datetime
    try:
        return datetime.date.fromisocalendar(year, week, 1)
    except ValueError:
        return None


def process_file(path, crosswalk, log):
    fname = os.path.basename(path)
    week, year = parse_week_year(fname)
    if week is None:
        log.append({"file": fname, "issue": "could not parse week/year from filename"})
        return [], [], []

    wdate = week_start_date(week, year)
    meta = {"week": week, "year": year, "week_start_date": wdate, "source_file": fname}

    disease_rows, compliance_rows, province_rows = [], [], []

    with pdfplumber.open(path) as pdf:
        # --- compliance ---
        try:
            comp = extract_compliance_table(pdf)
            for r in comp["rows"]:
                province, how = match_district(r["district"], crosswalk)
                if province is None:
                    log.append({"file": fname, "issue": f"unmatched district in compliance: {r['district']}"})
                compliance_rows.append({
                    **meta, "district_raw": r["district"], "province": province,
                    "match_method": how if province else "UNMATCHED",
                    "total_sites": r["total_sites"], "reported_sites": r["reported_sites"],
                    "compliance_rate": r["compliance_rate"],
                })
        except Exception as e:
            log.append({"file": fname, "issue": f"compliance extraction failed: {e}"})

        # --- province summary (Table 1) ---
        found_p1 = False
        for page in pdf.pages[:8]:  # Table 1 has always appeared in the first ~8 pages so far
            try:
                res = extract_province_summary(page)
            except Exception:
                res = None
            if res and len(res["rows"]) >= 10:
                found_p1 = True
                for row in res["rows"]:
                    disease = row.pop("disease")
                    for prov, val in row.items():
                        province_rows.append({**meta, "disease": disease, "province": prov, "suspected_cases": val})
                break
        if not found_p1:
            log.append({"file": fname, "issue": "province summary table (Table 1) not found"})

        # --- district-disease tables ---
        try:
            dtabs = extract_all_district_disease_tables(pdf)
            if not dtabs:
                log.append({"file": fname, "issue": "no district-disease tables found (expected for 2021/2022 legacy format)"})
            for t in dtabs:
                for r in t["rows"]:
                    district = r.pop("district")
                    province, how = match_district(district, crosswalk)
                    if province is None:
                        log.append({"file": fname, "issue": f"unmatched district in disease table: {district}"})
                    for disease, val in r.items():
                        disease_rows.append({
                            **meta, "district_raw": district, "province": province,
                            "match_method": how if province else "UNMATCHED",
                            "disease": disease, "suspected_cases": val,
                        })
        except Exception as e:
            log.append({"file": fname, "issue": f"district-disease extraction failed: {e}"})

    return disease_rows, compliance_rows, province_rows


def build_all(input_dir, output_dir):
    crosswalk = _load_crosswalk()
    all_disease, all_compliance, all_province, log = [], [], [], []

    for fname in sorted(os.listdir(input_dir)):
        if not fname.lower().endswith(".pdf"):
            continue
        d, c, p = process_file(os.path.join(input_dir, fname), crosswalk, log)
        all_disease.extend(d)
        all_compliance.extend(c)
        all_province.extend(p)

    os.makedirs(output_dir, exist_ok=True)
    df_disease = pd.DataFrame(all_disease)
    df_compliance = pd.DataFrame(all_compliance)
    df_province = pd.DataFrame(all_province)
    df_log = pd.DataFrame(log)

    df_disease.to_parquet(f"{output_dir}/district_disease_cases.parquet", index=False)
    df_compliance.to_parquet(f"{output_dir}/district_compliance.parquet", index=False)
    df_province.to_parquet(f"{output_dir}/province_summary.parquet", index=False)
    df_log.to_csv(f"{output_dir}/build_log.csv", index=False)

    return df_disease, df_compliance, df_province, df_log


if __name__ == "__main__":
    import sys
    d, c, p, log = build_all(sys.argv[1], sys.argv[2])
    print(f"district_disease_cases: {len(d)} rows")
    print(f"district_compliance: {len(c)} rows")
    print(f"province_summary: {len(p)} rows")
    print(f"log entries: {len(log)}")
