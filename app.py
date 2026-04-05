"""
app.py  —  Plant Demand Dashboard
Run:  streamlit run app.py
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    OPENPYXL_OK = True
except ImportError:
    OPENPYXL_OK = False

# ─────────────────────────────────────────────────────────────────────────────
# SUPABASE  —  reads credentials from st.secrets (Streamlit Cloud) or env vars
# ─────────────────────────────────────────────────────────────────────────────

try:
    from supabase import create_client, Client as SupabaseClient
    _SUPA_URL = st.secrets.get("SUPABASE_URL", os.environ.get("SUPABASE_URL", ""))
    _SUPA_KEY = st.secrets.get("SUPABASE_KEY", os.environ.get("SUPABASE_KEY", ""))
    if _SUPA_URL and _SUPA_KEY:
        _supa: SupabaseClient = create_client(_SUPA_URL, _SUPA_KEY)
        SUPABASE_OK = True
    else:
        _supa = None
        SUPABASE_OK = False
except Exception:
    _supa = None
    SUPABASE_OK = False

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

# Local fallback dir (used only when running locally without Supabase secrets)
DATA_DIR = Path("plant_data")
DATA_DIR.mkdir(exist_ok=True)

# 96 time-block timestamps
TIME_STAMPS = []
for i in range(96):
    h_start = i * 15 // 60
    m_start = (i * 15) % 60
    h_end   = (i * 15 + 15) // 60
    m_end   = ((i * 15 + 15)) % 60
    TIME_STAMPS.append(f"{h_start}:{m_start:02d} - {h_end}:{m_end:02d}")

# Plant definitions
# (plant_id, display_name, primary_col, aux_col_or_None, group, unit)
PLANTS = [
    # Generation plants  — have auxiliary column
    ("GEN_80MW",   "80MW Generation",    "80MW GENERATION",      "80MW AUXILIARY",   "Generation", "MW"),
    ("GEN_43MW",   "43MW Generation",    "43MW GENERATION",      "43MW AUXILIARY",   "Generation", "MW"),
    ("GEN_Solar",  "Solar Generation",   "SOLAR NET GENERATION", "SOLAR AUXILIARY",  "Generation", "MW"),
    # Load plants — no auxiliary column
    ("WLL_WLL",    "WLL",                "WLL",                  None,               "WLL",        "MW"),
    ("WLL_WHSL",   "WHSL",               "WHSL",                 None,               "WLL",        "MW"),
    ("WCL_PIPE",   "WCL Pipe Division",  "WCL PIPE DIVISION",    None,               "WCL",        "MW"),
    ("WCL_STEEL",  "WCL Steel Division", "WCL STEEL DIVISION",   None,               "WCL",        "MW"),
    ("WCL_ATSPL",  "ATSPL",              "ATSPL",                None,               "WCL",        "MW"),
    ("WCL_WDIPL",  "WDIPL",              "WDIPL",                None,               "WCL",        "MW"),
    ("WCL_WASCO",  "WASCO",              "WASCO",                None,               "WCL",        "MW"),
]

PLANT_BY_ID = {p[0]: p for p in PLANTS}

def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

DEFAULT_CREDENTIALS = {p[0]: _hash(p[0].lower()) for p in PLANTS}

# ─────────────────────────────────────────────────────────────────────────────
# CREDENTIALS  —  Supabase table "credentials" or local JSON fallback
# ─────────────────────────────────────────────────────────────────────────────

CREDENTIALS_FILE = DATA_DIR / "credentials.json"

def load_credentials() -> dict:
    """Load credentials: Supabase first, local JSON fallback."""
    if SUPABASE_OK:
        try:
            rows = _supa.table("credentials").select("plant_id, password_hash").execute()
            return {r["plant_id"]: r["password_hash"] for r in rows.data}
        except Exception:
            pass
    # Local fallback
    if not CREDENTIALS_FILE.exists():
        CREDENTIALS_FILE.write_text(json.dumps(DEFAULT_CREDENTIALS, indent=2))
    return json.loads(CREDENTIALS_FILE.read_text())

def verify_password(plant_id: str, password: str) -> bool:
    creds = load_credentials()
    return creds.get(plant_id) == _hash(password)

# ─────────────────────────────────────────────────────────────────────────────
# DATA STORAGE  —  Supabase table "plant_readings" or local CSV fallback
# ─────────────────────────────────────────────────────────────────────────────

def data_file(plant_id: str) -> Path:
    return DATA_DIR / f"{plant_id}.csv"

def save_data(plant_id: str, df: pd.DataFrame) -> None:
    """Save plant data rows. Supabase upsert on (plant_id, date, time_block); CSV fallback."""
    df = df.copy()
    df["plant_id"] = plant_id

    if SUPABASE_OK:
        try:
            records = []
            for _, row in df.iterrows():
                rec = {
                    "plant_id":   plant_id,
                    "date":       row["Date"].strftime("%Y-%m-%d"),
                    "time_block": int(row["Time Block"]),
                    "value":      None if pd.isna(row["Value"]) else float(row["Value"]),
                    "auxiliary":  None if (pd.isna(row.get("Auxiliary", float("nan"))) if "Auxiliary" in row else True) else float(row["Auxiliary"]),
                    "unit":       str(row.get("Unit", "MW")),
                }
                records.append(rec)
            # Upsert in chunks of 500
            for i in range(0, len(records), 500):
                _supa.table("plant_readings").upsert(
                    records[i:i+500],
                    on_conflict="plant_id,date,time_block"
                ).execute()
            return
        except Exception as e:
            st.warning(f"Supabase save failed, falling back to local: {e}")

    # ── Local CSV fallback ────────────────────────────────────────────────
    path = data_file(plant_id)
    if path.exists():
        existing = pd.read_csv(path, parse_dates=["Date"])
        new_dates = df["Date"].unique()
        existing = existing[~existing["Date"].isin(new_dates)]
        combined = pd.concat([existing, df], ignore_index=True)
    else:
        combined = df
    combined.sort_values(["Date", "Time Block"], inplace=True)
    combined.to_csv(path, index=False)


def load_data(plant_id: str) -> Optional[pd.DataFrame]:
    """Load all data for a plant. Supabase first, CSV fallback."""
    if SUPABASE_OK:
        try:
            rows = _supa.table("plant_readings")                         .select("date, time_block, value, auxiliary, unit")                         .eq("plant_id", plant_id)                         .order("date")                         .order("time_block")                         .execute()
            if not rows.data:
                return None
            df = pd.DataFrame(rows.data)
            df.rename(columns={"date": "Date", "time_block": "Time Block",
                                "value": "Value", "auxiliary": "Auxiliary",
                                "unit": "Unit"}, inplace=True)
            df["Date"] = pd.to_datetime(df["Date"])
            df["Time Block"] = df["Time Block"].astype(int)
            df["Value"] = pd.to_numeric(df["Value"], errors="coerce")
            df["Auxiliary"] = pd.to_numeric(df["Auxiliary"], errors="coerce")
            df.sort_values(["Date", "Time Block"], inplace=True)
            return df
        except Exception as e:
            st.warning(f"Supabase load failed, falling back to local: {e}")

    # ── Local CSV fallback ────────────────────────────────────────────────
    path = data_file(plant_id)
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["Date"])
    df.sort_values(["Date", "Time Block"], inplace=True)
    return df


def load_last_entry(plant_id: str) -> Optional[pd.DataFrame]:
    df = load_data(plant_id)
    if df is None or df.empty:
        return None
    last_date = df["Date"].max()
    return df[df["Date"] == last_date].copy()

# ─────────────────────────────────────────────────────────────────────────────
# EXCEL PARSER  — now handles timestamp column + optional auxiliary
# ─────────────────────────────────────────────────────────────────────────────

def parse_upload(uploaded_file, plant_id: str) -> tuple[Optional[pd.DataFrame], str]:
    pid, display, col_header, aux_col, group, unit = PLANT_BY_ID[plant_id]

    try:
        if uploaded_file.name.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        else:
            # Row 0 = title banner ("... Daily Data Template | D+2: ..."), row 1 = real headers
            # Try header=1 first; fall back to header=0 if the expected columns aren't found
            df = pd.read_excel(uploaded_file, header=1)
            # If the real header columns aren't present, the file has no title row — re-read
            cols_upper = [str(c).strip().upper() for c in df.columns]
            if "DATE" not in cols_upper:
                uploaded_file.seek(0)
                df = pd.read_excel(uploaded_file, header=0)

        df.columns = [str(c).strip() for c in df.columns]

        # ── Date column ──────────────────────────────────────────────────────
        date_col = next((c for c in df.columns if "date" in c.lower()), None)
        if date_col is None:
            return None, "Could not find 'Date' column."

        # ── Time Interval / Time Block column ────────────────────────────────
        tb_col = next(
            (c for c in df.columns if "time" in c.lower() or "interval" in c.lower() or "block" in c.lower()),
            None
        )
        if tb_col is None:
            return None, "Could not find 'Time Interval' or 'Time Block' column."

        # ── Primary value column ─────────────────────────────────────────────
        value_col = None
        for c in df.columns:
            if c.upper() == col_header.upper():
                value_col = c
                break
        if value_col is None:
            for c in df.columns:
                if col_header.upper() in c.upper() or c.upper() in col_header.upper():
                    value_col = c
                    break
        if value_col is None:
            return None, f"Could not find column '{col_header}'. Columns: {list(df.columns)}"

        # ── Auxiliary column (optional) ──────────────────────────────────────
        aux_val_col = None
        if aux_col:
            for c in df.columns:
                if c.upper() == aux_col.upper():
                    aux_val_col = c
                    break
            if aux_val_col is None:
                for c in df.columns:
                    if aux_col.upper() in c.upper() or c.upper() in aux_col.upper():
                        aux_val_col = c
                        break

        # ── Build result dataframe ───────────────────────────────────────────
        # Time block: if numeric keep as-is, if string like "0:00 - 0:15" map to index
        raw_tb = df[tb_col]
        if pd.api.types.is_numeric_dtype(raw_tb):
            time_block_series = pd.to_numeric(raw_tb, errors="coerce")
        else:
            # Map timestamp string to block index 1-96
            ts_map = {ts: i+1 for i, ts in enumerate(TIME_STAMPS)}
            # Also accept slight variations (strip spaces)
            ts_map_clean = {ts.replace(" ", ""): i+1 for i, ts in enumerate(TIME_STAMPS)}
            def map_ts(v):
                v = str(v).strip()
                if v in ts_map:
                    return ts_map[v]
                v2 = v.replace(" ", "")
                if v2 in ts_map_clean:
                    return ts_map_clean[v2]
                try:
                    return int(float(v))
                except Exception:
                    return None
            time_block_series = raw_tb.apply(map_ts)

        result = pd.DataFrame({
            "Date":       pd.to_datetime(df[date_col], dayfirst=True, errors="coerce"),
            "Time Block": time_block_series,
            "Value":      pd.to_numeric(df[value_col], errors="coerce"),
            "Unit":       unit,
        })

        if aux_val_col:
            result["Auxiliary"] = pd.to_numeric(df[aux_val_col], errors="coerce")
        else:
            result["Auxiliary"] = None

        result.dropna(subset=["Date", "Time Block", "Value"], inplace=True)

        if result.empty:
            return None, "No valid data rows found after parsing."

        return result, ""

    except Exception as e:
        return None, f"Error reading file: {e}"

# ─────────────────────────────────────────────────────────────────────────────
# EXCEL TEMPLATE GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def _d2_date() -> date:
    """Return today + 2 days (D+2)."""
    return date.today() + timedelta(days=2)

def generate_template(plant_id: str) -> bytes:
    """Generate a filled Excel template for the given plant with D+2 date and 96 time-block rows."""
    if not OPENPYXL_OK:
        return b""

    pid, display, col_header, aux_col, group, unit = PLANT_BY_ID[plant_id]
    d2 = _d2_date()
    d2_str = d2.strftime("%d-%m-%Y")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = display[:31]

    # ── Styling helpers ──────────────────────────────────────────────────────
    HEADER_FILL   = PatternFill("solid", fgColor="1E3A5F")
    SUBHDR_FILL   = PatternFill("solid", fgColor="2D6A9F")
    ALT_FILL      = PatternFill("solid", fgColor="EBF5FB")
    HEADER_FONT   = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    TITLE_FONT    = Font(name="Calibri", bold=True, color="FFFFFF", size=13)
    DATA_FONT     = Font(name="Calibri", size=10)
    CENTER        = Alignment(horizontal="center", vertical="center")
    LEFT          = Alignment(horizontal="left",   vertical="center")
    thin          = Side(style="thin", color="BDC3C7")
    BORDER        = Border(left=thin, right=thin, top=thin, bottom=thin)

    # ── Title row ────────────────────────────────────────────────────────────
    cols = ["Date", "Time Interval", col_header]
    if aux_col:
        cols.append(aux_col)
    num_cols = len(cols)
    merge_end = get_column_letter(num_cols)

    ws.merge_cells(f"A1:{merge_end}1")
    title_cell = ws["A1"]
    title_cell.value = f"{display} — Daily Data Template  |  D+2: {d2_str}"
    title_cell.font  = TITLE_FONT
    title_cell.fill  = HEADER_FILL
    title_cell.alignment = CENTER
    ws.row_dimensions[1].height = 26

    # ── Header row ───────────────────────────────────────────────────────────
    for ci, hdr in enumerate(cols, start=1):
        cell = ws.cell(row=2, column=ci, value=hdr)
        cell.font      = HEADER_FONT
        cell.fill      = SUBHDR_FILL
        cell.alignment = CENTER
        cell.border    = BORDER
    ws.row_dimensions[2].height = 20

    # ── Data rows ────────────────────────────────────────────────────────────
    for i, ts in enumerate(TIME_STAMPS):
        row = 3 + i
        fill = ALT_FILL if i % 2 == 0 else None

        # Date
        dc = ws.cell(row=row, column=1, value=d2_str)
        dc.font = DATA_FONT; dc.alignment = CENTER; dc.border = BORDER
        if fill: dc.fill = fill

        # Time Interval
        tc = ws.cell(row=row, column=2, value=ts)
        tc.font = DATA_FONT; tc.alignment = CENTER; tc.border = BORDER
        if fill: tc.fill = fill

        # Primary value (blank — user fills in)
        vc = ws.cell(row=row, column=3, value=None)
        vc.font = DATA_FONT; vc.alignment = CENTER; vc.border = BORDER
        if fill: vc.fill = fill

        # Auxiliary (if applicable)
        if aux_col:
            ac = ws.cell(row=row, column=4, value=None)
            ac.font = DATA_FONT; ac.alignment = CENTER; ac.border = BORDER
            if fill: ac.fill = fill

    # ── Column widths ────────────────────────────────────────────────────────
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 22
    if aux_col:
        ws.column_dimensions["D"].width = 22

    # ── Freeze panes ─────────────────────────────────────────────────────────
    ws.freeze_panes = "A3"

    # ── Instructions sheet ───────────────────────────────────────────────────
    ws2 = wb.create_sheet("Instructions")
    instructions = [
        ("Plant",        display),
        ("Group",        group),
        ("Column",       col_header),
        ("",             ""),
        ("Instructions", ""),
        ("1.",           "Fill in the data values in the highlighted column(s)."),
        ("2.",           f"Date is pre-filled as D+2: {d2_str}. Do not change the Date column."),
        ("3.",           "Time Intervals are fixed 15-min blocks (96 rows = full day)."),
        ("4.",           "Save as .xlsx and upload via the Data Input page."),
        ("5.",           "Do NOT add or remove rows."),
    ]
    for ri, (k, v) in enumerate(instructions, start=1):
        ws2.cell(row=ri, column=1, value=k).font = Font(bold=(ri<=4 or k.endswith(".")))
        ws2.cell(row=ri, column=2, value=v)
    ws2.column_dimensions["A"].width = 16
    ws2.column_dimensions["B"].width = 70

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

# ─────────────────────────────────────────────────────────────────────────────
# CHARTS
# ─────────────────────────────────────────────────────────────────────────────

COLORS = {
    "GEN_80MW":   "#F97316",
    "GEN_43MW":   "#EAB308",
    "GEN_Solar":  "#22C55E",
    "WLL_WLL":    "#3B82F6",
    "WLL_WHSL":   "#6366F1",
    "WCL_PIPE":   "#EC4899",
    "WCL_STEEL":  "#14B8A6",
    "WCL_ATSPL":  "#8B5CF6",
    "WCL_WDIPL":  "#F43F5E",
    "WCL_WASCO":  "#06B6D4",
    # Totals
    "TOTAL_GEN":  "#F97316",
    "TOTAL_WLL":  "#3B82F6",
    "TOTAL_WCL":  "#EC4899",
    "TOTAL_AUX":  "#8B5CF6",
    "TOTAL_LOAD": "#1E293B",
}

def _tb_to_label(tb):
    """Convert 1-96 time block index to timestamp label."""
    try:
        idx = int(tb) - 1
        if 0 <= idx < 96:
            return TIME_STAMPS[idx]
    except Exception:
        pass
    return str(tb)

def make_line_chart(df: pd.DataFrame, plant_id: str, title: str) -> go.Figure:
    _, display, _, _, group, unit = PLANT_BY_ID[plant_id]
    color = COLORS.get(plant_id, "#3B82F6")

    fig = go.Figure()

    for d in sorted(df["Date"].unique()):
        sub = df[df["Date"] == d].sort_values("Time Block")
        label = pd.to_datetime(d).strftime("%d %b %Y")
        x_labels = sub["Time Block"].apply(_tb_to_label)
        fig.add_trace(go.Scatter(
            x=x_labels,
            y=sub["Value"],
            mode="lines",
            name=label,
            line=dict(width=2),
            hovertemplate=f"%{{x}}<br>%{{y:.2f}} {unit}<br>{label}<extra></extra>",
        ))

        # Auxiliary trace (dashed) if present
        if "Auxiliary" in sub.columns and sub["Auxiliary"].notna().any():
            fig.add_trace(go.Scatter(
                x=x_labels,
                y=sub["Auxiliary"],
                mode="lines",
                name=f"{label} (Aux)",
                line=dict(width=1.5, dash="dot", color=color),
                opacity=0.6,
                hovertemplate=f"%{{x}}<br>Aux: %{{y:.2f}} {unit}<br>{label}<extra></extra>",
            ))

    fig.update_layout(
        title=dict(text=title, font=dict(size=16, family="DM Sans", color="#1e293b"), x=0.02),
        xaxis=dict(
            title="Time Interval",
            gridcolor="#f1f5f9",
            linecolor="#e2e8f0",
            tickfont=dict(family="DM Sans", size=9),
            tickangle=-45,
            nticks=12,
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
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
            font=dict(family="DM Sans", size=11),
        ),
        margin=dict(l=50, r=20, t=60, b=70),
        height=350,
    )
    return fig


def make_total_chart(series_dict: dict, title: str, color_map: dict) -> go.Figure:
    """Plot multiple named series (dict of name → series indexed by time-block) on one chart."""
    fig = go.Figure()
    for name, ser in series_dict.items():
        if ser is None or ser.empty:
            continue
        x_labels = ser.index.map(_tb_to_label)
        fig.add_trace(go.Scatter(
            x=x_labels,
            y=ser.values,
            mode="lines",
            name=name,
            line=dict(width=2.5, color=color_map.get(name, "#888")),
            hovertemplate=f"<b>{name}</b><br>%{{x}}<br>%{{y:.2f}} MW<extra></extra>",
        ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=15, family="DM Sans", color="#1e293b"), x=0.02),
        xaxis=dict(title="Time Interval", gridcolor="#f1f5f9", tickfont=dict(family="DM Sans", size=9),
                   tickangle=-45, nticks=12),
        yaxis=dict(title="MW", gridcolor="#f1f5f9", tickfont=dict(family="DM Sans", size=11)),
        plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
        legend=dict(font=dict(family="DM Sans", size=11)),
        margin=dict(l=50, r=20, t=60, b=70),
        height=380,
    )
    return fig


def make_dashboard_overview(all_data: dict) -> go.Figure:
    fig = go.Figure()
    for plant_id, df in all_data.items():
        if df is None or df.empty:
            continue
        last_date = df["Date"].max()
        sub = df[df["Date"] == last_date].sort_values("Time Block")
        _, display, _, _, group, unit = PLANT_BY_ID[plant_id]
        x_labels = sub["Time Block"].apply(_tb_to_label)
        fig.add_trace(go.Scatter(
            x=x_labels, y=sub["Value"],
            mode="lines", name=display,
            line=dict(color=COLORS.get(plant_id, "#888"), width=2),
            hovertemplate=f"<b>{display}</b><br>%{{x}}<br>%{{y:.2f}} {unit}<br>{pd.to_datetime(last_date).strftime('%d %b %Y')}<extra></extra>",
        ))
    fig.update_layout(
        title=dict(text="All Plants — Latest Submission", font=dict(size=16, family="DM Sans", color="#1e293b"), x=0.02),
        xaxis=dict(title="Time Interval", gridcolor="#f1f5f9", tickfont=dict(family="DM Sans", size=9), tickangle=-45, nticks=12),
        yaxis=dict(title="MW", gridcolor="#f1f5f9", tickfont=dict(family="DM Sans", size=11)),
        plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
        legend=dict(orientation="v", font=dict(family="DM Sans", size=11),
                    bgcolor="rgba(255,255,255,0.9)", bordercolor="#e2e8f0", borderwidth=1),
        margin=dict(l=50, r=20, t=60, b=70),
        height=420,
    )
    return fig

# ─────────────────────────────────────────────────────────────────────────────
# TOTALS HELPER
# ─────────────────────────────────────────────────────────────────────────────

def compute_totals(plants_with_data: dict, selected_date) -> dict:
    """
    Returns dict of {label: pd.Series indexed by Time Block 1-96}.
    Total WCL  = PIPE + STEEL + ATSPL + WDIPL + WASCO
    Total WLL  = WLL + WHSL
    Total AUX  = 80MW AUX + 43MW AUX + Solar AUX (if any)
    Total Load = Total WLL + Total WCL + Total AUX
    Total Gen  = 80MW GEN + 43MW GEN + Solar GEN
    """
    def get_series(plant_id, col="Value"):
        df = plants_with_data.get(plant_id)
        if df is None or df.empty:
            return None
        sub = df[df["Date"] == selected_date]
        if sub.empty:
            # Fall back to most recent available date
            sub = df[df["Date"] == df["Date"].max()]
        if sub.empty:
            return None
        if col == "Auxiliary":
            if "Auxiliary" not in sub.columns:
                return None
            s = sub.set_index("Time Block")["Auxiliary"].dropna()
        else:
            s = sub.set_index("Time Block")["Value"]
        return s if not s.empty else None

    idx = pd.RangeIndex(1, 97)

    def safe_add(*series_list):
        result = pd.Series(0.0, index=idx)
        any_data = False
        for s in series_list:
            if s is not None and not s.empty:
                result = result.add(s.reindex(idx, fill_value=0), fill_value=0)
                any_data = True
        return result if any_data else None

    # WCL group
    wcl = safe_add(
        get_series("WCL_PIPE"), get_series("WCL_STEEL"),
        get_series("WCL_ATSPL"), get_series("WCL_WDIPL"), get_series("WCL_WASCO"),
    )
    # WLL group
    wll = safe_add(get_series("WLL_WLL"), get_series("WLL_WHSL"))
    # Auxiliary from generation plants
    aux = safe_add(
        get_series("GEN_80MW", "Auxiliary"),
        get_series("GEN_43MW", "Auxiliary"),
        get_series("GEN_Solar", "Auxiliary"),
    )
    # Total Load
    total_load = safe_add(
        wcl if wcl is not None else None,
        wll if wll is not None else None,
        aux if aux is not None else None,
    )
    # Total Generation
    total_gen = safe_add(
        get_series("GEN_80MW"), get_series("GEN_43MW"), get_series("GEN_Solar"),
    )

    return {
        "Total WCL":        wcl,
        "Total WLL":        wll,
        "Total Auxiliary":  aux,
        "Total Load":       total_load,
        "Total Generation": total_gen,
    }

def avg_or_none(s) -> Optional[float]:
    if s is None or (hasattr(s, "empty") and s.empty):
        return None
    return float(s.mean())

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

    if "logged_in_plant" not in st.session_state:
        st.session_state.logged_in_plant = None

    if not st.session_state.logged_in_plant:
        with st.container():
            st.markdown("#### 🔐 Plant Login")
            col1, col2 = st.columns([1, 1])
            with col1:
                selected = st.selectbox(
                    "Select Your Plant",
                    options=[p[0] for p in PLANTS],
                    format_func=lambda x: PLANT_BY_ID[x][1],
                )
            with col2:
                password = st.text_input("Password", type="password",
                                          help="Default password is your plant ID in lowercase")

            if st.button("Login →", type="primary"):
                if verify_password(selected, password):
                    st.session_state.logged_in_plant = selected
                    st.success(f"Welcome, {PLANT_BY_ID[selected][1]}!")
                    st.rerun()
                else:
                    st.error("Incorrect password.")

            st.markdown("""
            <div style='background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;
                        padding:0.8rem 1rem;margin-top:1rem;font-family:DM Sans,sans-serif;
                        font-size:0.85rem;color:#0369a1'>
                💡 <strong>Default passwords</strong> are your Plant ID in lowercase.<br>
                Examples: <code>gen_80mw</code>, <code>wll_wll</code>, <code>wcl_pipe</code>
            </div>
            """, unsafe_allow_html=True)

        # ── Template for the selected plant only ──────────────────────────
        if OPENPYXL_OK:
            st.markdown("---")
            d2 = _d2_date()
            sel_pid, sel_disp, sel_col, sel_aux, sel_grp, sel_unit = PLANT_BY_ID[selected]
            aux_note = f" + {sel_aux}" if sel_aux else ""
            st.markdown(f"""
            <div style='background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;
                        padding:0.7rem 1rem;margin-bottom:0.8rem;font-family:DM Sans,sans-serif;
                        font-size:0.85rem;color:#166534'>
                📋 <strong>{sel_disp}</strong> template — columns: Date | Time Interval |
                <strong>{sel_col}</strong>{aux_note}<br>
                Pre-filled for <strong>D+2: {d2.strftime('%d %B %Y')}</strong> · 96 rows
            </div>
            """, unsafe_allow_html=True)
            tmpl_bytes = generate_template(selected)
            st.download_button(
                label=f"⬇️ Download Template — {sel_disp}",
                data=tmpl_bytes,
                file_name=f"template_{selected}_{d2.strftime('%d-%m-%Y')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="prelogin_tmpl",
                use_container_width=False,
            )
        return

    # ── Logged in ─────────────────────────────────────────────────────────
    plant_id = st.session_state.logged_in_plant
    pid, display, col_header, aux_col, group, unit = PLANT_BY_ID[plant_id]

    col_title, col_logout = st.columns([4, 1])
    with col_title:
        color = COLORS.get(plant_id, "#3B82F6")
        st.markdown(f"""
        <div style='display:flex;align-items:center;gap:0.6rem;margin-bottom:1rem'>
            <div style='width:12px;height:12px;border-radius:50%;background:{color}'></div>
            <span style='font-family:DM Sans,sans-serif;font-size:1.1rem;font-weight:600;color:#1e293b'>
                Logged in as: {display}
            </span>
            <span style='background:#f1f5f9;color:#64748b;font-size:0.75rem;padding:2px 8px;
                         border-radius:12px;font-family:DM Sans,sans-serif'>{group}</span>
        </div>
        """, unsafe_allow_html=True)
    with col_logout:
        if st.button("Logout", use_container_width=True):
            st.session_state.logged_in_plant = None
            st.rerun()

    # ── Template download ──────────────────────────────────────────────────
    st.markdown("#### 📥 Download Data Template")
    d2_str = _d2_date().strftime("%d-%m-%Y")
    aux_note = f" + {aux_col}" if aux_col else ""
    st.markdown(f"""
    <div style='background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;
                padding:0.8rem 1rem;margin-bottom:0.8rem;font-family:DM Sans,sans-serif;
                font-size:0.85rem;color:#166534'>
        📋 Template columns: <strong>Date</strong> | <strong>Time Interval</strong> | 
        <strong>{col_header}</strong>{aux_note}<br>
        Date pre-filled as <strong>D+2: {d2_str}</strong> · 96 rows (00:00–24:00)
    </div>
    """, unsafe_allow_html=True)

    if OPENPYXL_OK:
        tmpl_bytes = generate_template(plant_id)
        st.download_button(
            label=f"⬇️  Download Template — {display}",
            data=tmpl_bytes,
            file_name=f"template_{plant_id}_{d2_str}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=False,
        )
    else:
        st.warning("openpyxl not installed — template download unavailable.")

    # ── Last submission ───────────────────────────────────────────────────
    last = load_last_entry(plant_id)
    if last is not None:
        last_date = last["Date"].max().strftime("%d %B %Y")
        avg_val = last["Value"].mean()
        max_val = last["Value"].max()
        aux_str = ""
        if "Auxiliary" in last.columns and last["Auxiliary"].notna().any():
            avg_aux = last["Auxiliary"].mean()
            aux_str = f"<div style='color:#64748b;font-size:0.9rem'>Avg Aux: <strong>{avg_aux:.2f} {unit}</strong></div>"
        st.markdown(f"""
        <div style='background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
                    padding:1rem 1.2rem;margin:1rem 0;font-family:DM Sans,sans-serif'>
            <div style='font-size:0.8rem;color:#94a3b8;margin-bottom:0.3rem'>LAST SUBMISSION</div>
            <div style='display:flex;gap:2rem;align-items:center;flex-wrap:wrap'>
                <div><span style='font-size:1rem;font-weight:600;color:#1e293b'>{last_date}</span></div>
                <div style='color:#64748b;font-size:0.9rem'>Avg: <strong>{avg_val:.2f} {unit}</strong></div>
                <div style='color:#64748b;font-size:0.9rem'>Peak: <strong>{max_val:.2f} {unit}</strong></div>
                <div style='color:#64748b;font-size:0.9rem'>Rows: <strong>{len(last)}</strong></div>
                {aux_str}
            </div>
        </div>
        """, unsafe_allow_html=True)

        with st.expander("Preview last submission data"):
            show = last[["Date", "Time Block", "Value"]].copy()
            show["Time Interval"] = show["Time Block"].apply(_tb_to_label)
            show["Date"] = show["Date"].dt.strftime("%d-%m-%Y")
            cols_show = ["Date", "Time Interval", col_header]
            show = show.rename(columns={"Value": col_header})
            if "Auxiliary" in last.columns and last["Auxiliary"].notna().any():
                show[aux_col] = last["Auxiliary"].values
                cols_show.append(aux_col)
            st.dataframe(show[cols_show], use_container_width=True, hide_index=True)
    else:
        st.info("No previous data found. Download the template, fill in your values, and upload below.")

    # ── Upload ───────────────────────────────────────────────────────────
    st.markdown("#### 📂 Upload Today's Data")
    aux_note2 = f", <strong>{aux_col}</strong>" if aux_col else ""
    st.markdown(f"""
    <div style='background:#fefce8;border:1px solid #fde68a;border-radius:8px;
                padding:0.8rem 1rem;margin-bottom:1rem;font-family:DM Sans,sans-serif;
                font-size:0.85rem;color:#92400e'>
        📋 Required columns: <strong>Date</strong>, <strong>Time Interval</strong>, 
        <strong>{col_header}</strong>{aux_note2}<br>
        Accepted: <strong>.xlsx</strong> or <strong>.csv</strong> &nbsp;|&nbsp; 96 rows
    </div>
    """, unsafe_allow_html=True)

    uploaded = st.file_uploader(
        f"Upload file for {display}",
        type=["xlsx", "xls", "csv"],
    )

    if uploaded:
        df_parsed, err = parse_upload(uploaded, plant_id)
        if err:
            st.error(f"❌ {err}")
        else:
            dates_found = df_parsed["Date"].dt.strftime("%d %B %Y").unique()
            st.success(f"✅ Parsed {len(df_parsed)} rows for: {', '.join(dates_found)}")

            preview = df_parsed[["Date", "Time Block", "Value"]].copy()
            preview["Time Interval"] = preview["Time Block"].apply(_tb_to_label)
            preview["Date"] = preview["Date"].dt.strftime("%d-%m-%Y")
            preview = preview.rename(columns={"Value": col_header})
            show_cols = ["Date", "Time Interval", col_header]
            if "Auxiliary" in df_parsed.columns and df_parsed["Auxiliary"].notna().any():
                preview[aux_col] = df_parsed["Auxiliary"].values
                show_cols.append(aux_col)

            with st.expander("Preview parsed data", expanded=True):
                st.dataframe(preview[show_cols].head(20), use_container_width=True, hide_index=True)
                if len(preview) > 20:
                    st.caption(f"... and {len(preview) - 20} more rows")

            col_a, col_b = st.columns([1, 3])
            with col_a:
                if st.button("✅ Confirm & Save", type="primary", use_container_width=True):
                    save_data(plant_id, df_parsed)
                    st.success("Data saved successfully!")
                    st.balloons()
                    st.rerun()

    if last is not None and len(last) > 0:
        st.markdown("#### 📈 Your Latest Data")
        fig = make_line_chart(last, plant_id, f"{display} — Last Submission")
        st.plotly_chart(fig, use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
# PAGE: TEMPLATE DOWNLOAD (ALL PLANTS)
# ─────────────────────────────────────────────────────────────────────────────

def page_templates():
    st.markdown("""
    <div style='margin-bottom:1.5rem'>
        <h2 style='font-family:DM Sans,sans-serif;color:#1e293b;font-size:1.6rem;margin:0'>
            📋 Excel Templates
        </h2>
        <p style='color:#64748b;font-family:DM Sans,sans-serif;margin-top:0.3rem'>
            Download pre-filled templates for each plant. Date is set to D+2 with 96 time-interval rows.
        </p>
    </div>
    """, unsafe_allow_html=True)

    d2 = _d2_date()
    st.info(f"📅 Templates pre-filled for **D+2 date: {d2.strftime('%d %B %Y')}**")

    groups = {}
    for p in PLANTS:
        g = p[4]
        groups.setdefault(g, []).append(p)

    for grp, plants_list in groups.items():
        st.markdown(f"##### {grp} Plants")
        cols = st.columns(min(len(plants_list), 3))
        for ci, (pid, display, col_header, aux_col, grp2, unit) in enumerate(plants_list):
            with cols[ci % 3]:
                aux_badge = f"<br><span style='font-size:0.72rem;color:#7c3aed'>+ {aux_col}</span>" if aux_col else ""
                st.markdown(f"""
                <div style='background:white;border:1px solid #e2e8f0;border-radius:10px;
                            padding:0.8rem;margin-bottom:0.5rem;font-family:DM Sans,sans-serif;
                            border-top:3px solid {COLORS.get(pid,"#888")}'>
                    <div style='font-weight:600;color:#1e293b;font-size:0.95rem'>{display}</div>
                    <div style='font-size:0.78rem;color:#64748b'>{col_header}{aux_badge}</div>
                </div>
                """, unsafe_allow_html=True)
                if OPENPYXL_OK:
                    tmpl_bytes = generate_template(pid)
                    d2_str = d2.strftime("%d-%m-%Y")
                    st.download_button(
                        label="⬇️ Download",
                        data=tmpl_bytes,
                        file_name=f"template_{pid}_{d2_str}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"tmpl_{pid}",
                        use_container_width=True,
                    )
                else:
                    st.warning("openpyxl needed")

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

    all_data = {p[0]: load_data(p[0]) for p in PLANTS}
    plants_with_data = {k: v for k, v in all_data.items() if v is not None and not v.empty}

    if not plants_with_data:
        st.warning("No data available yet. Plants need to submit their data first.")
        return

    # ── Date selector ──────────────────────────────────────────────────────
    all_dates = sorted(set(
        d for df in plants_with_data.values() for d in df["Date"].unique()
    ), reverse=True)
    date_options_str = [pd.to_datetime(d).strftime("%d %B %Y") for d in all_dates]
    selected_date_str = st.selectbox("📅 View Date", date_options_str, key="dash_date")
    selected_date = pd.to_datetime(selected_date_str, format="%d %B %Y")

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 1 — INDIVIDUAL SUMMARY CARDS
    # ─────────────────────────────────────────────────────────────────────
    st.markdown("#### Summary — Individual Plants")
    card_cols = st.columns(5)
    for i, (plant_id, df) in enumerate(plants_with_data.items()):
        _, display, _, _, group, unit = PLANT_BY_ID[plant_id]
        sub = df[df["Date"] == selected_date]
        if sub.empty:
            sub = df[df["Date"] == df["Date"].max()]
        avg = sub["Value"].mean()
        color = COLORS.get(plant_id, "#3B82F6")
        with card_cols[i % 5]:
            st.markdown(f"""
            <div style='background:white;border:1px solid #e2e8f0;border-top:3px solid {color};
                        border-radius:10px;padding:0.9rem;margin-bottom:0.8rem;font-family:DM Sans,sans-serif'>
                <div style='font-size:0.72rem;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em'>{group}</div>
                <div style='font-size:0.95rem;font-weight:700;color:#1e293b;margin:0.2rem 0'>{display}</div>
                <div style='font-size:1.3rem;font-weight:800;color:{color}'>{avg:.1f}</div>
                <div style='font-size:0.75rem;color:#94a3b8'>{unit} avg</div>
            </div>
            """, unsafe_allow_html=True)

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 2 — TOTALS CARDS
    # ─────────────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 📊 Totals Overview")

    totals = compute_totals(plants_with_data, selected_date)

    TOTAL_META = {
        "Total WCL":        ("WCL Load", "#EC4899",  "Pipe + Steel + ATSPL + WDIPL + WASCO"),
        "Total WLL":        ("WLL Load",  "#3B82F6",  "WLL + WHSL"),
        "Total Auxiliary":  ("Auxiliary", "#8B5CF6",  "80MW Aux + 43MW Aux + Solar Aux"),
        "Total Load":       ("Net Load",  "#1E293B",  "WCL + WLL + Auxiliary"),
        "Total Generation": ("Generation","#F97316",  "80MW + 43MW + Solar"),
    }

    total_cols = st.columns(5)
    for i, (key, (label, color, tooltip)) in enumerate(TOTAL_META.items()):
        s = totals.get(key)
        avg = avg_or_none(s)
        val_str = f"{avg:.1f}" if avg is not None else "—"
        with total_cols[i]:
            st.markdown(f"""
            <div style='background:white;border:2px solid {color};border-radius:10px;
                        padding:0.9rem;margin-bottom:0.8rem;font-family:DM Sans,sans-serif;
                        box-shadow:0 2px 8px rgba(0,0,0,0.06)'>
                <div style='font-size:0.72rem;color:#94a3b8;text-transform:uppercase;
                             letter-spacing:0.05em'>{label}</div>
                <div style='font-size:0.9rem;font-weight:700;color:#1e293b;margin:0.2rem 0'>{key}</div>
                <div style='font-size:1.5rem;font-weight:800;color:{color}'>{val_str}</div>
                <div style='font-size:0.7rem;color:#94a3b8;margin-top:2px'>{tooltip}</div>
            </div>
            """, unsafe_allow_html=True)

    # ── Totals Chart ───────────────────────────────────────────────────────
    total_color_map = {
        "Total WCL":        "#EC4899",
        "Total WLL":        "#3B82F6",
        "Total Auxiliary":  "#8B5CF6",
        "Total Load":       "#1E293B",
        "Total Generation": "#F97316",
    }
    valid_totals = {k: v for k, v in totals.items() if v is not None}
    if valid_totals:
        fig_totals = make_total_chart(valid_totals, "Totals — All Groups", total_color_map)
        st.plotly_chart(fig_totals, use_container_width=True)

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 3 — ALL PLANTS OVERVIEW CHART
    # ─────────────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### All Plants Overview")
    fig_overview = make_dashboard_overview(plants_with_data)
    st.plotly_chart(fig_overview, use_container_width=True)

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 4 — DETAILED PLANT CHARTS
    # ─────────────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Detailed Plant Charts")

    group_filter = st.radio(
        "Plant Group", ["All", "Generation", "WLL", "WCL"],
        horizontal=True,
    )

    filtered_plants = [
        (pid, df) for pid, df in plants_with_data.items()
        if group_filter == "All" or PLANT_BY_ID[pid][4] == group_filter
    ]

    if not filtered_plants:
        st.info("No data for selected filters.")
    else:
        for i in range(0, len(filtered_plants), 2):
            row_cols = st.columns(2)
            for j, (plant_id, df) in enumerate(filtered_plants[i:i+2]):
                _, display, _, _, group, unit = PLANT_BY_ID[plant_id]
                with row_cols[j]:
                    date_df = df[df["Date"] == selected_date]
                    is_fallback = False
                    display_date = selected_date
                    if date_df.empty:
                        last_available = df["Date"].max()
                        if pd.notnull(last_available):
                            date_df = df[df["Date"] == last_available]
                            display_date = last_available
                            is_fallback = True

                    if date_df.empty:
                        st.markdown(f"""
                        <div style='background:#f8fafc;border:1px dashed #cbd5e1;border-radius:10px;
                                    padding:2rem;text-align:center;color:#94a3b8'>
                            <strong>{display}</strong><br>No data available
                        </div>""", unsafe_allow_html=True)
                    else:
                        if is_fallback:
                            st.markdown(f"""
                            <div style='background:#fffbeb;border:1px solid #fef3c7;border-radius:6px;
                                        padding:4px 10px;margin-bottom:8px;font-size:0.75rem;color:#92400e'>
                                ⚠️ Showing latest data from <b>{pd.to_datetime(display_date).strftime('%d %b %Y')}</b>
                            </div>""", unsafe_allow_html=True)
                        fig = make_line_chart(date_df, plant_id, display)
                        st.plotly_chart(fig, use_container_width=True)

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 5 — STATISTICS TABLE
    # ─────────────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Daily Statistics Table")

    rows = []
    for plant_id, df in plants_with_data.items():
        _, display, _, _, group, unit = PLANT_BY_ID[plant_id]
        for d in sorted(df["Date"].unique(), reverse=True)[:7]:
            sub = df[df["Date"] == d]
            row = {
                "Plant": display, "Group": group,
                "Date":  pd.to_datetime(d).strftime("%d %b %Y"),
                "Rows":  len(sub),
                f"Avg ({unit})": round(sub["Value"].mean(), 2),
                f"Max ({unit})": round(sub["Value"].max(), 2),
                f"Min ({unit})": round(sub["Value"].min(), 2),
            }
            if "Auxiliary" in sub.columns and sub["Auxiliary"].notna().any():
                row["Avg Aux (MW)"] = round(sub["Auxiliary"].mean(), 2)
            rows.append(row)

    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 6 — COMPARISON CHART
    # ─────────────────────────────────────────────────────────────────────
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
            sub = df[df["Date"] == selected_date]
            if sub.empty:
                sub = df[df["Date"] == df["Date"].max()]
            sub = sub.sort_values("Time Block")
            _, display, _, _, _, unit = PLANT_BY_ID[pid]
            x_labels = sub["Time Block"].apply(_tb_to_label)
            fig_compare.add_trace(go.Scatter(
                x=x_labels, y=sub["Value"],
                mode="lines", name=display,
                line=dict(color=COLORS.get(pid, "#888"), width=2.5),
                hovertemplate=f"{display}: %{{y:.2f}} {unit}<extra></extra>",
            ))
        fig_compare.update_layout(
            title=dict(text="Plant Comparison", font=dict(size=15, family="DM Sans", color="#1e293b"), x=0.02),
            xaxis=dict(title="Time Interval", gridcolor="#f1f5f9",
                       tickfont=dict(family="DM Sans", size=9), tickangle=-45, nticks=12),
            yaxis=dict(title="MW", gridcolor="#f1f5f9", tickfont=dict(family="DM Sans", size=11)),
            plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
            legend=dict(font=dict(family="DM Sans", size=11)),
            margin=dict(l=50, r=20, t=60, b=70), height=400,
        )
        st.plotly_chart(fig_compare, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE: CONSOLIDATED VIEW
# ─────────────────────────────────────────────────────────────────────────────

def page_consolidated():
    st.markdown("""
    <div style='margin-bottom:1.5rem'>
        <h2 style='font-family:DM Sans,sans-serif;color:#1e293b;font-size:1.6rem;margin:0'>
            📑 Consolidated Data
        </h2>
        <p style='color:#64748b;font-family:DM Sans,sans-serif;margin-top:0.3rem'>
            All plants combined into one table — latest available data used where a date is missing.
        </p>
    </div>
    """, unsafe_allow_html=True)

    # Load all plant data
    all_data = {p[0]: load_data(p[0]) for p in PLANTS}
    plants_with_data = {k: v for k, v in all_data.items() if v is not None and not v.empty}

    if not plants_with_data:
        st.warning("No data available yet.")
        return

    # ── Date selector ──────────────────────────────────────────────────────
    all_dates = sorted(set(
        d for df in plants_with_data.values() for d in df["Date"].unique()
    ), reverse=True)
    date_options_str = [pd.to_datetime(d).strftime("%d %B %Y") for d in all_dates]
    selected_date_str = st.selectbox("📅 View Date", date_options_str, key="cons_date")
    selected_date = pd.to_datetime(selected_date_str, format="%d %B %Y")

    # ── Build consolidated rows indexed by Time Block 1-96 ─────────────────
    def get_plant_series(plant_id, col="Value") -> pd.Series:
        """Return a Series indexed 1-96. Falls back to latest date if selected_date missing."""
        df = plants_with_data.get(plant_id)
        if df is None or df.empty:
            return pd.Series(dtype=float)
        sub = df[df["Date"] == selected_date]
        if sub.empty:
            # fallback to most recent date
            sub = df[df["Date"] == df["Date"].max()]
        if sub.empty:
            return pd.Series(dtype=float)
        if col == "Auxiliary":
            if "Auxiliary" not in sub.columns:
                return pd.Series(dtype=float)
            return sub.set_index("Time Block")["Auxiliary"]
        return sub.set_index("Time Block")["Value"]

    idx = list(range(1, 97))

    rows = []
    for tb in idx:
        def v(pid, col="Value"):
            s = get_plant_series(pid, col)
            return round(s.get(tb, float("nan")), 3) if not s.empty else float("nan")

        gen_80   = v("GEN_80MW")
        gen_43   = v("GEN_43MW")
        gen_sol  = v("GEN_Solar")
        aux_80   = v("GEN_80MW", "Auxiliary")
        aux_43   = v("GEN_43MW", "Auxiliary")
        aux_sol  = v("GEN_Solar", "Auxiliary")

        total_gen = sum(x for x in [gen_80, gen_43, gen_sol] if pd.notna(x)) or float("nan")
        total_aux = sum(x for x in [aux_80, aux_43, aux_sol] if pd.notna(x)) or float("nan")

        wll  = v("WLL_WLL")
        whsl = v("WLL_WHSL")
        pipe = v("WCL_PIPE")
        stl  = v("WCL_STEEL")
        atspl= v("WCL_ATSPL")
        wdipl= v("WCL_WDIPL")
        wasco= v("WCL_WASCO")

        # WML = WLL + WHSL (total WLL group)
        wml_vals = [x for x in [wll, whsl] if pd.notna(x)]
        wml = sum(wml_vals) if wml_vals else float("nan")

        # Total WCL = pipe + steel + atspl + wdipl + wasco
        wcl_vals = [x for x in [pipe, stl, atspl, wdipl, wasco] if pd.notna(x)]
        total_wcl = sum(wcl_vals) if wcl_vals else float("nan")

        # Total Demand = Total WCL + Total WLL (WML) + Total Auxiliary
        demand_vals = [x for x in [total_wcl, wml, total_aux] if pd.notna(x)]
        total_demand = sum(demand_vals) if demand_vals else float("nan")

        rows.append({
            "Time Interval":        TIME_STAMPS[tb - 1],
            "80MW GENERATION":      gen_80,
            "43MW GENERATION":      gen_43,
            "SOLAR NET GENERATION": gen_sol,
            "TOTAL TG GENERATION":  round(total_gen, 3) if pd.notna(total_gen) else float("nan"),
            "80MW AUXILIARY":       aux_80,
            "43MW AUXILIARY":       aux_43,
            "SOLAR AUXILIARY":      aux_sol,
            "TOTAL AUXILIARY":      round(total_aux, 3) if pd.notna(total_aux) else float("nan"),
            "WLL":                  wll,
            "WHSL":                 whsl,
            "WCL PIPE DIVISION":    pipe,
            "WCL STEEL DIVISION":   stl,
            "ATSPL":                atspl,
            "WDIPL":                wdipl,
            "WASCO":                wasco,
            "WML":                  round(wml, 3) if pd.notna(wml) else float("nan"),
            "TOTAL DEMAND":         round(total_demand, 3) if pd.notna(total_demand) else float("nan"),
        })

    cons_df = pd.DataFrame(rows)

    # ── Fallback notice ────────────────────────────────────────────────────
    fallback_notes = []
    for pid, df in plants_with_data.items():
        _, disp, _, _, _, _ = PLANT_BY_ID[pid]
        if selected_date not in df["Date"].values:
            fb_date = pd.to_datetime(df["Date"].max()).strftime("%d %b %Y")
            fallback_notes.append(f"<b>{disp}</b>: using {fb_date}")

    if fallback_notes:
        st.markdown(f"""
        <div style='background:#fffbeb;border:1px solid #fde68a;border-radius:8px;
                    padding:0.7rem 1rem;margin-bottom:1rem;font-family:DM Sans,sans-serif;font-size:0.82rem;color:#92400e'>
            ⚠️ <strong>Fallback data used for:</strong> {" &nbsp;|&nbsp; ".join(fallback_notes)}
        </div>
        """, unsafe_allow_html=True)

    # ── Summary stat row ───────────────────────────────────────────────────
    numeric_cols = [c for c in cons_df.columns if c != "Time Interval"]
    summary = cons_df[numeric_cols].mean().round(2)
    scols = st.columns(3)
    highlights = [
        ("TOTAL AUXILIARY",    "#8B5CF6"),
        ("TOTAL TG GENERATION","#F97316"),
        ("TOTAL DEMAND",       "#1E293B"),
    ]
    for i, (col, color) in enumerate(highlights):
        val = summary.get(col, float("nan"))
        val_str = f"{val:.2f}" if pd.notna(val) else "—"
        with scols[i]:
            st.markdown(f"""
            <div style='background:white;border:2px solid {color};border-radius:10px;
                        padding:1rem 1.2rem;font-family:DM Sans,sans-serif;
                        box-shadow:0 2px 8px rgba(0,0,0,0.06)'>
                <div style='font-size:0.72rem;color:#94a3b8;text-transform:uppercase;
                             letter-spacing:0.06em;margin-bottom:0.2rem'>{col}</div>
                <div style='font-size:1.8rem;font-weight:800;color:{color}'>{val_str}</div>
                <div style='font-size:0.75rem;color:#94a3b8'>MW avg across 96 blocks</div>
            </div>
            """, unsafe_allow_html=True)

    # ── Main table ─────────────────────────────────────────────────────────
    st.markdown(f"##### All 96 Time Blocks — {selected_date_str}")
    st.dataframe(
        cons_df,
        use_container_width=True,
        hide_index=True,
        height=500,
        column_config={c: st.column_config.NumberColumn(format="%.3f") for c in numeric_cols},
    )

    # ── Download consolidated as Excel ─────────────────────────────────────
    if OPENPYXL_OK:
        def to_excel(df: pd.DataFrame) -> bytes:
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Consolidated")
                ws = writer.sheets["Consolidated"]
                # Header styling
                hdr_fill = PatternFill("solid", fgColor="1E3A5F")
                hdr_font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
                for cell in ws[1]:
                    cell.fill = hdr_fill
                    cell.font = hdr_font
                    cell.alignment = Alignment(horizontal="center")
                for col_cells in ws.columns:
                    max_len = max(len(str(c.value or "")) for c in col_cells)
                    ws.column_dimensions[col_cells[0].column_letter].width = max(12, max_len + 2)
                ws.freeze_panes = "B2"
            return buf.getvalue()

        xl_bytes = to_excel(cons_df)
        d_str = selected_date.strftime("%d-%m-%Y")
        st.download_button(
            label="⬇️ Download Consolidated Excel",
            data=xl_bytes,
            file_name=f"consolidated_{d_str}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    # ── Line chart of key totals ───────────────────────────────────────────
    st.markdown("---")
    st.markdown("##### Totals Chart")
    chart_cols = {
        "TOTAL TG GENERATION": "#F97316",
        "TOTAL AUXILIARY":     "#8B5CF6",
        "TOTAL DEMAND":        "#1E293B",
    }
    fig = go.Figure()
    for col, color in chart_cols.items():
        series = cons_df[col].dropna()
        if series.empty:
            continue
        fig.add_trace(go.Scatter(
            x=cons_df.loc[series.index, "Time Interval"],
            y=series.values,
            mode="lines", name=col,
            line=dict(color=color, width=2.5),
            hovertemplate=f"<b>{col}</b><br>%{{x}}<br>%{{y:.3f}} MW<extra></extra>",
        ))
    fig.update_layout(
        xaxis=dict(title="Time Interval", gridcolor="#f1f5f9",
                   tickfont=dict(family="DM Sans", size=9), tickangle=-45, nticks=12),
        yaxis=dict(title="MW", gridcolor="#f1f5f9"),
        plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
        legend=dict(font=dict(family="DM Sans", size=11)),
        margin=dict(l=50, r=20, t=40, b=70), height=380,
    )
    st.plotly_chart(fig, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE: LIVE GENERATION vs DEMAND
# ─────────────────────────────────────────────────────────────────────────────

import urllib.request
import urllib.error

LIVE_API_URL = "http://localhost:8765/live"

def _fetch_live() -> Optional[dict]:
    try:
        with urllib.request.urlopen(LIVE_API_URL, timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return None

def _get_demand_for_block(block_index: int) -> Optional[float]:
    """Sum WCL + WLL + Auxiliary for a given 1-96 block using latest available data."""
    all_data = {p[0]: load_data(p[0]) for p in PLANTS}
    plants_with_data = {k: v for k, v in all_data.items() if v is not None and not v.empty}
    if not plants_with_data:
        return None

    total = 0.0
    found_any = False

    load_plants = ["WCL_PIPE","WCL_STEEL","WCL_ATSPL","WCL_WDIPL","WCL_WASCO",
                   "WLL_WLL","WLL_WHSL"]
    aux_plants  = ["GEN_80MW","GEN_43MW","GEN_Solar"]

    for pid in load_plants:
        df = plants_with_data.get(pid)
        if df is None: continue
        sub = df[df["Date"] == df["Date"].max()]
        row = sub[sub["Time Block"] == block_index]
        if not row.empty:
            total += float(row["Value"].iloc[0])
            found_any = True

    for pid in aux_plants:
        df = plants_with_data.get(pid)
        if df is None: continue
        sub = df[df["Date"] == df["Date"].max()]
        row = sub[sub["Time Block"] == block_index]
        if not row.empty and "Auxiliary" in row.columns:
            v = row["Auxiliary"].iloc[0]
            if pd.notna(v):
                total += float(v)
                found_any = True

    return round(total, 2) if found_any else None


def page_live():
    st.markdown("""
    <div style='margin-bottom:1rem'>
        <h2 style='font-family:DM Sans,sans-serif;color:#1e293b;font-size:1.6rem;margin:0'>
            ⚡ Live Generation vs Demand
        </h2>
        <p style='color:#64748b;font-family:DM Sans,sans-serif;margin-top:0.3rem'>
            Real-time generation from the API compared against static scheduled demand.
            Make sure <code>mock_api.py</code> is running on port 8765.
        </p>
    </div>
    """, unsafe_allow_html=True)

    # ── API connection check ─────────────────────────────────────────────
    col_status, col_refresh, col_auto = st.columns([3, 1, 2])

    with col_refresh:
        if st.button("🔄 Refresh Now", use_container_width=True):
            st.rerun()

    with col_auto:
        auto_refresh = st.toggle("Auto-refresh (15s)", value=False)

    # Auto-refresh using sleep + rerun — preserves session state / navigation
    if auto_refresh:
        import time as _time
        _time.sleep(15)
        st.rerun()

    # ── Fetch live data ──────────────────────────────────────────────────
    live = _fetch_live()

    with col_status:
        if live:
            st.markdown("""
            <div style='background:#f0fdf4;border:1px solid #86efac;border-radius:8px;
                        padding:0.5rem 1rem;font-family:DM Sans,sans-serif;font-size:0.85rem;color:#166534;
                        display:inline-block'>
                🟢 <strong>API Connected</strong> — Live data streaming
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style='background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;
                        padding:0.5rem 1rem;font-family:DM Sans,sans-serif;font-size:0.85rem;color:#991b1b;
                        display:inline-block'>
                🔴 <strong>API Offline</strong> — Run <code>python mock_api.py</code> to start
            </div>
            """, unsafe_allow_html=True)
            st.info("Start the demo API with: `python mock_api.py`\n\nShowing demo snapshot data below.")
            # Use a static snapshot so the page is still useful
            live = {
                "timestamp": datetime.now().isoformat(),
                "time_block": "demo",
                "block_index": (datetime.now().hour * 4 + datetime.now().minute // 15) + 1,
                "plants": {
                    "80MW":  {"generation": 58.4,  "auxiliary": 2.28},
                    "43MW":  {"generation": 37.2,  "auxiliary": 1.52},
                    "Solar": {"generation": 12.6,  "auxiliary": 0.38},
                },
                "totals": {
                    "total_generation": 108.2,
                    "total_auxiliary":  4.18,
                }
            }

    block_idx  = live["block_index"]
    time_block = live["time_block"]
    ts         = live["timestamp"][:19].replace("T", "  ")
    plants_live = live["plants"]
    totals_live = live["totals"]

    live_gen   = totals_live["total_generation"]
    live_aux   = totals_live["total_auxiliary"]

    # ── Get demand for this block ────────────────────────────────────────
    demand = _get_demand_for_block(block_idx)
    diff   = round(live_gen - demand, 2) if demand is not None else None

    # ── Top KPI strip ────────────────────────────────────────────────────
    st.markdown(f"<div style='font-size:0.8rem;color:#94a3b8;font-family:DM Sans;margin:0.8rem 0 0.3rem'>Last polled: {ts} &nbsp;|&nbsp; Block {block_idx}/96 &nbsp;|&nbsp; {time_block}</div>", unsafe_allow_html=True)

    k1, k2, k3, k4 = st.columns(4)

    def kpi_card(col, label, value, unit, color, sub=""):
        with col:
            st.markdown(f"""
            <div style='background:white;border:2px solid {color};border-radius:10px;
                        padding:1rem 1.2rem;font-family:DM Sans,sans-serif;
                        box-shadow:0 2px 8px rgba(0,0,0,0.05)'>
                <div style='font-size:0.7rem;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em'>{label}</div>
                <div style='font-size:1.7rem;font-weight:800;color:{color};line-height:1.2'>{value}</div>
                <div style='font-size:0.72rem;color:#94a3b8'>{unit}{(" · " + sub) if sub else ""}</div>
            </div>
            """, unsafe_allow_html=True)

    kpi_card(k1, "Live Generation", f"{live_gen:.2f}", "MW",  "#F97316", "80MW+43MW+Solar")
    kpi_card(k2, "Live Auxiliary",  f"{live_aux:.2f}", "MW",  "#8B5CF6", "from gen plants")

    if demand is not None:
        kpi_card(k3, "Scheduled Demand", f"{demand:.2f}", "MW", "#3B82F6", f"block {block_idx}")
        # Surplus/deficit
        if diff > 0:
            kpi_card(k4, "Surplus ▲", f"+{diff:.2f}", "MW", "#16A34A", "Gen > Demand")
        elif diff < 0:
            kpi_card(k4, "Deficit ▼", f"{diff:.2f}",  "MW", "#DC2626", "Gen < Demand")
        else:
            kpi_card(k4, "Balanced ✓", f"{diff:.2f}", "MW", "#64748B", "Gen = Demand")
    else:
        kpi_card(k3, "Scheduled Demand", "—", "MW", "#3B82F6", "no data uploaded")
        kpi_card(k4, "Surplus / Deficit", "—", "MW", "#64748B", "upload demand data")

    # ── Per-plant live breakdown ─────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Per-Plant Live Readings")

    PLANT_COLORS = {"80MW": "#F97316", "43MW": "#EAB308", "Solar": "#22C55E"}
    pcols = st.columns(3)
    for i, (name, color) in enumerate(PLANT_COLORS.items()):
        p = plants_live.get(name, {})
        g = p.get("generation", 0)
        a = p.get("auxiliary",  0)
        with pcols[i]:
            st.markdown(f"""
            <div style='background:white;border:1px solid #e2e8f0;border-top:4px solid {color};
                        border-radius:10px;padding:1rem;font-family:DM Sans,sans-serif'>
                <div style='font-weight:700;font-size:1rem;color:#1e293b;margin-bottom:0.6rem'>{name} Plant</div>
                <div style='display:flex;justify-content:space-between;margin-bottom:0.3rem'>
                    <span style='color:#64748b;font-size:0.85rem'>Generation</span>
                    <span style='font-weight:700;color:{color};font-size:1rem'>{g:.2f} MW</span>
                </div>
                <div style='display:flex;justify-content:space-between'>
                    <span style='color:#64748b;font-size:0.85rem'>Auxiliary</span>
                    <span style='font-weight:600;color:#8B5CF6;font-size:0.95rem'>{a:.2f} MW</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

    # ── All-blocks comparison chart: static demand vs live snapshot ──────
    st.markdown("---")
    st.markdown("#### Scheduled Demand Profile vs Live Generation")

    all_data = {p[0]: load_data(p[0]) for p in PLANTS}
    plants_with_data = {k: v for k, v in all_data.items() if v is not None and not v.empty}

    if plants_with_data:
        # Build demand curve for all 96 blocks from latest data
        demand_rows = []
        load_plants = ["WCL_PIPE","WCL_STEEL","WCL_ATSPL","WCL_WDIPL","WCL_WASCO",
                       "WLL_WLL","WLL_WHSL"]
        aux_plants  = ["GEN_80MW","GEN_43MW","GEN_Solar"]

        idx = pd.RangeIndex(1, 97)
        demand_series = pd.Series(0.0, index=idx)
        has_demand = False

        for pid in load_plants:
            df = plants_with_data.get(pid)
            if df is None: continue
            sub = df[df["Date"] == df["Date"].max()].set_index("Time Block")["Value"]
            demand_series = demand_series.add(sub.reindex(idx, fill_value=0), fill_value=0)
            has_demand = True

        for pid in aux_plants:
            df = plants_with_data.get(pid)
            if df is None: continue
            sub = df[df["Date"] == df["Date"].max()]
            if "Auxiliary" in sub.columns:
                a = sub.set_index("Time Block")["Auxiliary"].dropna()
                demand_series = demand_series.add(a.reindex(idx, fill_value=0), fill_value=0)

        if has_demand:
            x_labels = idx.map(_tb_to_label)

            # Build simulated generation only up to the current block; NaN for future blocks
            import math as _math
            from mock_api import _interpolate, GEN_80MW_SHAPE, GEN_43MW_SHAPE, GEN_SOLAR_SHAPE
            sim_gen = []
            for tb in range(1, 97):
                if tb <= block_idx:
                    h_f = ((tb - 1) * 15) / 60.0
                    g80  = _interpolate(GEN_80MW_SHAPE, h_f)
                    g43  = _interpolate(GEN_43MW_SHAPE, h_f)
                    gsol = max(0, _interpolate(GEN_SOLAR_SHAPE, h_f))
                    sim_gen.append(round(g80 + g43 + gsol, 2))
                else:
                    sim_gen.append(float("nan"))  # future blocks — no line drawn

            sim_series = pd.Series(sim_gen, index=idx)

            # Overlay actual live point
            fig = go.Figure()

            # Demand curve
            fig.add_trace(go.Scatter(
                x=x_labels, y=demand_series.values,
                mode="lines", name="Scheduled Demand",
                line=dict(color="#3B82F6", width=2.5),
                hovertemplate="<b>Demand</b><br>%{x}<br>%{y:.2f} MW<extra></extra>",
            ))

            # Simulated generation profile
            fig.add_trace(go.Scatter(
                x=x_labels, y=sim_series.values,
                mode="lines", name="Generation Profile",
                line=dict(color="#F97316", width=2, dash="dot"),
                hovertemplate="<b>Generation</b><br>%{x}<br>%{y:.2f} MW<extra></extra>",
            ))

            # Surplus / deficit fill — only for blocks where generation data exists
            surplus = sim_series - demand_series
            surplus_display = surplus.where(sim_series.notna())
            fig.add_trace(go.Scatter(
                x=x_labels, y=surplus_display.values,
                mode="lines", name="Surplus (+) / Deficit (−)",
                line=dict(color="#16A34A", width=1.5),
                fill="tozeroy",
                fillcolor="rgba(22,163,74,0.08)",
                hovertemplate="<b>Net</b><br>%{x}<br>%{y:.2f} MW<extra></extra>",
            ))

            # Live point marker
            live_x = _tb_to_label(block_idx)
            fig.add_trace(go.Scatter(
                x=[live_x], y=[live_gen],
                mode="markers", name="Live Now",
                marker=dict(color="#DC2626", size=12, symbol="star",
                            line=dict(color="white", width=2)),
                hovertemplate=f"<b>LIVE</b><br>{live_x}<br>{live_gen:.2f} MW<extra></extra>",
            ))

            fig.update_layout(
                xaxis=dict(title="Time Interval", gridcolor="#f1f5f9",
                           tickfont=dict(family="DM Sans", size=9), tickangle=-45, nticks=12),
                yaxis=dict(title="MW", gridcolor="#f1f5f9"),
                plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
                legend=dict(font=dict(family="DM Sans", size=11),
                            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=50, r=20, t=50, b=70), height=420,
            )
            st.plotly_chart(fig, use_container_width=True)

            # ── Block-by-block difference table (last 10 completed blocks) ──
            st.markdown("#### Block Comparison Table")
            current_b = block_idx
            rows_table = []
            for tb in range(max(1, current_b - 11), current_b + 1):
                d_val = demand_series.get(tb, float("nan"))
                g_val = sim_series.get(tb, float("nan"))
                diff_val = round(g_val - d_val, 2) if pd.notna(d_val) and pd.notna(g_val) else float("nan")
                status = ""
                if pd.notna(diff_val):
                    if diff_val > 1:   status = "🟢 Surplus"
                    elif diff_val < -1: status = "🔴 Deficit"
                    else:               status = "🟡 Balanced"
                is_live = (tb == current_b)
                rows_table.append({
                    "Block": tb,
                    "Time Interval": _tb_to_label(tb),
                    "Demand (MW)":   round(d_val, 2) if pd.notna(d_val) else None,
                    "Generation (MW)": round(g_val, 2) if pd.notna(g_val) else None,
                    "Diff (MW)":     diff_val,
                    "Status":        ("⚡ LIVE → " + status) if is_live else status,
                })
            st.dataframe(
                pd.DataFrame(rows_table),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Diff (MW)": st.column_config.NumberColumn(format="%.2f"),
                    "Demand (MW)": st.column_config.NumberColumn(format="%.2f"),
                    "Generation (MW)": st.column_config.NumberColumn(format="%.2f"),
                }
            )
    else:
        st.info("Upload demand data for at least one plant to see the comparison chart.")


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
