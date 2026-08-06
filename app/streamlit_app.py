"""
Pakistan IDSR Surveillance Dashboard -- Streamlit entrypoint.

Reads the three consolidated parquet tables produced by parser/build_dataset.py
(and kept current by the weekly GitHub Action). No district boundary/geometry
data yet -- the choropleth map view is a placeholder until that's sourced, since
it needs actual polygon geometries, not just district names.
"""
import os
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "processed")

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

st.caption("Choropleth map view: pending district boundary geometry data (not yet sourced).")
