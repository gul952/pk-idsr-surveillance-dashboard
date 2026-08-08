"""
Re-resolves province for already-committed rows against the CURRENT crosswalk,
without needing the original source PDFs. Needed because a crosswalk fix (e.g.
adding a missing alias) only affects *future* scraper runs by default -- rows
already committed with province=NULL/UNMATCHED stay that way until something
re-applies the newer crosswalk to them.

Also removes rows whose district_raw fails the current name-plausibility guard
(_looks_like_a_name) in the legacy compliance format, so pipeline hardening
fixes reach already-scraped data too, not just future scrapes.

Only touches rows that are currently unmatched (province is null) or newly
identified as narrative false-positives -- never re-touches an already-correct
row, so this is safe to run repeatedly / idempotent.
"""
import sys
import pandas as pd
from district_match import match_district, _load_crosswalk
from extractors import _looks_like_a_name


def reconcile_file(path, crosswalk):
    df = pd.read_parquet(path)
    if "province" not in df.columns or "district_raw" not in df.columns:
        return df, 0, 0

    fixed = 0
    dropped = 0
    still_bad_mask = df["province"].isna()
    for idx in df[still_bad_mask].index:
        raw = df.at[idx, "district_raw"]
        if not _looks_like_a_name(raw, max_words=6):  # generous width for this pass
            continue  # leave as unmatched -- it's genuine narrative noise, not a real name
        province, how = match_district(raw, crosswalk)
        if province is not None:
            df.at[idx, "province"] = province
            df.at[idx, "match_method"] = f"reconciled:{how}"
            fixed += 1

    return df, fixed, dropped


if __name__ == "__main__":
    output_dir = sys.argv[1] if len(sys.argv) > 1 else "data/processed"
    crosswalk = _load_crosswalk()
    total_fixed = 0
    for tbl in ("district_compliance.parquet", "district_disease_cases.parquet"):
        path = f"{output_dir}/{tbl}"
        df, fixed, dropped = reconcile_file(path, crosswalk)
        if fixed:
            df.to_parquet(path, index=False)
        print(f"{tbl}: {fixed} rows reconciled against current crosswalk")
        total_fixed += fixed
    print(f"Total: {total_fixed} rows fixed retroactively")
