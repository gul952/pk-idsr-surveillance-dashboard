"""
Pakistan IDSR Surveillance Dashboard -- Streamlit entrypoint.

Reads the four consolidated parquet tables produced by parser/build_dataset.py
(and kept current by the weekly GitHub Action), plus the district boundary
GeoJSON (data/processed/pakistan_districts.geojson, from HDX COD-AB) and the
geometry join table (parser/reference/district_geometry_join.csv) that maps
case-data district names to HDX polygon names -- these differ in a handful of
real ways (spelling variants, and one genuine structural mismatch: HDX has a
single unified "Kurram" polygon while NIH case data reports it split into
"Lower & Central Kurram" / "Upper Kurram", so those two rows get summed onto
one shape).
"""
import os
import json
import csv
import difflib
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "processed")
REF_DIR = os.path.join(os.path.dirname(__file__), "..", "parser", "reference")

st.set_page_config(page_title="Pakistan IDSR Surveillance", layout="wide")


@st.cache_data(ttl=3600)
def load_data():
    disease = pd.read_parquet(os.path.join(DATA_DIR, "district_disease_cases.parquet"))
    compliance = pd.read_parquet(os.path.join(DATA_DIR, "district_compliance.parquet"))
    province = pd.read_parquet(os.path.join(DATA_DIR, "province_summary.parquet"))
    for df in (disease, compliance, province):
        df["week_start_date"] = pd.to_datetime(df["week_start_date"])
        df["week_key"] = df["year"] * 100 + df["week"]
    return disease, compliance, province


@st.cache_data(ttl=86400)
def load_geometry():
    with open(os.path.join(DATA_DIR, "pakistan_districts.geojson")) as f:
        geojson = json.load(f)
    with open(os.path.join(REF_DIR, "district_geometry_join.csv")) as f:
        join_rows = list(csv.DictReader(f))
    # case_data_district_raw -> hdx polygon name (many-to-one for the Kurram case)
    case_to_hdx = {r["case_data_district_raw"]: r["hdx_adm2_name"] for r in join_rows}
    hdx_names = sorted(set(r["hdx_adm2_name"] for r in join_rows))
    return geojson, case_to_hdx, hdx_names


def resolve_to_hdx(raw_name, case_to_hdx, hdx_names, cutoff=0.72):
    """Same exact -> fuzzy strategy as parser/district_match.py, applied to
    the (different, and differently-abbreviated) problem of matching a
    case-data district name to an HDX polygon name. Reuses the directional-
    pair guard for the same reason: 'North'/'South' etc. must never fuzzy-
    cross.

    KNOWN LIMITATION: a bare 'Waziristan' with no direction at all (rare,
    seen once across the full sample) cannot be disambiguated from the text
    alone and currently resolves to South Waziristan arbitrarily (first
    fuzzy candidate). Flagged here rather than silently accepted."""
    if raw_name in case_to_hdx:
        return case_to_hdx[raw_name]
    directional = {"north", "south", "upper", "lower", "east", "west", "central"}
    raw_dirs = {w for w in directional if w in raw_name.lower()}
    candidates = difflib.get_close_matches(raw_name, hdx_names, n=3, cutoff=cutoff)
    for cand in candidates:
        cand_dirs = {w for w in directional if w in cand.lower()}
        if raw_dirs and cand_dirs and raw_dirs != cand_dirs:
            continue
        return cand
    return None


disease_df, compliance_df, province_df = load_data()

st.title("Pakistan IDSR Weekly Surveillance")
st.caption(
    "Built from NIH's weekly Field Epidemiology & Disease Surveillance Division (FE&DSD) "
    "bulletins. Not an official NIH product."
)

# ---------------- sidebar controls ----------------
all_diseases = sorted(disease_df["disease"].dropna().unique())
default_disease = "Malaria" if "Malaria" in all_diseases else all_diseases[0]
disease_pick = st.sidebar.selectbox("Disease", all_diseases, index=all_diseases.index(default_disease))

all_provinces = sorted(disease_df["province"].dropna().unique())
province_pick = st.sidebar.multiselect("Province (blank = all)", all_provinces, default=[])

week_keys = sorted(disease_df["week_key"].unique())
if len(week_keys) > 1:
    wk_range = st.sidebar.select_slider(
        "Week range", options=week_keys,
        value=(week_keys[0], week_keys[-1]),
        format_func=lambda k: f"W{k % 100}-{k // 100}",
    )
else:
    wk_range = (week_keys[0], week_keys[0])

# ---------------- filter ----------------
d = disease_df[disease_df["disease"] == disease_pick]
d = d[(d["week_key"] >= wk_range[0]) & (d["week_key"] <= wk_range[1])]
if province_pick:
    d = d[d["province"].isin(province_pick)]

c = compliance_df[(compliance_df["week_key"] >= wk_range[0]) & (compliance_df["week_key"] <= wk_range[1])]
if province_pick:
    c = c[c["province"].isin(province_pick)]

# ---------------- KPI row ----------------
latest_week = d["week_key"].max() if len(d) else None
prev_week_candidates = sorted([w for w in d["week_key"].unique() if w < latest_week]) if latest_week else []
prev_week = prev_week_candidates[-1] if prev_week_candidates else None

latest_total = d[d["week_key"] == latest_week]["suspected_cases"].sum() if latest_week is not None else 0
prev_total = d[d["week_key"] == prev_week]["suspected_cases"].sum() if prev_week is not None else None
wow_pct = ((latest_total - prev_total) / prev_total * 100) if prev_total else None

avg_compliance = c["compliance_rate"].mean() if len(c) else None

col1, col2, col3 = st.columns(3)
col1.metric(f"{disease_pick} cases (latest week in range)", f"{latest_total:,.0f}" if latest_week is not None else "n/a",
            delta=f"{wow_pct:+.1f}% vs prior week" if wow_pct is not None else None)
col2.metric("Districts reporting this disease", d[d['week_key']==latest_week]['district_raw'].nunique() if latest_week is not None else "n/a")
col3.metric("Avg district compliance rate (selection)", f"{avg_compliance:.0f}%" if avg_compliance is not None else "n/a")

st.divider()

# ---------------- time series ----------------
st.subheader(f"{disease_pick} -- weekly suspected cases")
ts = d.groupby("week_start_date", as_index=False)["suspected_cases"].sum().sort_values("week_start_date")
if len(ts) >= 3:
    ts["rolling_avg_4wk"] = ts["suspected_cases"].rolling(4, min_periods=1).mean()
    # simple anomaly flag: current value > rolling_avg + 2*rolling_std
    ts["rolling_std_4wk"] = ts["suspected_cases"].rolling(4, min_periods=1).std().fillna(0)
    ts["anomaly"] = ts["suspected_cases"] > (ts["rolling_avg_4wk"] + 2 * ts["rolling_std_4wk"])
else:
    ts["rolling_avg_4wk"] = ts["suspected_cases"]
    ts["anomaly"] = False

fig = go.Figure()
fig.add_trace(go.Scatter(x=ts["week_start_date"], y=ts["suspected_cases"], mode="lines+markers", name="Suspected cases"))
fig.add_trace(go.Scatter(x=ts["week_start_date"], y=ts["rolling_avg_4wk"], mode="lines", name="4-week rolling avg", line=dict(dash="dot")))
anomalies = ts[ts["anomaly"]]
if len(anomalies):
    fig.add_trace(go.Scatter(x=anomalies["week_start_date"], y=anomalies["suspected_cases"], mode="markers",
                              name="Anomaly (>2 std above rolling avg)", marker=dict(size=12, symbol="x", color="red")))
fig.update_layout(height=400, margin=dict(t=20, b=20))
st.plotly_chart(fig, use_container_width=True)
if len(ts) < 8:
    st.caption(f"Note: only {len(ts)} weeks of data in the current sample -- rolling average and anomaly "
               f"flagging get more meaningful as the archive backfills further.")

# ---------------- top districts ----------------
st.subheader(f"Top districts -- {disease_pick}, latest week in range")
if latest_week is not None:
    top = (d[d["week_key"] == latest_week]
           .groupby(["district_raw", "province"], as_index=False)["suspected_cases"].sum()
           .sort_values("suspected_cases", ascending=False).head(15))
    fig2 = px.bar(top, x="suspected_cases", y="district_raw", color="province", orientation="h")
    fig2.update_layout(height=450, yaxis=dict(autorange="reversed"), margin=dict(t=20, b=20))
    st.plotly_chart(fig2, use_container_width=True)
else:
    st.info("No data in the selected range.")

st.divider()

# ---------------- compliance ----------------
st.subheader("District IDSR reporting compliance (selection)")
if latest_week is not None and len(c):
    latest_c = c[c["week_key"] == c["week_key"].max()].copy()
    latest_c = latest_c.sort_values("compliance_rate")
    st.dataframe(
        latest_c[["province", "district_raw", "total_sites", "reported_sites", "compliance_rate"]]
        .rename(columns={"district_raw": "district"}),
        use_container_width=True, height=400,
    )
    low = latest_c[latest_c["compliance_rate"] < 50]
    if len(low):
        st.warning(f"{len(low)} districts below 50% compliance in the latest week shown: "
                   + ", ".join(low["district_raw"].head(10).tolist())
                   + (f" (+{len(low)-10} more)" if len(low) > 10 else ""))
else:
    st.info("No compliance data in the selected range.")

st.divider()

# ---------------- choropleth map ----------------
st.subheader(f"District map -- {disease_pick}, latest week in range")
geojson, case_to_hdx, hdx_names = load_geometry()
if latest_week is not None:
    map_data = d[d["week_key"] == latest_week].copy()
    map_data["hdx_district"] = map_data["district_raw"].apply(
        lambda x: resolve_to_hdx(x, case_to_hdx, hdx_names))
    map_agg = map_data.dropna(subset=["hdx_district"]).groupby("hdx_district", as_index=False)["suspected_cases"].sum()

    unmapped = map_data[map_data["hdx_district"].isna()]["district_raw"].unique().tolist()
    if unmapped:
        st.caption(f"Not shown on map (no boundary match yet): {', '.join(unmapped[:8])}"
                   + (f" +{len(unmapped)-8} more" if len(unmapped) > 8 else ""))

    fig3 = px.choropleth(
        map_agg, geojson=geojson, locations="hdx_district", featureidkey="properties.district",
        color="suspected_cases", color_continuous_scale="Reds",
        hover_name="hdx_district",
    )
    fig3.update_geos(fitbounds="locations", visible=False)
    fig3.update_layout(height=550, margin=dict(t=20, b=20, l=0, r=0))
    st.plotly_chart(fig3, use_container_width=True)
    st.caption(
        "Districts with no fill have no reported cases for this disease/week in the data -- this "
        "includes essentially all of Punjab, which is structurally absent from NIH's own bulletins "
        "(see project notes), not a gap in this dashboard's extraction."
    )
else:
    st.info("No data in the selected range.")

st.divider()

# ---------------- data completeness ----------------
st.subheader("Data completeness by province")
st.caption(
    "How many weeks each province appears in NIH's own national summary table -- not this "
    "dashboard's extraction success, but whether the province was included in the source "
    "bulletin at all that week."
)
comp_prov = province_df[province_df["province"] != "Total"].copy()
presence = (
    comp_prov.groupby(["week_key", "province"])["suspected_cases"]
    .apply(lambda s: s.notna().any())
    .unstack()
    .fillna(False)
)
presence_pct = (presence.sum() / len(presence) * 100).sort_values()

fig4 = px.bar(
    presence_pct, orientation="h",
    labels={"value": "% of weeks present in the bulletin", "province": ""},
    color=presence_pct.values, color_continuous_scale=["#d62728", "#2ca02c"],
)
fig4.update_layout(height=320, showlegend=False, coloraxis_showscale=False, margin=dict(t=20, b=20))
st.plotly_chart(fig4, use_container_width=True)

lowest = presence_pct.index[0]
st.caption(
    f"**{lowest}** appears in only {presence_pct.iloc[0]:.0f}% of weeks covered by this dataset "
    f"({int(presence.iloc[:, presence.columns.get_loc(lowest)].sum())} of {len(presence)}), against "
    f"{presence_pct.iloc[-1]:.0f}% for the most consistently-reported province. This reflects NIH's "
    "own bulletins, not a gap in this dashboard's extraction -- see the project notes for how this "
    "was verified across multiple independent tables in the source data."
)

