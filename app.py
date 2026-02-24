"""
app.py - Main Streamlit dashboard for Supplier Risk Monitoring
Run with: streamlit run app.py
"""

import os
import pandas as pd
import streamlit as st
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file (local dev)
load_dotenv()

# Internal modules
from database import init_db, get_all_suppliers, get_all_events
from upload import process_upload, get_sample_csv
from geocoding import geocode_suppliers
from events import refresh_all_events, should_auto_refresh
from scoring import run_scoring_engine, get_score_breakdown
from recommendations import get_recommendations
from predictions import get_predictions
from alternatives import find_alternatives, detect_countdown
from mapping import build_supplier_map
from alerts import dispatch_alerts

# ─── Page Config ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Supplier Risk Dashboard",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Custom CSS ───────────────────────────────────────────────────────────────

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'IBM Plex Sans', sans-serif;
    }

    .main { background-color: #0f172a; }

    .stApp { background-color: #0f172a; }

    /* Metric cards */
    [data-testid="metric-container"] {
        background: #1e293b;
        border: 1px solid #334155;
        border-radius: 10px;
        padding: 16px;
    }

    /* Section headers */
    .section-header {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.75rem;
        letter-spacing: 0.15em;
        text-transform: uppercase;
        color: #64748b;
        margin-bottom: 0.5rem;
    }

    /* Risk badge */
    .risk-high { color: #ef4444; font-weight: 700; }
    .risk-medium { color: #f59e0b; font-weight: 700; }
    .risk-low { color: #22c55e; font-weight: 700; }

    /* Top supplier card */
    .supplier-card {
        background: #1e293b;
        border-left: 4px solid #ef4444;
        border-radius: 6px;
        padding: 12px 16px;
        margin-bottom: 8px;
    }
    .supplier-card.medium { border-color: #f59e0b; }
    .supplier-card.low { border-color: #22c55e; }

    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #0f172a;
        border-right: 1px solid #1e293b;
    }

    /* Divider */
    hr { border-color: #1e293b; }

    /* DataFrame */
    [data-testid="stDataFrame"] { border: 1px solid #334155; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# ─── Initialize DB ────────────────────────────────────────────────────────────

init_db()

# ─── Sidebar ─────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🛡️ SupplierRisk")
    st.markdown('<p class="section-header">Configuration</p>', unsafe_allow_html=True)

    # API Keys
    with st.expander("🔑 API Keys", expanded=False):
        news_api_key = st.text_input(
            "NewsAPI Key",
            value=os.getenv("NEWS_API_KEY", ""),
            type="password",
            help="Get free key at newsapi.org"
        )
        weather_api_key = st.text_input(
            "OpenWeatherMap Key",
            value=os.getenv("OPENWEATHER_API_KEY", ""),
            type="password",
            help="Get free key at openweathermap.org"
        )
        openai_api_key = st.text_input(
            "OpenAI Key (Optional)",
            value=os.getenv("OPENAI_API_KEY", ""),
            type="password",
            help="Optional: enhances event parsing with GPT"
        )

    st.markdown("---")
    st.markdown('<p class="section-header">Data Controls</p>', unsafe_allow_html=True)

    # Auto-refresh toggle
    auto_refresh = st.toggle("⚡ Auto-refresh", value=False)
    refresh_interval = st.select_slider(
        "Refresh every",
        options=[5, 10, 15, 30, 60],
        value=10,
        format_func=lambda x: f"{x} min"
    )

    # Show last refresh time
    if "last_refresh" in st.session_state:
        elapsed = int((datetime.utcnow() - st.session_state["last_refresh"]).total_seconds() / 60)
        st.caption(f"🕐 Last refreshed {elapsed} min ago")

    # Manual fetch button
    if st.button("🔄 Fetch Events Now", use_container_width=True):
        suppliers_df = get_all_suppliers()
        if suppliers_df.empty:
            st.warning("Upload suppliers first.")
        else:
            countries = suppliers_df["country"].dropna().unique().tolist()
            llm_note = "🤖 + LLM filter" if openai_api_key else "keyword filter only"
            # Build supplier list for targeted feeds
            supplier_list = suppliers_df[["city","country"]].dropna().to_dict("records")
            with st.spinner(f"Scanning 60+ sources + {len(supplier_list)} supplier-targeted feeds · {llm_note}..."):
                rss, gdelt, newsapi, weather, fstats = refresh_all_events(
                    news_api_key, weather_api_key, countries,
                    openai_api_key, suppliers=supplier_list
                )
            approved = fstats.get("approved", 0)
            total    = fstats.get("total", 0)
            rej_l1   = fstats.get("rejected_l1", 0)
            rej_l2   = fstats.get("rejected_l2", 0)
            rej_l3   = fstats.get("rejected_l3", 0)
            llm_calls = fstats.get("llm_calls", 0)
            st.success(f"✅ {approved} events stored from {total} articles scanned")
            st.caption(f"Filtered out: {rej_l1} (no keywords) · {rej_l2} (blocklist) · {rej_l3} (LLM) · {llm_calls} LLM calls used")
            st.session_state["last_refresh"] = datetime.utcnow()
            st.rerun()

    # Auto-refresh logic — checks every minute if interval has passed
    if auto_refresh:
        import time as _time
        last = st.session_state.get("last_refresh", None)
        if should_auto_refresh(last, refresh_interval):
            suppliers_df = get_all_suppliers()
            if not suppliers_df.empty:
                countries = suppliers_df["country"].dropna().unique().tolist()
                supplier_list = suppliers_df[["city","country"]].dropna().to_dict("records")
                rss, gdelt, newsapi, weather, fstats = refresh_all_events(
                    news_api_key, weather_api_key, countries,
                    openai_api_key, suppliers=supplier_list
                )
                run_scoring_engine()
                st.session_state["last_refresh"] = datetime.utcnow()
                st.rerun()
        _time.sleep(60)
        st.rerun()

    # Re-score button
    if st.button("⚡ Recalculate Risk Scores", use_container_width=True):
        with st.spinner("Scoring suppliers..."):
            run_scoring_engine()
        st.success("Risk scores updated!")
        st.rerun()

    # Send Alerts button
    if st.button("🔔 Send Alerts (High Risk)", use_container_width=True):
        suppliers_df = get_all_suppliers()
        sent = dispatch_alerts(suppliers_df)
        if sent:
            st.success(f"Sent {sent} alert(s).")
        else:
            st.info("No alerts sent. Check SMTP/Slack config or no high-risk suppliers.")

    st.markdown("---")

    # Sample CSV download
    st.markdown('<p class="section-header">Tools</p>', unsafe_allow_html=True)
    st.download_button(
        label="📥 Download Sample CSV",
        data=get_sample_csv(),
        file_name="sample_suppliers.csv",
        mime="text/csv",
        use_container_width=True,
    )

# ─── Main Content ─────────────────────────────────────────────────────────────

st.markdown("# 🛡️ Supplier Risk Monitoring Dashboard")
st.markdown("*Real-time risk intelligence for global supply chains*")
st.markdown("---")

# ── 1. Upload Section ────────────────────────────────────────────────────────

with st.container():
    st.markdown('<p class="section-header">📁 Supplier Upload</p>', unsafe_allow_html=True)

    col_upload, col_info = st.columns([2, 1])

    with col_upload:
        uploaded_file = st.file_uploader(
            "Upload Supplier CSV",
            type=["csv"],
            help="Required columns: Supplier Name, Category, City, Country, Tier",
            label_visibility="collapsed"
        )

    with col_info:
        st.markdown("""
        **Required columns:**
        `Supplier Name` · `Category` · `City` · `Country` · `Tier`
        """)

    if uploaded_file:
        # Use file name + size as unique key to detect if this is a NEW file
        file_key = f"{uploaded_file.name}_{uploaded_file.size}"

        if st.session_state.get("last_uploaded_file") == file_key:
            # Same file already processed — show confirmation, don't re-run
            st.success("✅ Suppliers already uploaded and scored. Upload a new file to replace.")
        else:
            # New file detected — process it
            with st.spinner("Processing upload..."):
                df = process_upload(uploaded_file)

            if df is not None:
                st.info(f"Running geocoding for {len(df)} suppliers (may take ~{len(df)} seconds)...")
                progress = st.progress(0, text="Starting geocoding...")
                df = geocode_suppliers(df, progress_bar=progress)
                progress.empty()

                # Auto-run scoring after upload
                with st.spinner("Computing initial risk scores..."):
                    run_scoring_engine()

                # Mark this file as done so it won't re-process on next rerun
                st.session_state["last_uploaded_file"] = file_key
                st.success("✅ Suppliers uploaded, geocoded, and scored!")
                st.rerun()

st.markdown("---")

# ── 2. KPI Metrics ───────────────────────────────────────────────────────────

suppliers_df = get_all_suppliers()
events_df = get_all_events()

if not suppliers_df.empty:
    total = len(suppliers_df)
    high_risk = len(suppliers_df[suppliers_df["risk_level"] == "High"])
    medium_risk = len(suppliers_df[suppliers_df["risk_level"] == "Medium"])
    low_risk = len(suppliers_df[suppliers_df["risk_level"] == "Low"])
    total_events = len(events_df)

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total Suppliers", total)
    k2.metric("🔴 High Risk", high_risk, delta=None)
    k3.metric("🟡 Medium Risk", medium_risk)
    k4.metric("🟢 Low Risk", low_risk)
    k5.metric("Events Tracked", total_events)

    st.markdown("---")

    # ── 3. Map ───────────────────────────────────────────────────────────────

    st.markdown('<p class="section-header">🌍 Global Supplier Map</p>', unsafe_allow_html=True)
    fig = build_supplier_map(suppliers_df)
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # ── 4. Top 5 + Events ────────────────────────────────────────────────────

    col_top, col_events = st.columns([1, 1])

    with col_top:
        st.markdown('<p class="section-header">🚨 Top 5 Highest Risk Suppliers</p>', unsafe_allow_html=True)
        top5 = suppliers_df.head(5)
        for _, row in top5.iterrows():
            level = str(row.get("risk_level", "Low")).lower()
            score = row.get("risk_score", 0)
            name = row.get("supplier_name", "Unknown")
            country = row.get("country", "")
            card_class = level if level in ("high", "medium", "low") else "low"
            badge_class = f"risk-{level}"
            if st.button(f"{name}  —  {score:.0f}/100  ({country})", key=f"top5_{name}", use_container_width=True):
                st.session_state["drill_supplier"] = name

    with col_events:
        st.markdown('<p class="section-header">📰 Recent Supply Chain Events</p>', unsafe_allow_html=True)
        if events_df.empty:
            st.info("No events yet. Click 'Fetch Events Now' in the sidebar.")
        else:
            EVENT_ICONS = {"weather": "⛅", "news": "📰"}
            SEV_COLORS  = {"high": "#ef4444", "medium": "#f59e0b", "low": "#94a3b8"}

            for _, ev in events_df.head(8).iterrows():
                icon     = EVENT_ICONS.get(str(ev.get("event_type","")), "📰")
                country  = str(ev.get("detected_country", "Global"))
                title    = str(ev.get("title", ""))[:85]
                source   = str(ev.get("source", ""))[:25]
                pub      = str(ev.get("published_date", ""))[:10]
                severity = str(ev.get("severity", "medium")).lower()
                sev_col  = SEV_COLORS.get(severity, "#94a3b8")

                st.markdown(f"""
                <div style="background:#0f172a;border:1px solid #1e293b;border-left:3px solid {sev_col};
                            border-radius:6px;padding:8px 12px;margin-bottom:5px;">
                    <div style="color:#f1f5f9;font-size:0.85rem;font-weight:600">{icon} {title}</div>
                    <div style="color:#64748b;font-size:0.75rem;margin-top:2px">
                        🌍 {country} &nbsp;·&nbsp; 📡 {source} &nbsp;·&nbsp; 🗓 {pub}
                        &nbsp;·&nbsp; <span style="color:{sev_col}">● {severity.upper()}</span>
                    </div>
                </div>
                """, unsafe_allow_html=True)

    st.markdown("---")

    # ── 5. Full Supplier Table ────────────────────────────────────────────────

    st.markdown('<p class="section-header">📋 All Suppliers — click a row to see score breakdown</p>', unsafe_allow_html=True)

    # Filters
    fcol1, fcol2, fcol3 = st.columns(3)
    with fcol1:
        filter_risk = st.multiselect("Filter by Risk Level", options=["High", "Medium", "Low"], default=["High", "Medium", "Low"])
    with fcol2:
        categories = suppliers_df["category"].dropna().unique().tolist()
        filter_cat = st.multiselect("Filter by Category", options=categories, default=categories)
    with fcol3:
        tiers = suppliers_df["tier"].dropna().unique().tolist()
        filter_tier = st.multiselect("Filter by Tier", options=tiers, default=tiers)

    filtered = suppliers_df[
        suppliers_df["risk_level"].isin(filter_risk) &
        suppliers_df["category"].isin(filter_cat) &
        suppliers_df["tier"].isin(filter_tier)
    ].sort_values("risk_score", ascending=False)

    # Clickable supplier rows
    RISK_ICON = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}

    for _, row in filtered.iterrows():
        name     = str(row.get("supplier_name", ""))
        city     = str(row.get("city", ""))
        country  = str(row.get("country", ""))
        tier     = str(row.get("tier", ""))
        category = str(row.get("category", ""))
        score    = row.get("risk_score", 0)
        level    = str(row.get("risk_level", "Low"))
        icon     = RISK_ICON.get(level, "⚪")

        label = f"{icon}  {name}   |   {city}, {country}   |   {category} · Tier {tier}   |   **{score:.0f} / 100**"
        if st.button(label, key=f"row_{name}", use_container_width=True):
            # Toggle: clicking same supplier again closes the panel
            if st.session_state.get("drill_supplier") == name:
                st.session_state.pop("drill_supplier", None)
            else:
                st.session_state["drill_supplier"] = name

        # ── Drill-down panel — renders inline below the clicked row ──────────
        if st.session_state.get("drill_supplier") == name:
            with st.container():
                st.markdown(f"""
                <div style="background:#1e293b;border:1px solid #334155;border-radius:10px;
                            padding:20px;margin:4px 0 12px 0;">
                <h4 style="color:#f1f5f9;margin:0 0 4px 0">📊 Score Breakdown: {name}</h4>
                <p style="color:#64748b;margin:0;font-size:0.85rem">
                    {city}, {country} &nbsp;·&nbsp; {category} &nbsp;·&nbsp; Tier {tier}
                </p>
                </div>
                """, unsafe_allow_html=True)

                # Get full per-event breakdown
                breakdown = get_score_breakdown(
                    supplier_country = country,
                    supplier_lat     = row.get("latitude"),
                    supplier_lon     = row.get("longitude"),
                    supplier_city    = city,
                    events_df        = events_df
                )

                if not breakdown:
                    st.info("No events matched this supplier.")
                else:
                    # Score summary bar
                    sc1, sc2, sc3, sc4 = st.columns(4)
                    sc1.metric("Risk Score", f"{score:.0f} / 100")
                    sc2.metric("Risk Level", level)
                    counted = [e for e in breakdown if e["counted"]]
                    sc3.metric("Events Counted", f"{len(counted)} of {len(breakdown)}")
                    sc4.metric("Top Event", f"{counted[0]['points']:.1f} pts" if counted else "—")

                    # ── Predictions Panel ─────────────────────────────────────
                    st.markdown("---")
                    st.markdown("##### 🔮 Forward-Looking Risk Predictions")
                    st.caption(f"Based on city-level risk profile for {city}, {country} + active events")

                    pred_key = f"pred_{name}"
                    if st.button("📡 Generate Predictions", key=f"genp_{name}"):
                        st.session_state[pred_key] = get_predictions(
                            supplier_name = name,
                            city          = city,
                            country       = country,
                            category      = category,
                            tier          = tier,
                            risk_score    = score,
                            risk_level    = level,
                            lat           = row.get("latitude"),
                            lon           = row.get("longitude"),
                            breakdown     = breakdown,
                            openai_api_key= openai_api_key,
                        )

                    pred = st.session_state.get(pred_key)
                    if pred:
                        conf_badge = "🤖 AI-Enhanced" if pred.confidence == "ai-enhanced" else "📋 Rule-Based"

                        # City risk profile + active threat
                        st.markdown(f"""
                        <div style="background:#1e293b;border:1px solid #334155;border-radius:8px;padding:14px;margin-bottom:10px;">
                            <div style="color:#94a3b8;font-size:0.72rem;letter-spacing:0.1em;margin-bottom:6px">
                                📍 CITY RISK PROFILE — {city.upper()}, {country.upper()} &nbsp;·&nbsp; {conf_badge}
                            </div>
                            <p style="color:#f1f5f9;margin:0 0 6px 0;font-size:0.87rem">{pred.city_risk_profile}</p>
                            <p style="color:#f59e0b;margin:0;font-size:0.83rem">⚡ {pred.active_threat}</p>
                            <p style="color:#64748b;margin:4px 0 0 0;font-size:0.78rem">🗓 {pred.seasonal_context}</p>
                        </div>
                        """, unsafe_allow_html=True)

                        # Time horizons
                        TRAJ_COLORS = {
                            "ESCALATING": "#ef4444", "STABLE": "#22c55e",
                            "IMPROVING":  "#3b82f6", "UNCERTAIN": "#f59e0b",
                            "ELEVATED":   "#f59e0b",
                        }
                        h_cols = st.columns(3)
                        for i, horizon in enumerate(pred.horizons):
                            traj_color = TRAJ_COLORS.get(horizon.risk_trajectory, "#64748b")
                            with h_cols[i]:
                                st.markdown(f"""
                                <div style="background:#0f172a;border:1px solid #1e293b;
                                            border-top:3px solid {traj_color};
                                            border-radius:6px;padding:12px;height:100%">
                                    <div style="display:flex;justify-content:space-between;margin-bottom:6px">
                                        <span style="color:#94a3b8;font-size:0.72rem;font-weight:600">
                                            {horizon.icon} {horizon.timeframe.upper()}
                                        </span>
                                        <span style="color:{traj_color};font-size:0.72rem;font-weight:700">
                                            {horizon.risk_trajectory}
                                        </span>
                                    </div>
                                    <div style="font-size:1.4rem;font-weight:700;color:{traj_color};margin-bottom:2px">
                                        {horizon.probability_of_disruption}%
                                    </div>
                                    <div style="color:#64748b;font-size:0.72rem;margin-bottom:8px">
                                        disruption probability · {horizon.expected_impact} impact
                                    </div>
                                    <p style="color:#94a3b8;font-size:0.8rem;margin:0;line-height:1.5">
                                        {horizon.narrative}
                                    </p>
                                </div>
                                """, unsafe_allow_html=True)

                        # Triggers to watch
                        if pred.horizons:
                            st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
                            t1, t2 = st.columns(2)
                            with t1:
                                st.markdown("""
                                <div style="color:#f59e0b;font-size:0.75rem;font-weight:600;margin-bottom:4px">
                                    👁 TRIGGERS TO WATCH (72h)
                                </div>""", unsafe_allow_html=True)
                                for trigger in pred.horizons[0].triggers_to_watch:
                                    st.markdown(f"<div style='color:#94a3b8;font-size:0.8rem'>• {trigger}</div>",
                                                unsafe_allow_html=True)
                            with t2:
                                if pred.cascade_risks:
                                    st.markdown("""
                                    <div style="color:#8b5cf6;font-size:0.75rem;font-weight:600;margin-bottom:4px">
                                        🔗 CASCADE RISKS
                                    </div>""", unsafe_allow_html=True)
                                    for cr in pred.cascade_risks[:3]:
                                        st.markdown(f"<div style='color:#94a3b8;font-size:0.8rem'>• {cr}</div>",
                                                    unsafe_allow_html=True)

                    st.markdown("---")
                    st.markdown("##### 🔍 Events contributing to this score")
                    st.caption("Only the top 5 highest-scoring events count toward the final score. Others are shown for context.")

                    for ev in breakdown[:15]:  # Show top 15 for context
                        counted_badge  = "✅ COUNTED" if ev["counted"] else "⬜ not counted"
                        signal_colors  = {"high": "#ef4444", "medium": "#f59e0b", "low": "#94a3b8"}
                        signal_color   = signal_colors.get(ev["signal"], "#94a3b8")

                        # Build multiplier explanation
                        dist_pct  = int(ev["dist_mult"] * 100)
                        sev_pct   = int(ev["sev_mult"] * 100)
                        time_pct  = int(ev["time_mult"] * 100)

                        is_fwd      = ev.get("is_forecast", False)
                        is_seasonal = ev.get("_is_seasonal", False)
                        if is_seasonal:
                            fwd_badge    = '<span style="background:#0f766e;color:white;font-size:0.7rem;padding:1px 6px;border-radius:4px;margin-left:6px">📅 SEASONAL</span>'
                            time_label   = "Seasonal window — forward signal"
                            time_icon    = "📅"
                            border_color = "#0f766e"
                        elif is_fwd:
                            fwd_badge    = '<span style="background:#7c3aed;color:white;font-size:0.7rem;padding:1px 6px;border-radius:4px;margin-left:6px">🔮 FORECAST</span>'
                            time_label   = "Future signal — act now"
                            time_icon    = "🔮"
                            border_color = "#7c3aed"
                        else:
                            fwd_badge    = ""
                            time_label   = f"{time_pct}% recency weight"
                            time_icon    = "🕐"
                            border_color = signal_color

                        st.markdown(f"""
                        <div style="background:#0f172a;border:1px solid #1e293b;border-left:3px solid {border_color};
                                    border-radius:6px;padding:10px 14px;margin-bottom:6px;">
                            <div style="display:flex;justify-content:space-between;align-items:center;">
                                <span style="color:#f1f5f9;font-weight:600;font-size:0.9rem">
                                    {ev['title'][:90]}{fwd_badge}
                                </span>
                                <span style="color:#f1f5f9;font-weight:700;font-size:1rem;min-width:60px;text-align:right">
                                    +{ev['points']:.2f} pts
                                </span>
                            </div>
                            <div style="color:#64748b;font-size:0.78rem;margin-top:4px">
                                📍 <b style="color:#94a3b8">{ev['proximity']}</b>
                                &nbsp;·&nbsp; 🗓 {ev['published']}
                                &nbsp;·&nbsp; 📡 {ev['source'][:30]}
                                &nbsp;·&nbsp; {counted_badge}
                            </div>
                            <div style="color:#64748b;font-size:0.75rem;margin-top:4px">
                                <span style="color:{signal_color}">● {ev['signal'].upper()} signal</span>
                                &nbsp;·&nbsp;
                                📏 Distance: <b>{dist_pct}%</b> weight
                                &nbsp;·&nbsp;
                                ⚡ Severity: <b>{sev_pct}%</b> weight
                                &nbsp;·&nbsp;
                                {time_icon} <b>{time_label}</b>
                                &nbsp;·&nbsp;
                                Formula: 25 × {ev['dist_mult']} × {ev['sev_mult']} × {ev['time_mult']} = <b>{ev['points']:.2f}</b>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)

                    # ── Recommendations Panel ─────────────────────────────
                    st.markdown("---")
                    st.markdown("##### 💡 What Should You Do?")

                    rec_key = f"rec_{name}"
                    if st.button("🤖 Generate Action Plan", key=f"gen_{name}", use_container_width=False):
                        st.session_state[rec_key] = get_recommendations(
                            supplier_name    = name,
                            supplier_city    = city,
                            supplier_country = country,
                            supplier_tier    = tier,
                            supplier_category= category,
                            risk_score       = score,
                            risk_level       = level,
                            breakdown        = breakdown,
                            events_summary   = str(row.get("event_summary", "")),
                            openai_api_key   = openai_api_key
                        )

                    rec = st.session_state.get(rec_key)
                    if rec:
                        confidence_badge = "🤖 AI-Enhanced" if rec.confidence == "ai-enhanced" else "📋 Rule-Based"
                        URGENCY_COLORS = {
                            "CRITICAL": "#ef4444", "HIGH": "#f59e0b",
                            "MEDIUM": "#3b82f6",   "LOW": "#22c55e", "WATCH": "#64748b"
                        }
                        level_upper = level.upper()
                        urg_color = URGENCY_COLORS.get(level_upper, "#64748b")

                        # Situation summary card
                        st.markdown(f"""
                        <div style="background:#1e293b;border:1px solid #334155;border-left:4px solid {urg_color};
                                    border-radius:8px;padding:16px;margin-bottom:12px;">
                            <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
                                <span style="color:#94a3b8;font-size:0.75rem;letter-spacing:0.1em">SITUATION ASSESSMENT</span>
                                <span style="color:#64748b;font-size:0.75rem">{confidence_badge}</span>
                            </div>
                            <p style="color:#f1f5f9;margin:0;line-height:1.6">{rec.situation_summary}</p>
                        </div>
                        """, unsafe_allow_html=True)

                        # Action cards
                        st.markdown("**Recommended Actions** — in priority order:")
                        CAT_COLORS = {
                            "inventory":   "#3b82f6",
                            "redirect":    "#8b5cf6",
                            "dual_source": "#8b5cf6",
                            "monitor":     "#64748b",
                            "escalate":    "#ef4444",
                        }
                        TIMEFRAME_URGENCY = {
                            "Immediate": "#ef4444",
                            "Within 48 hours": "#f59e0b",
                            "This week": "#3b82f6",
                            "This month": "#22c55e",
                            "Ongoing": "#64748b",
                        }
                        for action in rec.actions:
                            cat_color  = CAT_COLORS.get(action.category, "#64748b")
                            tf_color   = TIMEFRAME_URGENCY.get(action.timeframe, "#64748b")
                            st.markdown(f"""
                            <div style="background:#0f172a;border:1px solid #1e293b;border-left:4px solid {cat_color};
                                        border-radius:6px;padding:12px 16px;margin-bottom:8px;">
                                <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                                    <span style="color:#f1f5f9;font-weight:700;font-size:0.95rem">
                                        {action.icon} {action.action}
                                    </span>
                                    <span style="color:{tf_color};font-size:0.75rem;font-weight:600;
                                                 white-space:nowrap;margin-left:12px;">
                                        ⏱ {action.timeframe}
                                    </span>
                                </div>
                                <p style="color:#94a3b8;margin:6px 0 0 0;font-size:0.85rem;line-height:1.5">
                                    {action.detail}
                                </p>
                            </div>
                            """, unsafe_allow_html=True)

                        # Stock up + Alternative note side by side
                        sn1, sn2 = st.columns(2)
                        with sn1:
                            st.markdown(f"""
                            <div style="background:#0f172a;border:1px solid #1e293b;border-radius:6px;padding:12px;">
                                <div style="color:#f59e0b;font-size:0.75rem;font-weight:600;margin-bottom:4px">
                                    ⏱ LEAD TIME WARNING
                                </div>
                                <p style="color:#94a3b8;margin:0;font-size:0.83rem">{rec.lead_time_warning}</p>
                            </div>
                            """, unsafe_allow_html=True)
                        with sn2:
                            st.markdown(f"""
                            <div style="background:#0f172a;border:1px solid #1e293b;border-radius:6px;padding:12px;">
                                <div style="color:#8b5cf6;font-size:0.75rem;font-weight:600;margin-bottom:4px">
                                    🔄 ALTERNATIVE SUPPLIERS
                                </div>
                                <p style="color:#94a3b8;margin:0;font-size:0.83rem">{rec.alternative_note}</p>
                            </div>
                            """, unsafe_allow_html=True)

                        # Don't do this
                        if rec.do_not_do:
                            st.markdown("""
                            <div style="background:#1a0a0a;border:1px solid #3f1f1f;border-radius:6px;
                                        padding:12px 16px;margin-top:10px;">
                                <div style="color:#ef4444;font-size:0.75rem;font-weight:600;margin-bottom:6px">
                                    ⛔ COMMON MISTAKES TO AVOID
                                </div>
                            """, unsafe_allow_html=True)
                            for dont in rec.do_not_do:
                                st.markdown(f"""
                                <div style="color:#fca5a5;font-size:0.83rem;margin-bottom:3px">
                                    • {dont}
                                </div>
                                """, unsafe_allow_html=True)
                            st.markdown("</div>", unsafe_allow_html=True)

                    # ── Alternative Suppliers Panel ───────────────────────
                    st.markdown("---")
                    st.markdown("##### 🔄 Alternative Suppliers")

                    alt_key = f"alt_{name}"
                    if st.button("🔍 Find Alternatives", key=f"gena_{name}"):
                        # Scan top events for countdown language
                        countdown = None
                        for ev in breakdown[:5]:
                            countdown = detect_countdown(
                                ev.get("title",""), ev.get("title",""),
                                ev.get("published","")
                            )
                            if countdown:
                                break

                        # Build supplier list for searching
                        all_sup_list = suppliers_df.to_dict("records")
                        # Normalise column names
                        norm = []
                        for s in all_sup_list:
                            norm.append({
                                "supplier_name": s.get("supplier_name") or s.get("Supplier Name",""),
                                "category":      s.get("category") or s.get("Category",""),
                                "country":       s.get("country") or s.get("Country",""),
                                "city":          s.get("city") or s.get("City",""),
                                "tier":          s.get("tier") or s.get("Tier",""),
                                "risk_score":    s.get("risk_score", 0),
                                "risk_level":    s.get("risk_level", "Low"),
                                "latitude":      s.get("latitude"),
                                "longitude":     s.get("longitude"),
                            })

                        at_risk = {
                            "supplier_name": name, "category": category,
                            "country": country, "city": city,
                            "latitude": row.get("latitude"), "longitude": row.get("longitude"),
                        }
                        st.session_state[alt_key] = find_alternatives(
                            at_risk, norm,
                            risk_reason=f"Risk score {score:.0f}/100 ({level})",
                            countdown_event=countdown,
                        )

                    alt = st.session_state.get(alt_key)
                    if alt:
                        URGENCY_COLORS = {"immediate": "#ef4444", "this_week": "#f59e0b", "plan_ahead": "#3b82f6"}
                        urg_color = URGENCY_COLORS.get(alt["urgency"], "#64748b")
                        urg_label = {"immediate":"🚨 IMMEDIATE ACTION", "this_week":"⚠️ ACT THIS WEEK", "plan_ahead":"📋 PLAN AHEAD"}.get(alt["urgency"],"")

                        # Countdown banner if detected
                        if alt.get("countdown"):
                            cd = alt["countdown"]
                            days_left = cd.days_remaining
                            bar_color = "#ef4444" if days_left <= 3 else "#f59e0b" if days_left <= 10 else "#3b82f6"
                            bar_pct   = max(0, min(100, int((1 - days_left/30)*100))) if days_left >= 0 else 100
                            st.markdown(f"""
                            <div style="background:#1a0a0a;border:1px solid #7f1d1d;border-left:4px solid {bar_color};
                                        border-radius:8px;padding:14px;margin-bottom:12px;">
                                <div style="display:flex;justify-content:space-between;margin-bottom:6px;">
                                    <span style="color:{bar_color};font-size:0.75rem;font-weight:700;letter-spacing:0.08em">
                                        ⏳ COUNTDOWN EVENT DETECTED
                                    </span>
                                    <span style="color:{bar_color};font-weight:700">
                                        {"TODAY" if days_left == 0 else f"DEADLINE PASSED" if days_left < 0 else f"{days_left} DAYS REMAINING"}
                                    </span>
                                </div>
                                <p style="color:#fca5a5;margin:0 0 6px 0;font-size:0.87rem;font-weight:600">
                                    {cd.headline[:120]}
                                </p>
                                <div style="color:#94a3b8;font-size:0.78rem;margin-bottom:8px">
                                    📅 Published: {cd.published_date.strftime("%b %d")}
                                    &nbsp;·&nbsp; ⏰ Deadline: {cd.deadline_date.strftime("%b %d, %Y")}
                                    &nbsp;·&nbsp; 🎯 Confidence: {cd.confidence}%
                                    &nbsp;·&nbsp; Type: {cd.risk_type.replace("_"," ").title()}
                                </div>
                                <div style="background:#1f0a0a;border-radius:4px;padding:8px;font-size:0.82rem;color:#fca5a5">
                                    💥 {cd.supply_chain_impact[:300]}
                                </div>
                                <div style="background:#374151;border-radius:4px;height:6px;margin-top:8px">
                                    <div style="background:{bar_color};height:6px;border-radius:4px;width:{bar_pct}%"></div>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)

                        # Recommendation banner
                        st.markdown(f"""
                        <div style="background:#1e293b;border:1px solid #334155;border-left:4px solid {urg_color};
                                    border-radius:8px;padding:12px 16px;margin-bottom:12px;">
                            <div style="color:{urg_color};font-size:0.75rem;font-weight:700;margin-bottom:4px">
                                {urg_label}
                            </div>
                            <p style="color:#f1f5f9;margin:0;font-size:0.87rem">{alt["recommendation"]}</p>
                        </div>
                        """, unsafe_allow_html=True)

                        # Internal alternatives (from your own list)
                        if alt["internal_alternatives"]:
                            st.markdown("**✅ Already in your supplier list — lower risk:**")
                            for a in alt["internal_alternatives"]:
                                tier_labels = {1:"🟢 Very Stable", 2:"🟡 Mostly Stable", 3:"🟠 Elevated", 4:"🔴 High Risk"}
                                tier_label  = tier_labels.get(a.get("risk_tier",3), "⚪ Unknown")
                                dist_note   = f" · {a['distance_km']:,} km away" if a.get("distance_km") else ""
                                st.markdown(f"""
                                <div style="background:#0f172a;border:1px solid #1e293b;border-left:4px solid #22c55e;
                                            border-radius:6px;padding:10px 14px;margin-bottom:6px;">
                                    <div style="color:#22c55e;font-weight:700">✅ {a["supplier_name"]}</div>
                                    <div style="color:#94a3b8;font-size:0.82rem;margin-top:3px">
                                        📍 {a["city"]}, {a["country"]}{dist_note}
                                        &nbsp;·&nbsp; {a["category"]} · Tier {a["tier"]}
                                        &nbsp;·&nbsp; Risk: {a["risk_score"]:.0f}/100 ({a["risk_level"]})
                                        &nbsp;·&nbsp; {tier_label}
                                    </div>
                                </div>
                                """, unsafe_allow_html=True)
                        else:
                            st.caption("No lower-risk supplier found in your current list for this category.")

                        # Regional suggestions
                        if alt["regional_suggestions"]:
                            st.markdown("**🌍 Recommended alternative sourcing regions:**")
                            tier_colors = {1:"#22c55e", 2:"#84cc16", 3:"#f59e0b", 4:"#ef4444"}
                            for sug in alt["regional_suggestions"]:
                                tc = tier_colors.get(sug["risk_tier"], "#64748b")
                                st.markdown(f"""
                                <div style="background:#0f172a;border:1px solid #1e293b;border-left:4px solid {tc};
                                            border-radius:6px;padding:10px 14px;margin-bottom:6px;">
                                    <div style="display:flex;justify-content:space-between;">
                                        <span style="color:#f1f5f9;font-weight:600">🌍 {sug["country"]}</span>
                                        <span style="color:{tc};font-size:0.75rem;font-weight:600">
                                            {sug["tier_label"]}
                                        </span>
                                    </div>
                                    <div style="color:#94a3b8;font-size:0.82rem;margin-top:4px">
                                        {sug["reason"]}
                                    </div>
                                </div>
                                """, unsafe_allow_html=True)

                    if st.button("✕ Close", key=f"close_{name}"):
                        st.session_state.pop("drill_supplier", None)
                        st.session_state.pop(rec_key, None)
                        st.session_state.pop(alt_key, None)
                        st.rerun()

                st.markdown("---")

else:
    # Empty state
    st.markdown("""
    <div style="text-align:center; padding: 80px 20px; color: #64748b;">
        <h2>👋 Welcome to Supplier Risk Dashboard</h2>
        <p>Upload a CSV file above to get started.<br>
        Download the sample CSV from the sidebar to see the expected format.</p>
    </div>
    """, unsafe_allow_html=True)

# ─── Footer ──────────────────────────────────────────────────────────────────

st.markdown("---")
st.markdown(
    '<small style="color:#334155">Supplier Risk Dashboard · Powered by OpenStreetMap, NewsAPI, OpenWeatherMap</small>',
    unsafe_allow_html=True
)
