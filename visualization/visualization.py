"""
F1 Analytics Dashboard
======================
Streamlit visualization layer — reads exclusively from MongoDB (f1_nosql).

Matches the exact migration script schema:
  - db: f1_nosql
  - races collection fields:
      raceid, year, round, name, date, time
      circuit (embedded): circuitId, circuitRef, name, country, lat, lng
      results (embedded): raceid, driverid, driverName, driverNationality,
                          constructorid, constructorName, constructorNationality,
                          grid, position, points, fastestLapMs, fastestLapTime
      derived: totalDrivers, winnerDriverId, winnerDriverName,
               winnerConstructorId, winnerConstructorName
  - drivers:      driverid, driverref, forname, surname, nationality, dob
  - constructors: constructorid, constructorref, name, nationality
  - lap_times:    raceid, driverid, lap, position, milliseconds

Visualizations:
  1. Constructor Championship Points by Year  → results.points (embedded)
  2. Top N Drivers by Race Wins               → winnerDriverId / winnerDriverName (derived)
  3. Grid vs Finish Position Heatmap          → results.grid / results.position (embedded)
  4. Avg Fastest Lap Time by Circuit          → results.fastestLapMs (derived)
  5. Drivers per Race Over the Years          → totalDrivers (derived)
  6. Win Rate by Driver Nationality           → winnerDriverId → drivers.nationality

Run:
    pip install streamlit plotly pymongo pandas
    streamlit run f1_dashboard.py
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from pymongo import MongoClient

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="F1 Analytics",
    page_icon="🏎️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS — dark F1 racing theme
# ---------------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;600;700&family=Barlow:wght@300;400;500&display=swap');

html, body, [class*="css"] {
    font-family: 'Barlow', sans-serif;
    background-color: #0d0d0d;
    color: #f0f0f0;
}
h1, h2, h3 { font-family: 'Barlow Condensed', sans-serif; letter-spacing: 0.04em; }
.block-container { padding-top: 1.5rem; }

[data-testid="metric-container"] {
    background: #1a1a1a;
    border: 1px solid #2a2a2a;
    border-left: 3px solid #e10600;
    padding: 1rem;
    border-radius: 4px;
}
[data-testid="stMetricValue"]  { color: #e10600 !important; font-family: 'Barlow Condensed', sans-serif; font-size: 2rem !important; }
[data-testid="stMetricLabel"]  { color: #888 !important; font-size: 0.75rem !important; text-transform: uppercase; letter-spacing: 0.1em; }

[data-testid="stSidebar"]               { background-color: #111 !important; border-right: 1px solid #222; }
[data-testid="stSidebar"] .block-container { padding-top: 2rem; }

.stTabs [data-baseweb="tab-list"]  { background: #111; border-bottom: 1px solid #222; }
.stTabs [data-baseweb="tab"]       { color: #888; font-family: 'Barlow Condensed', sans-serif; font-size: 1rem; letter-spacing: 0.05em; }
.stTabs [aria-selected="true"]     { color: #e10600 !important; border-bottom: 2px solid #e10600; }

.derived-tag {
    display: inline-block;
    background: #1a0000;
    color: #e10600;
    border: 1px solid #3a0000;
    border-radius: 3px;
    font-size: 0.7rem;
    font-family: 'Barlow Condensed', monospace;
    letter-spacing: 0.08em;
    padding: 2px 7px;
    margin-bottom: 0.6rem;
}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# MongoDB connection
# ---------------------------------------------------------------------------
MONGO_URI = "mongodb://localhost:27017"
MONGO_DB  = "f1_nosql"   # matches your migration script

@st.cache_resource
def get_db():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
    return client[MONGO_DB]

try:
    db = get_db()
    db.command("ping")
except Exception as e:
    st.error(f"❌ Cannot connect to MongoDB at `{MONGO_URI}` — {e}")
    st.stop()

# ---------------------------------------------------------------------------
# Plotly theme helpers
# ---------------------------------------------------------------------------
PLOT_BG  = "#0d0d0d"
F1_RED   = "#e10600"
GRID_CLR = "#1e1e1e"
FONT_CLR = "#c0c0c0"
F1_COLORS = px.colors.qualitative.Bold

def base_layout(fig, title=""):
    fig.update_layout(
        title=dict(text=title, font=dict(family="Barlow Condensed", size=20, color="#f0f0f0")),
        plot_bgcolor=PLOT_BG, paper_bgcolor=PLOT_BG,
        font=dict(family="Barlow", color=FONT_CLR),
        legend=dict(bgcolor="#111", bordercolor="#222", borderwidth=1),
        margin=dict(l=40, r=20, t=50, b=40),
    )
    fig.update_xaxes(gridcolor=GRID_CLR, zerolinecolor=GRID_CLR)
    fig.update_yaxes(gridcolor=GRID_CLR, zerolinecolor=GRID_CLR)
    return fig

def derived_tag(field):
    st.markdown(f"<div class='derived-tag'>derived field: {field}</div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Data loaders (cached)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_races():
    docs = list(db.races.find({}, {
        "raceid":1,"year":1,"round":1,"name":1,
        "totalDrivers":1,
        "winnerDriverId":1,"winnerDriverName":1,
        "winnerConstructorId":1,"winnerConstructorName":1,
        "circuit.name":1,"circuit.country":1,
    }))
    return pd.DataFrame(docs)

@st.cache_data(ttl=300)
def load_drivers():
    docs = list(db.drivers.find({}, {"driverid":1,"forname":1,"surname":1,"nationality":1}))
    df = pd.DataFrame(docs)
    df["fullname"] = df["forname"].fillna("") + " " + df["surname"].fillna("")
    return df

@st.cache_data(ttl=300)
def load_constructors():
    docs = list(db.constructors.find({}, {"constructorid":1,"name":1,"nationality":1}))
    return pd.DataFrame(docs)

@st.cache_data(ttl=300)
def load_results_flat():
    """
    Unwind the embedded results array from the races collection.
    Uses fastestLapMs and fastestLapTime which were derived during migration.
    """
    pipeline = [
        {"$unwind": "$results"},
        {"$project": {
            "year":              "$year",
            "circuit_name":      "$circuit.name",
            "circuit_country":   "$circuit.country",
            "driverid":          "$results.driverid",
            "driverName":        "$results.driverName",          # denormalized during migration
            "driverNationality": "$results.driverNationality",   # denormalized during migration
            "constructorid":     "$results.constructorid",
            "constructorName":   "$results.constructorName",     # denormalized during migration
            "grid":              "$results.grid",
            "position":          "$results.position",
            "points":            "$results.points",
            "fastestLapMs":      "$results.fastestLapMs",        # derived during migration
            "fastestLapTime":    "$results.fastestLapTime",      # derived during migration
        }}
    ]
    return pd.DataFrame(list(db.races.aggregate(pipeline)))

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
races_df = load_races()

st.sidebar.markdown("## 🏎️ F1 ANALYTICS")
st.sidebar.markdown("---")

years = sorted(races_df["year"].dropna().unique().tolist())
year_range = st.sidebar.select_slider(
    "Season Range", options=years, value=(min(years), max(years))
)

st.sidebar.markdown("---")
st.sidebar.markdown(
    "<div style='color:#555;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.1em'>"
    f"MongoDB · {MONGO_DB}</div>", unsafe_allow_html=True
)

filtered_races = races_df[
    (races_df["year"] >= year_range[0]) & (races_df["year"] <= year_range[1])
]

# ---------------------------------------------------------------------------
# Header + KPIs
# ---------------------------------------------------------------------------
st.markdown("# 🏁 FORMULA 1 — ANALYTICS DASHBOARD")
st.markdown(
    f"<div style='color:#555;font-family:Barlow Condensed;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:1rem'>"
    f"Seasons {year_range[0]} – {year_range[1]}</div>", unsafe_allow_html=True
)

drivers_df      = load_drivers()
constructors_df = load_constructors()

k1, k2, k3, k4 = st.columns(4)
k1.metric("Races",        f"{len(filtered_races):,}")
k2.metric("Seasons",      f"{filtered_races['year'].nunique()}")
k3.metric("Drivers",      f"{len(drivers_df):,}")
k4.metric("Constructors", f"{len(constructors_df):,}")

st.markdown("---")

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🏆  Constructor Points",
    "👤  Driver Wins",
    "📊  Grid vs Finish",
    "⏱️  Fastest Laps",
    "👥  Drivers per Race",
    "🌍  Win Nationality",
])

# ===========================================================================
# TAB 1 — Constructor Championship Points by Year
# Derived from: embedded results.points (aggregated per constructor per year)
# ===========================================================================
with tab1:
    st.markdown("### Constructor Championship Points by Year")
    derived_tag("results.points  ·  embedded in races collection")

    results_flat = load_results_flat()
    res_f = results_flat[
        (results_flat["year"] >= year_range[0]) &
        (results_flat["year"] <= year_range[1])
    ]

    con_pts = (
        res_f.groupby(["year", "constructorName"])["points"]
        .sum().reset_index()
    )

    top_n = st.slider("Show top N constructors (by total points)", 3, 15, 6, key="con_top")
    top_names = (
        con_pts.groupby("constructorName")["points"]
        .sum().nlargest(top_n).index.tolist()
    )
    con_pts_top = con_pts[con_pts["constructorName"].isin(top_names)]

    fig1 = px.line(
        con_pts_top, x="year", y="points", color="constructorName",
        markers=True, color_discrete_sequence=F1_COLORS,
        labels={"constructorName": "Constructor", "points": "Points", "year": "Season"},
    )
    base_layout(fig1)
    fig1.update_traces(line=dict(width=2.5), marker=dict(size=6))
    st.plotly_chart(fig1, use_container_width=True)

# ===========================================================================
# TAB 2 — Top N Drivers by Race Wins
# Derived from: winnerDriverId + winnerDriverName (computed during migration)
# ===========================================================================
with tab2:
    st.markdown("### Top Drivers by Race Wins")
    derived_tag("winnerDriverId  ·  winnerDriverName  ·  set to position=1 per race during migration")

    top_n_drv = st.slider("Number of drivers", 5, 20, 10, key="drv_top")

    win_counts = (
        filtered_races[["winnerDriverId", "winnerDriverName"]]
        .dropna(subset=["winnerDriverId"])
        .groupby(["winnerDriverId", "winnerDriverName"])
        .size().reset_index(name="wins")
        .sort_values("wins", ascending=False)
        .head(top_n_drv)
        .sort_values("wins", ascending=True)
    )

    fig2 = px.bar(
        win_counts, x="wins", y="winnerDriverName",
        orientation="h",
        color="wins",
        color_continuous_scale=[[0, "#330000"], [1, F1_RED]],
        text="wins",
        labels={"winnerDriverName": "", "wins": "Race Wins"},
    )
    base_layout(fig2)
    fig2.update_traces(textposition="outside", textfont=dict(color="#f0f0f0"))
    fig2.update_layout(coloraxis_showscale=False)
    st.plotly_chart(fig2, use_container_width=True)

# ===========================================================================
# TAB 3 — Grid Position vs Finish Position Heatmap
# From: embedded results.grid and results.position
# ===========================================================================
with tab3:
    st.markdown("### Starting Grid vs Finishing Position")
    derived_tag("results.grid  ·  results.position  ·  embedded in races collection")

    results_flat = load_results_flat()
    res_gp = results_flat[
        (results_flat["year"] >= year_range[0]) &
        (results_flat["year"] <= year_range[1]) &
        results_flat["grid"].notna() &
        results_flat["position"].notna()
    ].copy()
    res_gp["grid"]     = res_gp["grid"].astype(int)
    res_gp["position"] = res_gp["position"].astype(int)

    max_pos = st.slider("Max grid / finish position", 5, 25, 15, key="heatmap_max")
    res_gp  = res_gp[(res_gp["grid"] <= max_pos) & (res_gp["position"] <= max_pos)]

    hmap = (
        res_gp.groupby(["grid", "position"]).size()
        .reset_index(name="count")
        .pivot(index="position", columns="grid", values="count")
        .fillna(0)
    )

    fig3 = go.Figure(go.Heatmap(
        z=hmap.values,
        x=[f"P{c}" for c in hmap.columns],
        y=[f"P{r}" for r in hmap.index],
        colorscale=[[0, "#0d0d0d"], [0.3, "#330000"], [1, F1_RED]],
        hovertemplate="Grid: %{x}<br>Finish: %{y}<br>Count: %{z}<extra></extra>",
    ))
    base_layout(fig3)
    fig3.update_layout(
        xaxis_title="Starting Grid Position",
        yaxis_title="Finishing Position",
        height=520,
    )
    st.plotly_chart(fig3, use_container_width=True)
    st.caption("Bright diagonal = drivers finish where they start. Top-left = gained positions from the back.")

# ===========================================================================
# TAB 4 — Average Fastest Lap Time by Circuit
# Derived from: results.fastestLapMs (MIN milliseconds per driver per race,
#               computed during migration and embedded in each result)
# ===========================================================================
with tab4:
    st.markdown("### Average Fastest Lap Time by Circuit")
    derived_tag("results.fastestLapMs  ·  MIN(milliseconds) per driver per race — computed during migration")

    results_flat = load_results_flat()
    fl = results_flat[
        (results_flat["year"] >= year_range[0]) &
        (results_flat["year"] <= year_range[1]) &
        results_flat["fastestLapMs"].notna()
    ].copy()
    fl["fastestLapMs"] = fl["fastestLapMs"].astype(float)
    fl["fl_sec"]       = fl["fastestLapMs"] / 1000

    avg_fl = (
        fl.groupby("circuit_name")["fl_sec"]
        .mean().reset_index()
        .rename(columns={"fl_sec": "avg_sec"})
        .sort_values("avg_sec")
    )

    top_n_cir = st.slider("Number of circuits", 5, 30, 15, key="circuit_top")
    avg_fl_top = avg_fl.head(top_n_cir).copy()

    def fmt_sec(s):
        m = int(s // 60); sec = s - m * 60
        return f"{m}:{sec:06.3f}"

    avg_fl_top["label"] = avg_fl_top["avg_sec"].apply(fmt_sec)

    fig4 = px.bar(
        avg_fl_top, x="avg_sec", y="circuit_name",
        orientation="h",
        color="avg_sec",
        color_continuous_scale=[[0, F1_RED], [1, "#330000"]],
        text="label",
        labels={"circuit_name": "", "avg_sec": "Avg Fastest Lap (s)"},
    )
    base_layout(fig4)
    fig4.update_traces(textposition="outside", textfont=dict(color="#f0f0f0", size=11))
    fig4.update_layout(coloraxis_showscale=False, height=max(400, top_n_cir * 28))
    st.plotly_chart(fig4, use_container_width=True)

# ===========================================================================
# TAB 5 — Drivers per Race Over the Years
# Derived from: totalDrivers (COUNT of embedded results per race,
#               computed during migration)
# ===========================================================================
with tab5:
    st.markdown("### Average Drivers per Race by Season")
    derived_tag("totalDrivers  ·  COUNT(results) per race — computed during migration")

    avg_drivers = (
        filtered_races.groupby("year")["totalDrivers"]
        .agg(avg="mean", min="min", max="max")
        .reset_index()
    )

    fig5 = go.Figure()
    fig5.add_trace(go.Scatter(
        x=pd.concat([avg_drivers["year"], avg_drivers["year"][::-1]]),
        y=pd.concat([avg_drivers["max"], avg_drivers["min"][::-1]]),
        fill="toself", fillcolor="rgba(225,6,0,0.10)",
        line=dict(color="rgba(0,0,0,0)"),
        name="Min/Max range", hoverinfo="skip",
    ))
    fig5.add_trace(go.Scatter(
        x=avg_drivers["year"], y=avg_drivers["avg"],
        mode="lines+markers",
        line=dict(color=F1_RED, width=3),
        marker=dict(size=7, color=F1_RED),
        name="Season average",
        hovertemplate="Season: %{x}<br>Avg drivers: %{y:.1f}<extra></extra>",
    ))
    base_layout(fig5)
    fig5.update_layout(
        xaxis_title="Season", yaxis_title="Drivers per Race",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig5, use_container_width=True)
    st.caption("Shaded band = min/max range of drivers in any single race that season.")

# ===========================================================================
# TAB 6 — Race Wins by Driver Nationality
# Derived from: winnerDriverId (migration-derived) joined to
#               results.driverNationality (denormalized into races.results
#               during migration via JOIN drivers)
# ===========================================================================
with tab6:
    st.markdown("### Race Wins by Driver Nationality")
    derived_tag("winnerDriverId (derived)  ·  results.driverNationality (denormalized during migration)")

    # driverNationality is already denormalized inside each embedded result —
    # we just need to match the winner's driverid to their nationality
    wins_nat = (
        filtered_races[["winnerDriverId"]]
        .dropna()
        .merge(
            drivers_df[["driverid", "nationality"]],
            left_on="winnerDriverId", right_on="driverid", how="left"
        )
        .groupby("nationality").size()
        .reset_index(name="wins")
        .sort_values("wins", ascending=False)
    )

    col_bar, col_pie = st.columns([3, 2])

    with col_bar:
        top_nat = st.slider("Top nationalities", 5, 20, 10, key="nat_top")
        wins_top = wins_nat.head(top_nat).sort_values("wins", ascending=True)

        fig6a = px.bar(
            wins_top, x="wins", y="nationality",
            orientation="h",
            color="wins",
            color_continuous_scale=[[0, "#330000"], [1, F1_RED]],
            text="wins",
            labels={"nationality": "", "wins": "Total Race Wins"},
        )
        base_layout(fig6a)
        fig6a.update_traces(textposition="outside", textfont=dict(color="#f0f0f0"))
        fig6a.update_layout(coloraxis_showscale=False)
        st.plotly_chart(fig6a, use_container_width=True)

    with col_pie:
        threshold = wins_nat["wins"].sum() * 0.02
        pie_df = wins_nat.copy()
        pie_df.loc[pie_df["wins"] < threshold, "nationality"] = "Other"
        pie_df = pie_df.groupby("nationality")["wins"].sum().reset_index()

        fig6b = px.pie(
            pie_df, values="wins", names="nationality",
            color_discrete_sequence=F1_COLORS,
            hole=0.45,
        )
        base_layout(fig6b)
        fig6b.update_traces(
            textposition="outside", textinfo="label+percent",
            textfont_size=11,
        )
        fig6b.update_layout(showlegend=False, margin=dict(l=20,r=20,t=40,b=20))
        st.plotly_chart(fig6b, use_container_width=True)
