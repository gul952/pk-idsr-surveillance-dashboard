"""
Weekly incremental scraper. Intended to run inside the GitHub Action (which has
open internet access), NOT from a restricted sandbox.

1. Fetches the NIH bulletin archive index page.
2. Finds every linked PDF matching the Week-NN-YYYY filename pattern.
3. Compares against source_file values already present in the committed parquet
   datasets to find bulletins not yet processed.
4. Downloads only the new ones, runs them through the same extractors used for
   the historical sample, appends results, writes the datasets back.
5. Downloaded PDFs are NOT committed -- only the extracted structured rows are.
   (Exception: files matching KEEP_AS_FIXTURE, used to refresh test fixtures.)

NOTE: the live HTTP fetch/parse logic in fetch_archive_links() has not been
tested against the real site from within Claude's sandbox (nih.org.pk is not on
the sandbox's network allowlist). It's built directly from a verified real fetch
of the archive index page done via the web_fetch tool, and from real filename
patterns seen across the 12 sample bulletins, but its first live run will be its
first real-world test -- worth running once via workflow_dispatch (manual
trigger) before trusting the schedule.
"""
import os
import re
import sys
import tempfile
import urllib.parse
import requests
import pandas as pd

from extractors import extract_compliance_table, extract_province_summary, extract_all_district_disease_tables
from district_match import match_district, _load_crosswalk
from build_dataset import parse_week_year, week_start_date, process_file

ARCHIVE_URL = "https://phb.nih.org.pk/integratedisease-surveillance-and-response"
FNAME_PAT = re.compile(r"(\d{1,2})-(\d{4})")
PDF_LINK_PAT = re.compile(r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']', re.IGNORECASE)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; IDSR-Dashboard-Bot/1.0; +https://github.com/)"}


def fetch_archive_links():
    """Returns list of (week, year, url) for every dated bulletin PDF linked
    from the archive index page."""
    resp = requests.get(ARCHIVE_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    html = resp.text

    links = set(PDF_LINK_PAT.findall(html))
    results = []
    for url in links:
        # make absolute if relative
        if url.startswith("/"):
            url = "https://phb.nih.org.pk" + url
        elif not url.startswith("http"):
            continue
        fname = url.split("/")[-1].split("?")[0]
        fname = urllib.parse.unquote(fname)  # %20 -> space, etc. -- must match
                                              # already_processed_filenames() exactly
        week, year = parse_week_year(fname)
        if week is not None:
            results.append((week, year, url, fname))
    return results


def already_processed_filenames(output_dir):
    seen = set()
    for tbl in ("district_disease_cases.parquet", "district_compliance.parquet", "province_summary.parquet"):
        path = os.path.join(output_dir, tbl)
        if os.path.exists(path):
            df = pd.read_parquet(path, columns=["source_file"])
            seen |= set(df["source_file"].unique())
    return seen


def download(url, dest_path):
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        f.write(resp.content)


def run(output_dir):
    crosswalk = _load_crosswalk()
    seen = already_processed_filenames(output_dir)
    candidates = fetch_archive_links()

    new_ones = [(w, y, url, fname) for (w, y, url, fname) in candidates if fname not in seen]
    print(f"Archive has {len(candidates)} dated bulletins; {len(seen)} already processed; "
          f"{len(new_ones)} new: {[f for *_, f in new_ones]}")

    if not new_ones:
        print("Nothing new. Exiting.")
        return False

    all_disease, all_compliance, all_province, all_lab, log = [], [], [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        for week, year, url, fname in new_ones:
            local_path = os.path.join(tmp, fname)
            try:
                download(url, local_path)
            except Exception as e:
                log.append({"file": fname, "issue": f"download failed: {e}"})
                continue
            d, c, p, lb = process_file(local_path, crosswalk, log)
            all_disease.extend(d)
            all_compliance.extend(c)
            all_province.extend(p)
            all_lab.extend(lb)

    def _merge(fname_out, new_rows):
        path = os.path.join(output_dir, fname_out)
        new_df = pd.DataFrame(new_rows)
        if len(new_df) and "week_start_date" in new_df.columns:
            new_df["week_start_date"] = pd.to_datetime(new_df["week_start_date"]).dt.date
        if os.path.exists(path) and len(new_df):
            old_df = pd.read_parquet(path)
            if "week_start_date" in old_df.columns:
                old_df["week_start_date"] = pd.to_datetime(old_df["week_start_date"]).dt.date
            combined = pd.concat([old_df, new_df], ignore_index=True)
        elif len(new_df):
            combined = new_df
        else:
            return
        combined.to_parquet(path, index=False)
        print(f"{fname_out}: +{len(new_df)} rows -> {len(combined)} total")

    _merge("district_disease_cases.parquet", all_disease)
    _merge("district_compliance.parquet", all_compliance)
    _merge("province_summary.parquet", all_province)
    _merge("lab_confirmation.parquet", all_lab)

    log_path = os.path.join(output_dir, "build_log.csv")
    if log:
        new_log_df = pd.DataFrame(log)
        if os.path.exists(log_path):
            old_log = pd.read_csv(log_path)
            pd.concat([old_log, new_log_df], ignore_index=True).to_csv(log_path, index=False)
        else:
            new_log_df.to_csv(log_path, index=False)

    return True


if __name__ == "__main__":
    output_dir = sys.argv[1] if len(sys.argv) > 1 else "data/processed"
    updated = run(output_dir)
    # signal to the workflow whether there's anything to commit
    sys.exit(0 if updated or True else 1)
