"""
app.py  —  Plant Demand Dashboard
Run:  streamlit run app.py
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("plant_data")
DATA_DIR.mkdir(exist_ok=True)

# Plant definitions
# Each entry: (plant_id, display_name, column_in_template, group, unit)
PLANTS = [
    # Generation plants
    ("GEN_80MW",   "80MW Generation",       "80MW GENERATION",      "Generation", "MW"),
    ("GEN_43MW",   "43MW Generation",        "43MW GENERATION",      "Generation", "MW"),
    ("GEN_Solar",  "Solar Generation",       "SOLAR NET GENERATION", "Generation", "MW"),
    # Load plants
    ("WLL_WLL",    "WLL",                    "WLL",                  "Load",       "MW"),
    ("WLL_WHSL",   "WHSL",                   "WHSL",                 "Load",       "MW"),
    ("WCL_PIPE",   "WCL Pipe Division",      "WCL PIPE DIVISION",    "Load",       "MW"),
    ("WCL_STEEL",  "WCL Steel Division",     "WCL STEEL DIVISION",   "Load",       "MW"),
    ("WCL_ATSPL",  "ATSPL",                  "ATSPL",                "Load",       "MW"),
    ("WCL_WDIPL",  "WDIPL",                  "WDIPL",                "Load",       "MW"),
    ("WCL_WASCO",  "WASCO",                  "WASCO",                "Load",       "MW"),
]

PLANT_BY_ID = {p[0]: p for p in PLANTS}

# Credentials: plant_id → hashed password
# Default password for each plant is the plant_id lowercase (e.g. "gen_80mw")
# Change these hashes to set custom passwords
def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

DEFAULT_CREDENTIALS = {p[0]: _hash(p[0].lower()) for p in PLANTS}

CREDENTIALS_FILE = DATA_DIR / "credentials.json"
if not CREDENTIALS_FILE.exists():
    CREDENTIALS_FILE.write_text(json.dumps(DEFAULT_CREDENTIALS, indent=2))

def load_credentials() -> dict:
    return json.loads(CREDENTIALS_FILE.read_text())

def verify_password(plant_id: str, password: str) -> bool:
    creds = load_credentials()
    return creds.get(plant_id) == _hash(password)

# ─────────────────────────────────────────────────────────────────────────────
# DATA STORAGE
# ─────────────────────────────────────────────────────────────────────────────

def data_file(plant_id: str) -> Path:
    return DATA_DIR / f"{plant_id}.csv"

def save_data(plant_id: str, df: pd.DataFrame) -> None:
    """Append new data; if same date exists, overwrite those rows."""
    path = data_file(plant_id)
    df = df.copy()
    df["plant_id"] = plant_id

    if path.exists():
        existing = pd.read_csv(path, parse_dates=["Date"])
        # Remove rows for dates in new upload, then append
        new_dates = df["Date"].unique()
        existing = existing[~existing["Date"].isin(new_dates)]
        combined = pd.concat([existing, df], ignore_index=True)
    else:
        combined = df

    combined.sort_values(["Date", "Time Block"], inplace=True)
    combined.to_csv(path, index=False)

def load_data(plant_id: str) -> Optional[pd.DataFrame]:
    path = data_file(plant_id)
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["Date"])
    df.sort_values(["Date", "Time Block"], inplace=True)
    return df

def load_last_entry(plant_id: str) -> Optional[pd.DataFrame]:
    """Return the most recent date's data for a plant."""
    df = load_data(plant_id)
    if df is None or df.empty:
        return None
    last_date = df["Date"].max()
    return df[df["Date"] == last_date].copy()

# ─────────────────────────────────────────────────────────────────────────────
# EXCEL PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_upload(uploaded_file, plant_id: str) -> tuple[Optional[pd.DataFrame], str]:
    """
    Parse uploaded Excel/CSV.
    Accepts two formats:
      1. Simple 3-col: Date | Time Block | <value_col>
      2. Full template with all columns
    Returns (dataframe, error_message)
    """
    _, display, col_header, _, unit = PLANT_BY_ID[plant_id]

    try:
        if uploaded_file.name.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file, header=0)

        df.columns = [str(c).strip() for c in df.columns]

        # Find value column — exact match or partial
        value_col = None
        for c in df.columns:
            if c.upper() == col_header.upper():
                value_col = c
                break
        if value_col is None:
            # Try partial match
            for c in df.columns:
                if col_header.upper() in c.upper() or c.upper() in col_header.upper():
                    value_col = c
                    break
        if value_col is None:
            return None, f"Could not find column '{col_header}' in uploaded file. Columns found: {list(df.columns)}"

        # Find Date column
        date_col = None
        for c in df.columns:
            if "date" in c.lower():
                date_col = c
                break
        if date_col is None:
            return None, "Could not find 'Date' column in uploaded file."

        # Find Time Block column
        tb_col = None
        for c in df.columns:
            if "time" in c.lower() or "block" in c.lower():
                tb_col = c
                break
        if tb_col is None:
            return None, "Could not find 'Time Block' column in uploaded file."

        result = pd.DataFrame({
            "Date": pd.to_datetime(df[date_col], dayfirst=True, errors="coerce"),
            "Time Block": pd.to_numeric(df[tb_col], errors="coerce"),
            "Value": pd.to_numeric(df[value_col], errors="coerce"),
            "Unit": unit,
        })

        result.dropna(subset=["Date", "Time Block", "Value"], inplace=True)

        if result.empty:
            return None, "No valid data rows found after parsing."

        return result, ""

    except Exception as e:
        return None, f"Error reading file: {e}"

# ─────────────────────────────────────────────────────────────────────────────
# CHARTS
# ─────────────────────────────────────────────────────────────────────────────

COLORS = {
    "GEN_80MW":   "#F97316",   # orange
    "GEN_43MW":   "#EAB308",   # yellow
    "GEN_Solar":  "#22C55E",   # green
    "WLL_WLL":    "#3B82F6",   # blue
    "WLL_WHSL":   "#6366F1",   # indigo
    "WCL_PIPE":   "#EC4899",   # pink
    "WCL_STEEL":  "#14B8A6",   # teal
    "WCL_ATSPL":  "#8B5CF6",   # violet
    "WCL_WDIPL":  "#F43F5E",   # rose
    "WCL_WASCO":  "#06B6D4",   # cyan
}

def make_line_chart(df: pd.DataFrame, plant_id: str, title: str) -> go.Figure:
    _, display, _, _, unit = PLANT_BY_ID[plant_id]
    color = COLORS.get(plant_id, "#3B82F6")

    dates = df["Date"].dt.strftime("%d %b %Y").unique()

    fig = go.Figure()

    for d in sorted(df["Date"].unique()):
        sub = df[df["Date"] == d].sort_values("Time Block")
        label = pd.to_datetime(d).strftime("%d %b %Y")
        fig.add_trace(go.Scatter(
            x=sub["Time Block"],
            y=sub["Value"],
            mode="lines",
            name=label,
            line=dict(width=2),
            hovertemplate=f"Block %{{x}}<br>%{{y:.2f}} {unit}<br>{label}<extra></extra>",
        ))

    fig.update_layout(
        title=dict(text=title, font=dict(size=16, family="DM Sans", color="#1e293b"), x=0.02),
        xaxis=dict(
            title="Time Block (1–96)",
            gridcolor="#f1f5f9",
            linecolor="#e2e8f0",
            tickfont=dict(family="DM Sans", size=11),
        ),
        yaxis=dict(
            title=f"Value ({unit})",
            gridcolor="#f1f5f9",
            linecolor="#e2e8f0",
            tickfont=dict(family="DM Sans", size=11),
        ),
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(family="DM Sans", size=11),
        ),
        margin=dict(l=50, r=20, t=60, b=50),
        height=350,
    )
    return fig


def make_daily_bar(df: pd.DataFrame, plant_id: str) -> go.Figure:
    _, display, _, _, unit = PLANT_BY_ID[plant_id]

    daily = df.groupby("Date")["Value"].agg(["mean", "max", "min"]).reset_index()
    daily["Date_str"] = daily["Date"].dt.strftime("%d %b")

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=daily["Date_str"],
        y=daily["mean"],
        name="Avg",
        marker_color=COLORS.get(plant_id, "#3B82F6"),
        hovertemplate="%{x}<br>Avg: %{y:.2f} " + unit + "<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=daily["Date_str"],
        y=daily["max"],
        mode="lines+markers",
        name="Peak",
        line=dict(color="#ef4444", width=1.5, dash="dot"),
        marker=dict(size=5),
    ))

    fig.update_layout(
        title=dict(text="Daily Average & Peak", font=dict(size=14, family="DM Sans", color="#1e293b"), x=0.02),
        xaxis=dict(gridcolor="#f1f5f9", tickfont=dict(family="DM Sans", size=11)),
        yaxis=dict(title=unit, gridcolor="#f1f5f9", tickfont=dict(family="DM Sans", size=11)),
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        legend=dict(font=dict(family="DM Sans", size=11)),
        margin=dict(l=50, r=20, t=50, b=40),
        height=280,
        barmode="group",
    )
    return fig


def make_dashboard_overview(all_data: dict) -> go.Figure:
    """Multi-line chart showing latest day for all plants that have data."""
    fig = go.Figure()

    for plant_id, df in all_data.items():
        if df is None or df.empty:
            continue
        last_date = df["Date"].max()
        sub = df[df["Date"] == last_date].sort_values("Time Block")
        _, display, _, _, unit = PLANT_BY_ID[plant_id]
        fig.add_trace(go.Scatter(
            x=sub["Time Block"],
            y=sub["Value"],
            mode="lines",
            name=display,
            line=dict(color=COLORS.get(plant_id, "#888"), width=2),
            hovertemplate=f"<b>{display}</b><br>Block %{{x}}<br>%{{y:.2f}} {unit}<br>{pd.to_datetime(last_date).strftime('%d %b %Y')}<extra></extra>",
        ))

    fig.update_layout(
        title=dict(text="All Plants — Latest Submission", font=dict(size=16, family="DM Sans", color="#1e293b"), x=0.02),
        xaxis=dict(title="Time Block (1–96)", gridcolor="#f1f5f9", tickfont=dict(family="DM Sans", size=11)),
        yaxis=dict(title="MW", gridcolor="#f1f5f9", tickfont=dict(family="DM Sans", size=11)),
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        legend=dict(
            orientation="v",
            font=dict(family="DM Sans", size=11),
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor="#e2e8f0",
            borderwidth=1,
        ),
        margin=dict(l=50, r=20, t=60, b=50),
        height=420,
    )
    return fig

# ─────────────────────────────────────────────────────────────────────────────
# PAGE: LOGIN / INPUT
# ─────────────────────────────────────────────────────────────────────────────

def page_input():
    st.markdown("""
    <div style='margin-bottom:1.5rem'>
        <h2 style='font-family:DM Sans,sans-serif;color:#1e293b;font-size:1.6rem;margin:0'>
            📥 Data Input
        </h2>
        <p style='color:#64748b;font-family:DM Sans,sans-serif;margin-top:0.3rem'>
            Log in with your plant credentials and upload your daily demand data.
        </p>
    </div>
    """, unsafe_allow_html=True)

    # ── Login form ──────────────────────────────────────────────────────────
    if "logged_in_plant" not in st.session_state:
        st.session_state.logged_in_plant = None

    if not st.session_state.logged_in_plant:
        with st.container():
            st.markdown("#### 🔐 Plant Login")
            col1, col2 = st.columns([1, 1])
            with col1:
                plant_options = [(p[0], p[1]) for p in PLANTS]
                selected = st.selectbox(
                    "Select Your Plant",
                    options=[p[0] for p in plant_options],
                    format_func=lambda x: PLANT_BY_ID[x][1],
                )
            with col2:
                password = st.text_input("Password", type="password",
                                          help="Default password is your plant ID in lowercase, e.g. gen_80mw")

            if st.button("Login →", type="primary", use_container_width=False):
                if verify_password(selected, password):
                    st.session_state.logged_in_plant = selected
                    st.success(f"Welcome, {PLANT_BY_ID[selected][1]}!")
                    st.rerun()
                else:
                    st.error("Incorrect password. Please try again.")

            st.markdown("""
            <div style='background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:0.8rem 1rem;margin-top:1rem;font-family:DM Sans,sans-serif;font-size:0.85rem;color:#0369a1'>
                💡 <strong>Default passwords</strong> are your Plant ID in lowercase.<br>
                Examples: <code>gen_80mw</code>, <code>wll_wll</code>, <code>wcl_pipe</code>
            </div>
            """, unsafe_allow_html=True)
        return

    # ── Logged in ───────────────────────────────────────────────────────────
    plant_id = st.session_state.logged_in_plant
    _, display, col_header, group, unit = PLANT_BY_ID[plant_id]

    col_title, col_logout = st.columns([4, 1])
    with col_title:
        color = COLORS.get(plant_id, "#3B82F6")
        st.markdown(f"""
        <div style='display:flex;align-items:center;gap:0.6rem;margin-bottom:1rem'>
            <div style='width:12px;height:12px;border-radius:50%;background:{color}'></div>
            <span style='font-family:DM Sans,sans-serif;font-size:1.1rem;font-weight:600;color:#1e293b'>
                Logged in as: {display}
            </span>
            <span style='background:#f1f5f9;color:#64748b;font-size:0.75rem;padding:2px 8px;border-radius:12px;font-family:DM Sans,sans-serif'>
                {group}
            </span>
        </div>
        """, unsafe_allow_html=True)
    with col_logout:
        if st.button("Logout", use_container_width=True):
            st.session_state.logged_in_plant = None
            st.rerun()

    # ── Last submission preview ─────────────────────────────────────────────
    last = load_last_entry(plant_id)
    if last is not None:
        last_date = last["Date"].max().strftime("%d %B %Y")
        avg_val = last["Value"].mean()
        max_val = last["Value"].max()
        st.markdown(f"""
        <div style='background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:1rem 1.2rem;margin-bottom:1.2rem;font-family:DM Sans,sans-serif'>
            <div style='font-size:0.8rem;color:#94a3b8;margin-bottom:0.3rem'>LAST SUBMISSION</div>
            <div style='display:flex;gap:2rem;align-items:center'>
                <div><span style='font-size:1rem;font-weight:600;color:#1e293b'>{last_date}</span></div>
                <div style='color:#64748b;font-size:0.9rem'>Avg: <strong>{avg_val:.2f} {unit}</strong></div>
                <div style='color:#64748b;font-size:0.9rem'>Peak: <strong>{max_val:.2f} {unit}</strong></div>
                <div style='color:#64748b;font-size:0.9rem'>Blocks: <strong>{len(last)}</strong></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        with st.expander("Preview last submission data"):
            show = last[["Date", "Time Block", "Value", "Unit"]].copy()
            show["Date"] = show["Date"].dt.strftime("%d-%m-%Y")
            show.columns = ["Date", "Time Block", col_header, "Unit"]
            st.dataframe(show, use_container_width=True, hide_index=True)
    else:
        st.info("No previous data found. Please upload your first submission below.")

    # ── Upload section ──────────────────────────────────────────────────────
    st.markdown("#### 📂 Upload Today's Data")

    st.markdown(f"""
    <div style='background:#fefce8;border:1px solid #fde68a;border-radius:8px;padding:0.8rem 1rem;margin-bottom:1rem;font-family:DM Sans,sans-serif;font-size:0.85rem;color:#92400e'>
        📋 Your file must contain columns: <strong>Date</strong>, <strong>Time Block</strong>, <strong>{col_header}</strong><br>
        Accepted formats: <strong>.xlsx</strong> or <strong>.csv</strong> &nbsp;|&nbsp; 96 rows (one per 15-min block)
    </div>
    """, unsafe_allow_html=True)

    uploaded = st.file_uploader(
        f"Upload file for {display}",
        type=["xlsx", "xls", "csv"],
        help=f"Upload your daily demand Excel or CSV for {display}",
    )

    if uploaded:
        df_parsed, err = parse_upload(uploaded, plant_id)
        if err:
            st.error(f"❌ {err}")
        else:
            dates_found = df_parsed["Date"].dt.strftime("%d %B %Y").unique()
            st.success(f"✅ Parsed {len(df_parsed)} rows for: {', '.join(dates_found)}")

            preview = df_parsed[["Date", "Time Block", "Value", "Unit"]].copy()
            preview["Date"] = preview["Date"].dt.strftime("%d-%m-%Y")
            preview.columns = ["Date", "Time Block", col_header, "Unit"]

            with st.expander("Preview parsed data", expanded=True):
                st.dataframe(preview.head(20), use_container_width=True, hide_index=True)
                if len(preview) > 20:
                    st.caption(f"... and {len(preview) - 20} more rows")

            col_a, col_b = st.columns([1, 3])
            with col_a:
                if st.button("✅ Confirm & Save", type="primary", use_container_width=True):
                    save_data(plant_id, df_parsed)
                    st.success("Data saved successfully!")
                    st.balloons()
                    st.rerun()

    # ── Quick chart of last data ────────────────────────────────────────────
    if last is not None and len(last) > 0:
        st.markdown("#### 📈 Your Latest Data")
        fig = make_line_chart(last, plant_id, f"{display} — Last Submission")
        st.plotly_chart(fig, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE: DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

def page_dashboard():
    st.markdown("""
    <div style='margin-bottom:1.5rem'>
        <h2 style='font-family:DM Sans,sans-serif;color:#1e293b;font-size:1.6rem;margin:0'>
            📊 Demand Dashboard
        </h2>
        <p style='color:#64748b;font-family:DM Sans,sans-serif;margin-top:0.3rem'>
            Real-time view of all plant demand submissions.
        </p>
    </div>
    """, unsafe_allow_html=True)

    # Load all data
    all_data = {p[0]: load_data(p[0]) for p in PLANTS}
    plants_with_data = {k: v for k, v in all_data.items() if v is not None and not v.empty}

    if not plants_with_data:
        st.warning("No data available yet. Plants need to submit their data first.")
        return

    # ── Summary cards ───────────────────────────────────────────────────────
    st.markdown("#### Summary — Latest Submissions")

    cols = st.columns(5)
    for i, (plant_id, df) in enumerate(plants_with_data.items()):
        _, display, _, group, unit = PLANT_BY_ID[plant_id]
        last_date = df["Date"].max().strftime("%d %b")
        last_df = df[df["Date"] == df["Date"].max()]
        avg = last_df["Value"].mean()
        color = COLORS.get(plant_id, "#3B82F6")
        with cols[i % 5]:
            st.markdown(f"""
            <div style='background:white;border:1px solid #e2e8f0;border-top:3px solid {color};
                        border-radius:10px;padding:0.9rem;margin-bottom:0.8rem;font-family:DM Sans,sans-serif'>
                <div style='font-size:0.72rem;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em'>{group}</div>
                <div style='font-size:0.95rem;font-weight:700;color:#1e293b;margin:0.2rem 0'>{display}</div>
                <div style='font-size:1.3rem;font-weight:800;color:{color}'>{avg:.1f}</div>
                <div style='font-size:0.75rem;color:#94a3b8'>{unit} avg · {last_date}</div>
            </div>
            """, unsafe_allow_html=True)

    # ── Overview chart ──────────────────────────────────────────────────────
    st.markdown("#### All Plants Overview")
    fig_overview = make_dashboard_overview(plants_with_data)
    st.plotly_chart(fig_overview, use_container_width=True)

    # ── Filter by group ─────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Detailed Plant Charts")

    col_filter, col_date = st.columns([2, 2])
    with col_filter:
        group_filter = st.radio(
            "Plant Group",
            ["All", "Generation", "Load"],
            horizontal=True,
        )
    with col_date:
        all_dates = sorted(set(
            d for df in plants_with_data.values()
            for d in df["Date"].unique()
        ), reverse=True)
        date_options = [pd.to_datetime(d).strftime("%d %B %Y") for d in all_dates]
        selected_date_str = st.selectbox("View Date", date_options)
        selected_date = pd.to_datetime(selected_date_str, format="%d %B %Y")

    # Filter plants
    filtered_plants = [
        (pid, df) for pid, df in plants_with_data.items()
        if group_filter == "All" or PLANT_BY_ID[pid][3] == group_filter
    ]

    if not filtered_plants:
        st.info("No data for selected filters.")
        return

    # ── Per-plant detailed charts ───────────────────────────────────────────
    for i in range(0, len(filtered_plants), 2):
        cols = st.columns(2)
        for j, (plant_id, df) in enumerate(filtered_plants[i:i+2]):
            _, display, _, group, unit = PLANT_BY_ID[plant_id]
            with cols[j]:
                date_df = df[df["Date"] == selected_date]
                is_fallback = False
                display_date = selected_date
                
                if date_df.empty:
                    # Fallback to absolute most recent date
                    last_available_date = df["Date"].max()
                    if pd.notnull(last_available_date):
                        date_df = df[df["Date"] == last_available_date]
                        display_date = last_available_date
                        is_fallback = True

                if date_df.empty:
                    st.markdown(f"""
                    <div style='background:#f8fafc;border:1px dashed #cbd5e1;border-radius:10px;
                                padding:2rem;text-align:center;font-family:DM Sans,sans-serif;color:#94a3b8'>
                        <strong>{display}</strong><br>No data available
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    if is_fallback:
                        st.markdown(f"""
                        <div style='background:#fffbeb;border:1px solid #fef3c7;border-radius:6px;
                                    padding:4px 10px;margin-bottom:8px;font-size:0.75rem;color:#92400e;
                                    display:inline-block;font-family:DM Sans,sans-serif'>
                            ⚠️ Showing latest available data from <b>{display_date.strftime('%d %b %Y')}</b>
                        </div>
                        """, unsafe_allow_html=True)
                    
                    fig = make_line_chart(date_df, plant_id, f"{display}")
                    st.plotly_chart(fig, use_container_width=True)

    # ── Daily stats table ───────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Daily Statistics Table")

    rows = []
    for plant_id, df in plants_with_data.items():
        _, display, _, group, unit = PLANT_BY_ID[plant_id]
        for d in sorted(df["Date"].unique(), reverse=True)[:7]:
            sub = df[df["Date"] == d]
            rows.append({
                "Plant": display,
                "Group": group,
                "Date": pd.to_datetime(d).strftime("%d %b %Y"),
                "Blocks": len(sub),
                f"Avg ({unit})": round(sub["Value"].mean(), 2),
                f"Max ({unit})": round(sub["Value"].max(), 2),
                f"Min ({unit})": round(sub["Value"].min(), 2),
            })

    if rows:
        table_df = pd.DataFrame(rows)
        st.dataframe(
            table_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Group": st.column_config.TextColumn(width="small"),
                "Blocks": st.column_config.NumberColumn(width="small"),
            }
        )

    # ── Comparison chart ─────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Compare Plants Side-by-Side")

    compare_options = [p[0] for p in PLANTS if p[0] in plants_with_data]
    selected_compare = st.multiselect(
        "Select plants to compare",
        options=compare_options,
        default=compare_options[:3],
        format_func=lambda x: PLANT_BY_ID[x][1],
    )

    if selected_compare:
        fig_compare = go.Figure()
        for pid in selected_compare:
            df = plants_with_data[pid]
            last_date = df["Date"].max()
            sub = df[df["Date"] == last_date].sort_values("Time Block")
            _, display, _, _, unit = PLANT_BY_ID[pid]
            fig_compare.add_trace(go.Scatter(
                x=sub["Time Block"],
                y=sub["Value"],
                mode="lines",
                name=display,
                line=dict(color=COLORS.get(pid, "#888"), width=2.5),
                hovertemplate=f"{display}: %{{y:.2f}} {unit}<extra></extra>",
            ))
        fig_compare.update_layout(
            title=dict(text="Plant Comparison — Latest Day", font=dict(size=15, family="DM Sans", color="#1e293b"), x=0.02),
            xaxis=dict(title="Time Block", gridcolor="#f1f5f9", tickfont=dict(family="DM Sans", size=11)),
            yaxis=dict(title="MW", gridcolor="#f1f5f9", tickfont=dict(family="DM Sans", size=11)),
            plot_bgcolor="#ffffff",
            paper_bgcolor="#ffffff",
            legend=dict(font=dict(family="DM Sans", size=11)),
            margin=dict(l=50, r=20, t=60, b=50),
            height=400,
        )
        st.plotly_chart(fig_compare, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN APP
# ─────────────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Plant Demand Dashboard",
        page_icon="⚡",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # ── Global styles ────────────────────────────────────────────────────────
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700;800&display=swap');

    html, body, [class*="css"] {
        font-family: 'DM Sans', sans-serif;
    }
    .main .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2rem;
        max-width: 1200px;
    }
    .stButton > button {
        font-family: 'DM Sans', sans-serif;
        font-weight: 600;
        border-radius: 8px;
    }
    .stButton > button[kind="primary"] {
        background: #1e40af;
        border: none;
    }
    .stButton > button[kind="primary"]:hover {
        background: #1d4ed8;
    }
    div[data-testid="stFileUploader"] {
        border-radius: 10px;
    }
    .stSelectbox > div > div {
        border-radius: 8px;
    }
    </style>
    """, unsafe_allow_html=True)

    # ── Sidebar ──────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("""
        <div style='padding:0.5rem 0 1.5rem 0'>
            <div style='font-size:1.5rem;font-weight:800;color:#1e293b;font-family:DM Sans,sans-serif'>
                ⚡ Plant Demand
            </div>
            <div style='font-size:0.8rem;color:#94a3b8;font-family:DM Sans,sans-serif'>
                50Hertz · WLL / WCL Automation
            </div>
        </div>
        """, unsafe_allow_html=True)

        page = st.radio(
            "Navigation",
            ["📥 Data Input", "📊 Dashboard"],
            label_visibility="collapsed",
        )

        st.markdown("---")

        # Plant status
        st.markdown("<div style='font-size:0.75rem;color:#94a3b8;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.5rem'>Plant Status</div>", unsafe_allow_html=True)
        for p in PLANTS:
            pid, display, _, group, _ = p
            df = load_data(pid)
            if df is not None and not df.empty:
                last_date = df["Date"].max()
                is_today = pd.to_datetime(last_date).date() >= date.today()
                dot = "🟢" if is_today else "🟡"
                date_str = pd.to_datetime(last_date).strftime("%d %b")
            else:
                dot = "🔴"
                date_str = "No data"
            color = COLORS.get(pid, "#888")
            st.markdown(f"""
            <div style='display:flex;justify-content:space-between;align-items:center;
                        padding:0.25rem 0;font-family:DM Sans,sans-serif;font-size:0.82rem'>
                <span style='color:#334155'>{dot} {display}</span>
                <span style='color:#94a3b8;font-size:0.75rem'>{date_str}</span>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("---")
        st.markdown(f"<div style='font-size:0.75rem;color:#94a3b8;font-family:DM Sans,sans-serif'>Last refreshed<br>{datetime.now().strftime('%d %b %Y, %H:%M')}</div>", unsafe_allow_html=True)

        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()

    # ── Route ────────────────────────────────────────────────────────────────
    if "Input" in page:
        page_input()
    else:
        page_dashboard()


if __name__ == "__main__":
    main()

# ─────────────────────────────────────────────────────────────────────────────
# MARKET DATA BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_market_data() -> dict:
    import math as _m

    re_schedule = []
    for i in range(96):
        h = (i * 15) / 60.0
        solar = 30 * _m.exp(-((h - 12) ** 2) / 16) if 6 <= h <= 18 else 0
        thermal = (62
            + 8  * _m.sin(_m.pi * (h - 2) / 12)
            + 5  * _m.sin(_m.pi * (h - 8) / 6))
        re_schedule.append(round(thermal + solar, 2))

    re_generation = []
    for i, sched in enumerate(re_schedule):
        h = (i * 15) / 60.0
        shortfall = (0.10
            + 0.05 * _m.sin(2 * _m.pi * h / 7   + 0.5)
            + 0.02 * _m.sin(2 * _m.pi * h / 1.8 + 1.1))
        noise = 0.8 * _m.sin(2 * _m.pi * i / 3.7 + 2.1)
        re_generation.append(round(max(0, sched * (1 - shortfall) + noise), 2))

    all_data = {p[0]: load_data(p[0]) for p in PLANTS}
    plants_with_data = {k: v for k, v in all_data.items() if v is not None and not v.empty}

    demand_from_data = None
    if plants_with_data:
        idx96 = pd.RangeIndex(1, 97)
        ds = pd.Series(0.0, index=idx96)
        found = False
        for pid in ["WCL_PIPE","WCL_STEEL","WCL_ATSPL","WCL_WDIPL","WCL_WASCO","WLL_WLL","WLL_WHSL"]:
            df = plants_with_data.get(pid)
            if df is None: continue
            sub = df[df["Date"] == df["Date"].max()].set_index("Time Block")["Value"]
            ds = ds.add(sub.reindex(idx96, fill_value=0), fill_value=0)
            found = True
        for pid in ["GEN_80MW","GEN_43MW","GEN_Solar"]:
            df = plants_with_data.get(pid)
            if df is None: continue
            sub = df[df["Date"] == df["Date"].max()]
            if "Auxiliary" in sub.columns:
                a = sub.set_index("Time Block")["Auxiliary"].dropna()
                ds = ds.add(a.reindex(idx96, fill_value=0), fill_value=0)
        if found:
            demand_from_data = ds.tolist()

    if demand_from_data:
        actual_demand = [round(v, 2) for v in demand_from_data]
        data_source = "uploaded"
    else:
        actual_demand = []
        for i, sched in enumerate(re_schedule):
            h = (i * 15) / 60.0
            val = (sched
                + 20 * _m.sin(2 * _m.pi * h / 5.2 + 0.9)
                + 12 * _m.sin(2 * _m.pi * h / 2.6 + 1.5)
                +  7 * _m.sin(2 * _m.pi * h / 1.4 + 0.4)
                +  3 * _m.sin(2 * _m.pi * i / 3.1 + 0.7))
            actual_demand.append(round(max(50, min(180, val)), 2))
        data_source = "demo"

    net_demand = [round(actual_demand[i] - re_generation[i], 2) for i in range(96)]
    demand_met = [round(min(re_generation[i], actual_demand[i]), 2) for i in range(96)]
    buy  = [round( v, 2) if v > 0 else 0 for v in net_demand]
    sell = [round(-v, 2) if v < 0 else 0 for v in net_demand]
    crossings = sum(1 for i in range(1, 96) if (net_demand[i-1] > 0) != (net_demand[i] > 0))

    return dict(re_schedule=re_schedule, re_generation=re_generation,
                actual_demand=actual_demand, demand_met=demand_met,
                net_demand=net_demand, buy=buy, sell=sell,
                crossings=crossings, data_source=data_source)


import urllib.request, urllib.error
LIVE_API_URL = "http://localhost:8765/live"

def _fetch_live() -> Optional[dict]:
    try:
        with urllib.request.urlopen(LIVE_API_URL, timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# PAGE: LIVE GENERATION — WELSPUN MARKET VISUALIZATION
# ─────────────────────────────────────────────────────────────────────────────

def page_live():
    st.markdown("""
    <div style='margin-bottom:1rem'>
        <h2 style='font-family:DM Sans,sans-serif;color:#1e293b;font-size:1.6rem;margin:0'>
            ⚡ Live Generation vs Demand
        </h2>
        <p style='color:#64748b;font-family:DM Sans,sans-serif;margin-top:0.3rem'>
            RE Schedule vs Actual Generation vs Actual Demand — buy/sell positions in the open market.
        </p>
    </div>
    """, unsafe_allow_html=True)

    col_ctrl1, col_ctrl2, col_ctrl3 = st.columns([3, 1, 1])
    with col_ctrl2:
        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()
    with col_ctrl3:
        auto_refresh = st.toggle("Auto 30s", value=False)
    if auto_refresh:
        import time as _time; _time.sleep(30); st.rerun()

    mkt = _build_market_data()

    with col_ctrl1:
        if mkt["data_source"] == "uploaded":
            st.markdown("""<div style='background:#f0fdf4;border:1px solid #86efac;border-radius:8px;
                padding:0.45rem 1rem;font-family:DM Sans,sans-serif;font-size:0.82rem;color:#166534'>
                📂 Actual demand from uploaded plant data</div>""", unsafe_allow_html=True)
        else:
            st.markdown("""<div style='background:#fefce8;border:1px solid #fde68a;border-radius:8px;
                padding:0.45rem 1rem;font-family:DM Sans,sans-serif;font-size:0.82rem;color:#92400e'>
                🔶 Demo data — upload plant files to use real demand</div>""", unsafe_allow_html=True)

    now = datetime.now()
    cur_block = min(now.hour * 4 + now.minute // 15, 95)
    live = _fetch_live()
    live_gen_now = live["totals"]["total_generation"] if live else mkt["re_generation"][cur_block]

    avg_demand = round(sum(mkt["actual_demand"]) / 96, 1)
    avg_sched  = round(sum(mkt["re_schedule"])   / 96, 1)
    avg_gen    = round(sum(mkt["re_generation"])  / 96, 1)
    total_buy  = round(sum(mkt["buy"])  * 0.25, 1)
    total_sell = round(sum(mkt["sell"]) * 0.25, 1)

    def kpi(col, label, val, color, sub=""):
        with col:
            st.markdown(f"""
            <div style='background:white;border:2px solid {color};border-radius:10px;
                        padding:0.9rem 1rem;font-family:DM Sans,sans-serif;margin-bottom:0.5rem'>
                <div style='font-size:0.68rem;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em'>{label}</div>
                <div style='font-size:1.5rem;font-weight:800;color:{color};line-height:1.2'>{val}</div>
                <div style='font-size:0.7rem;color:#94a3b8'>{sub}</div>
            </div>""", unsafe_allow_html=True)

    c1,c2,c3,c4,c5 = st.columns(5)
    kpi(c1, "Avg actual demand",  f"{avg_demand} MW", "#8b3dba", "from all plants")
    kpi(c2, "Avg RE schedule",    f"{avg_sched} MW",  "#2a5fa5", "planned generation")
    kpi(c3, "Avg RE generation",  f"{avg_gen} MW",    "#1a8a50", "actual generation")
    kpi(c4, "Total mkt buy",      f"{total_buy} MWh", "#b94a2a",
        f"{sum(1 for v in mkt['net_demand'] if v > 0)} blocks")
    kpi(c5, "Total mkt sell",     f"{total_sell} MWh","#1a6b44",
        f"{sum(1 for v in mkt['net_demand'] if v < 0)} blocks")

    st.markdown(
        f"<div style='font-size:0.75rem;color:#94a3b8;font-family:DM Sans;margin-bottom:0.5rem'>"
        f"Current block: {TIME_STAMPS[cur_block]} &nbsp;|&nbsp; "
        f"Live RE gen: <strong>{live_gen_now:.1f} MW</strong> &nbsp;|&nbsp; "
        f"Crossovers today: <strong>{mkt['crossings']}</strong></div>",
        unsafe_allow_html=True)

    st.markdown("""
    <div style='display:flex;flex-wrap:wrap;gap:16px;margin-bottom:8px;font-size:12px;
                color:#64748b;font-family:DM Sans,sans-serif;align-items:center'>
        <span style='display:flex;align-items:center;gap:5px'>
            <span style='width:28px;height:0;border-top:2.5px dashed #8b3dba;display:inline-block'></span>Actual demand</span>
        <span style='display:flex;align-items:center;gap:5px'>
            <span style='width:28px;height:2.5px;background:#2a5fa5;display:inline-block;border-radius:2px'></span>RE schedule</span>
        <span style='display:flex;align-items:center;gap:5px'>
            <span style='width:28px;height:0;border-top:2px dashed #1a8a50;display:inline-block'></span>RE generation (actual)</span>
        <span style='display:flex;align-items:center;gap:5px'>
            <span style='width:12px;height:12px;border-radius:2px;background:rgba(180,60,30,0.65);display:inline-block'></span>Buy from market</span>
        <span style='display:flex;align-items:center;gap:5px'>
            <span style='width:12px;height:12px;border-radius:2px;background:rgba(20,120,70,0.65);display:inline-block'></span>Sell to market</span>
        <span style='display:flex;align-items:center;gap:5px'>
            <span style='font-size:14px'>⭐</span>Live now</span>
    </div>
    """, unsafe_allow_html=True)

    x_labels = list(TIME_STAMPS)
    fig = go.Figure()

    # Shaded gap between RE Schedule and RE Generation (underperformance zone)
    fig.add_trace(go.Scatter(
        x=x_labels, y=mkt["re_schedule"],
        fill=None, mode="lines", line=dict(width=0),
        showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=x_labels, y=mkt["re_generation"],
        fill="tonexty", fillcolor="rgba(42,95,165,0.07)",
        mode="lines", line=dict(width=0),
        showlegend=False, hoverinfo="skip",
    ))

    fig.add_trace(go.Scatter(
        x=x_labels, y=mkt["re_schedule"],
        mode="lines", name="RE schedule",
        line=dict(color="#2a5fa5", width=2.5),
        hovertemplate="<b>RE schedule</b><br>%{x}<br>%{y:.2f} MW<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=x_labels, y=mkt["re_generation"],
        mode="lines", name="RE generation",
        line=dict(color="#1a8a50", width=2, dash="dash"),
        hovertemplate="<b>RE generation</b><br>%{x}<br>%{y:.2f} MW<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=x_labels, y=mkt["actual_demand"],
        mode="lines", name="Actual demand",
        line=dict(color="#8b3dba", width=2.5, dash="dot"),
        hovertemplate="<b>Actual demand</b><br>%{x}<br>%{y:.2f} MW<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=[TIME_STAMPS[cur_block]], y=[live_gen_now],
        mode="markers", name="Live now",
        marker=dict(color="#DC2626", size=13, symbol="star",
                    line=dict(color="white", width=2)),
        hovertemplate=f"<b>LIVE</b><br>{TIME_STAMPS[cur_block]}<br>{live_gen_now:.2f} MW<extra></extra>",
    ))

    fig.update_layout(
        xaxis=dict(title="Time interval", gridcolor="#f1f5f9",
                   tickfont=dict(family="DM Sans", size=9), tickangle=-45, nticks=12),
        yaxis=dict(title="Power (MW)", gridcolor="#f1f5f9",
                   tickfont=dict(family="DM Sans", size=11)),
        plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
        legend=dict(font=dict(family="DM Sans", size=11),
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=55, r=20, t=50, b=60), height=400,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown(
        "<div style='font-size:12px;color:#64748b;font-family:DM Sans;margin-bottom:4px'>"
        "Net demand per block (actual demand − RE generation) &nbsp;·&nbsp; "
        "<span style='color:#1a6b44'>▲ green = surplus → sell to market</span> &nbsp;·&nbsp; "
        "<span style='color:#b94a2a'>▼ red = deficit → buy from market</span>"
        "</div>", unsafe_allow_html=True)

    fig2 = go.Figure()
    fig2.add_trace(go.Bar(
        x=x_labels, y=mkt["sell"], name="Sell to market",
        marker_color="rgba(20,120,70,0.72)",
        hovertemplate="<b>Sell</b><br>%{x}<br>+%{y:.2f} MW<extra></extra>",
    ))
    fig2.add_trace(go.Bar(
        x=x_labels, y=[-v for v in mkt["buy"]], name="Buy from market",
        marker_color="rgba(180,60,30,0.72)",
        customdata=mkt["buy"],
        hovertemplate="<b>Buy</b><br>%{x}<br>%{customdata:.2f} MW<extra></extra>",
    ))
    fig2.add_hline(y=0, line_width=1, line_color="#94a3b8")
    fig2.update_layout(
        barmode="relative",
        xaxis=dict(gridcolor="#f1f5f9",
                   tickfont=dict(family="DM Sans", size=9), tickangle=-45, nticks=12),
        yaxis=dict(title="Net demand (MW)", gridcolor="#f1f5f9",
                   tickfont=dict(family="DM Sans", size=11), tickformat="+.0f"),
        plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
        showlegend=False,
        margin=dict(l=55, r=20, t=10, b=60), height=200,
    )
    st.plotly_chart(fig2, use_container_width=True)

    st.markdown("---")
    st.markdown("#### Block Detail — Recent + Current")
    table_rows = []
    for i in range(max(0, cur_block - 8), min(96, cur_block + 5)):
        nd = mkt["net_demand"][i]
        if nd > 1:    status = "🔴 Buy"
        elif nd < -1: status = "🟢 Sell"
        else:         status = "🟡 Balanced"
        if i == cur_block: status = "⚡ LIVE · " + status
        table_rows.append({
            "Block":               i + 1,
            "Time Interval":       TIME_STAMPS[i],
            "RE Schedule (MW)":    mkt["re_schedule"][i],
            "RE Generation (MW)":  mkt["re_generation"][i],
            "Actual Demand (MW)":  mkt["actual_demand"][i],
            "Net Demand (MW)":     nd,
            "Market Action":       status,
        })
    st.dataframe(
        pd.DataFrame(table_rows), use_container_width=True, hide_index=True,
        column_config={
            "RE Schedule (MW)":   st.column_config.NumberColumn(format="%.2f"),
            "RE Generation (MW)": st.column_config.NumberColumn(format="%.2f"),
            "Actual Demand (MW)": st.column_config.NumberColumn(format="%.2f"),
            "Net Demand (MW)":    st.column_config.NumberColumn(format="%.2f"),
        }
    )
    st.markdown(
        "<div style='font-size:11px;color:#94a3b8;font-family:DM Sans;margin-top:6px'>"
        "Net demand = Actual demand − RE generation &nbsp;·&nbsp; "
        "Manikaran facilitates buy/sell on open power exchange on behalf of Welspun"
        "</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Plant Demand Dashboard",
        page_icon="⚡",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700;800&display=swap');
    html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
    .main .block-container { padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1200px; }
    .stButton > button { font-family: 'DM Sans', sans-serif; font-weight: 600; border-radius: 8px; }
    .stButton > button[kind="primary"] { background: #1e40af; border: none; }
    </style>
    """, unsafe_allow_html=True)

    with st.sidebar:
        st.markdown("""
        <div style='padding:0.5rem 0 1.5rem 0'>
            <div style='font-size:1.5rem;font-weight:800;color:#1e293b'>⚡ Plant Demand</div>
            <div style='font-size:0.8rem;color:#94a3b8'>50Hertz · WLL / WCL Automation</div>
        </div>
        """, unsafe_allow_html=True)

        page = st.radio(
            "Navigation",
            ["📥 Data Input", "📊 Dashboard", "📑 Consolidated", "⚡ Live Generation"],
            label_visibility="collapsed",
        )

        st.markdown("---")
        st.markdown("<div style='font-size:0.75rem;color:#94a3b8;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.5rem'>Plant Status</div>", unsafe_allow_html=True)

        for p in PLANTS:
            pid, display, _, _, group, _ = p
            df = load_data(pid)
            if df is not None and not df.empty:
                last_date = df["Date"].max()
                is_today = pd.to_datetime(last_date).date() >= date.today()
                dot = "🟢" if is_today else "🟡"
                date_str = pd.to_datetime(last_date).strftime("%d %b")
            else:
                dot = "🔴"
                date_str = "No data"
            st.markdown(f"""
            <div style='display:flex;justify-content:space-between;align-items:center;
                        padding:0.25rem 0;font-size:0.82rem'>
                <span style='color:#334155'>{dot} {display}</span>
                <span style='color:#94a3b8;font-size:0.75rem'>{date_str}</span>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("---")
        d2 = _d2_date()
        st.markdown(f"""
        <div style='font-size:0.75rem;color:#94a3b8'>
            Last refreshed<br>{datetime.now().strftime('%d %b %Y, %H:%M')}<br>
            <span style='color:#059669'>D+2: {d2.strftime('%d %b %Y')}</span>
        </div>
        """, unsafe_allow_html=True)

        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()

    if "Input" in page:
        page_input()
    elif "Consolidated" in page:
        page_consolidated()
    elif "Live" in page:
        page_live()
    else:
        page_dashboard()


if __name__ == "__main__":
    main()
