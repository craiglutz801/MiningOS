"""
Mining_OS dashboard: Minerals (editable), Areas of Focus (table + reports), Map.
"""

from __future__ import annotations

import os

import pandas as pd
import pydeck as pdk
import requests
import streamlit as st

API_URL = os.getenv("MINING_OS_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="Mining_OS", layout="wide", initial_sidebar_state="expanded")

def _api_get(path: str, **params):
    r = requests.get(f"{API_URL}{path}", params=params or None, timeout=60)
    r.raise_for_status()
    return r.json()

def _api_post(path: str, json: dict | None = None):
    r = requests.post(f"{API_URL}{path}", json=json, timeout=120)
    r.raise_for_status()
    return r.json()

def _api_put(path: str, json: dict):
    r = requests.put(f"{API_URL}{path}", json=json, timeout=60)
    r.raise_for_status()
    return r.json()

def _api_delete(path: str):
    r = requests.delete(f"{API_URL}{path}", timeout=60)
    r.raise_for_status()

# ---- Sidebar: page + global actions ----------------------------------------

with st.sidebar:
    st.title("Mining_OS")
    page = st.radio(
        "Page",
        ["Minerals of interest", "Areas of focus", "Map", "Candidates (legacy)"],
        label_visibility="collapsed",
    )
    st.divider()
    st.caption("Data actions")
    if st.button("Ingest data files → Areas of focus"):
        try:
            out = _api_post("/areas-of-focus/ingest")
            st.sidebar.success(f"Files: {out.get('files', 0)}, Rows: {out.get('rows', 0)}")
        except Exception as e:
            st.sidebar.error(str(e))
    if st.button("Email priority unpaid"):
        try:
            out = _api_post("/alerts/send-priority-unpaid")
            msg = out.get("message") or ""
            if out.get("email_sent") or out.get("sent"):
                st.sidebar.success(msg or f"Count: {out.get('count', 0)}")
            else:
                st.sidebar.warning(msg or f"Not sent. Count: {out.get('count', 0)}")
        except Exception as e:
            st.sidebar.error(str(e))

# ---- Minerals of interest (editable) ---------------------------------------

if page == "Minerals of interest":
    st.header("Minerals of interest")
    st.caption("Edit the list of priority minerals. Used for discovery and alerts.")
    try:
        minerals = _api_get("/minerals")
    except requests.ConnectionError:
        st.error(f"Cannot reach API at **{API_URL}**. Start the API first.")
        st.stop()
    except Exception as e:
        st.error(str(e))
        st.stop()

    df = pd.DataFrame(minerals)
    if df.empty:
        df = pd.DataFrame(columns=["id", "name", "sort_order", "updated_at"])
    edited = st.data_editor(
        df[["id", "name", "sort_order"]].copy(),
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "id": st.column_config.NumberColumn("ID", disabled=True),
            "name": st.column_config.TextColumn("Mineral name"),
            "sort_order": st.column_config.NumberColumn("Order"),
        },
    )

    col1, col2 = st.columns(2)
    with col1:
        new_name = st.text_input("Add mineral", placeholder="e.g. Lithium")
        if st.button("Add") and new_name.strip():
            try:
                r = requests.post(f"{API_URL}/minerals", params={"name": new_name.strip()}, timeout=60)
                r.raise_for_status()
                st.success("Added.")
                st.rerun()
            except Exception as e:
                st.error(str(e))
    with col2:
        del_id = st.number_input("Delete by ID", min_value=0, value=0, step=1)
        if st.button("Delete") and del_id:
            try:
                _api_delete(f"/minerals/{int(del_id)}")
                st.success("Deleted.")
                st.rerun()
            except Exception as e:
                st.error(str(e))

    if not df.empty:
        st.caption("To reorder or rename: use API or pgAdmin; or add/delete above.")

# ---- Areas of focus --------------------------------------------------------

if page == "Areas of focus":
    st.header("Areas of focus")
    st.caption("Claim/mine name, location (PLSS), mineral(s), status (paid/unpaid), reports and validity.")
    mineral_filter = st.text_input("Filter by mineral", placeholder="e.g. Uranium")
    status_filter = st.selectbox("Filter by status", ["", "paid", "unpaid", "unknown"], format_func=lambda x: x or "All")
    try:
        areas = _api_get(
            "/areas-of-focus",
            mineral=mineral_filter.strip() or None,
            status=status_filter.strip() or None,
            limit=500,
        )
    except requests.ConnectionError:
        st.error(f"Cannot reach API at **{API_URL}**.")
        st.stop()
    except Exception as e:
        st.error(str(e))
        st.stop()

    if not areas:
        st.info("No areas. Click **Ingest data files → Areas of focus** in the sidebar.")
        st.stop()

    # Build table
    rows = []
    for a in areas:
        rows.append({
            "id": a.get("id"),
            "Name": a.get("name"),
            "Location (PLSS)": a.get("location_plss"),
            "Minerals": ", ".join(a.get("minerals") or []),
            "Status": a.get("status") or "—",
            "Reports": ", ".join(a.get("report_links") or [])[:80] + ("…" if len((a.get("report_links") or [])) > 1 else ""),
            "Validity notes": (a.get("validity_notes") or "")[:60] + ("…" if len((a.get("validity_notes") or "")) > 60 else ""),
            "ROI": a.get("roi_score"),
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, height=400)

    # Detail + BLM check
    st.subheader("Detail & BLM check")
    area_id = st.number_input("Area ID", min_value=min(a["id"] for a in areas), max_value=max(a["id"] for a in areas), value=areas[0]["id"])
    try:
        detail = _api_get(f"/areas-of-focus/{int(area_id)}")
        st.json(detail)
        if detail.get("latitude") is not None and detail.get("longitude") is not None:
            if st.button("Check BLM (paid/unpaid) for this area"):
                try:
                    out = _api_post(f"/areas-of-focus/{int(area_id)}/check-blm")
                    st.success(str(out))
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
        else:
            st.caption("Add latitude/longitude to this area (e.g. via API or pgAdmin) to run BLM check.")
    except Exception as e:
        st.error(str(e))

# ---- Map --------------------------------------------------------------------

if page == "Map":
    st.header("Interactive map")
    st.caption("Areas of focus with coordinates; color by status.")
    try:
        areas = _api_get("/areas-of-focus", limit=1000)
    except requests.ConnectionError:
        st.error(f"Cannot reach API at **{API_URL}**.")
        st.stop()
    map_areas = [a for a in areas if a.get("latitude") is not None and a.get("longitude") is not None]
    if not map_areas:
        st.info("No areas have coordinates. Ingest Utah Dockets or add lat/lon to areas.")
        st.stop()

    df = pd.DataFrame([
        {
            "name": a.get("name"),
            "lat": a.get("latitude"),
            "lon": a.get("longitude"),
            "minerals": ", ".join(a.get("minerals") or []),
            "status": a.get("status") or "unknown",
        }
        for a in map_areas
    ])
    status_color = {"paid": [0, 200, 0], "unpaid": [200, 80, 80], "unknown": [120, 120, 120]}
    df["color_r"] = df["status"].map(lambda s: status_color.get((s or "").lower(), [120, 120, 120])[0])
    df["color_g"] = df["status"].map(lambda s: status_color.get((s or "").lower(), [120, 120, 120])[1])
    df["color_b"] = df["status"].map(lambda s: status_color.get((s or "").lower(), [120, 120, 120])[2])

    layer = pdk.Layer(
        "ScatterplotLayer",
        data=df,
        get_position="[lon, lat]",
        get_fill_color="[color_r, color_g, color_b, 180]",
        get_radius=800,
        pickable=True,
        auto_highlight=True,
    )
    view = pdk.ViewState(
        latitude=df["lat"].median(),
        longitude=df["lon"].median(),
        zoom=5,
        pitch=0,
    )
    st.pydeck_chart(
        pdk.Deck(
            layers=[layer],
            initial_view_state=view,
            tooltip={"text": "{name}\n{minerals}\nStatus: {status}"},
        )
    )

# ---- Candidates (legacy MVP) ------------------------------------------------

if page == "Candidates (legacy)":
    st.header("Candidates (legacy MVP)")
    st.caption("BLM × MRDS scored candidates from the original pipeline.")
    try:
        rows = _api_get("/candidates", limit=300)
    except requests.ConnectionError:
        st.error(f"Cannot reach API at **{API_URL}**.")
        st.stop()
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No candidates. Run pipeline: init-db, ingest, candidates.")
        st.stop()
    st.dataframe(
        df[["id", "score", "state_abbr", "serial_num", "claim_name", "trs", "mrds_hit_count", "commodities"]].head(100),
        use_container_width=True,
    )
