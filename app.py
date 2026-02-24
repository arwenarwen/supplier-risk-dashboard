"""
app.py - Main Streamlit dashboard for Supplier Risk Monitoring
Run with: streamlit run app.py
"""

import os
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# Load environment variables from .env file (local dev)
load_dotenv()

# Internal modules
from database import init_db, get_all_suppliers, get_all_events
from upload import process_upload, get_sample_csv
from geocoding import geocode_suppliers
from events import refresh_all_events, should_auto_refresh
from scoring import run_scoring_engine
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
            with st.spinner(f"Scanning 60+ sources across {len(countries)} countries..."):
                rss, gdelt, newsapi, weather = refresh_all_events(news_api_key, weather_api_key, countries)
            st.success(f"📡 {rss} RSS · {gdelt} GDELT · {newsapi} NewsAPI · {weather} ⛅")
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
                rss, gdelt, newsapi, weather = refresh_all_events(news_api_key, weather_api_key, countries)
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

    # ── 4. Top 5 High Risk Suppliers ─────────────────────────────────────────

    col_top, col_events = st.columns([1, 1])

    with col_top:
        st.markdown('<p class="section-header">🚨 Top 5 Highest Risk Suppliers</p>', unsafe_allow_html=True)
        top5 = suppliers_df.head(5)

        for _, row in top5.iterrows():
            level = str(row.get("risk_level", "Low")).lower()
            score = row.get("risk_score", 0)
            name = row.get("supplier_name", "Unknown")
            country = row.get("country", "")
            summary = str(row.get("event_summary", ""))[:100]

            badge_class = f"risk-{level}"
            card_class = level if level in ("high", "medium", "low") else "low"

            st.markdown(f"""
            <div class="supplier-card {card_class}">
                <strong>{name}</strong>
                <span class="{badge_class}" style="float:right">{score:.0f}/100</span><br>
                <small style="color:#94a3b8">{country} · {row.get('tier','')}</small><br>
                <small style="color:#64748b">{summary}</small>
            </div>
            """, unsafe_allow_html=True)

    with col_events:
        st.markdown('<p class="section-header">📰 Recent Events</p>', unsafe_allow_html=True)

        if events_df.empty:
            st.info("No events yet. Click 'Fetch Latest Events' in the sidebar.")
        else:
            display_events = events_df[["title", "detected_country", "event_type", "published_date"]].head(10)
            display_events.columns = ["Title", "Country", "Type", "Published"]
            st.dataframe(display_events, use_container_width=True, hide_index=True)

    st.markdown("---")

    # ── 5. Full Supplier Table ────────────────────────────────────────────────

    st.markdown('<p class="section-header">📋 All Suppliers</p>', unsafe_allow_html=True)

    # Filters
    fcol1, fcol2, fcol3 = st.columns(3)

    with fcol1:
        filter_risk = st.multiselect(
            "Filter by Risk Level",
            options=["High", "Medium", "Low"],
            default=["High", "Medium", "Low"]
        )
    with fcol2:
        categories = suppliers_df["category"].dropna().unique().tolist()
        filter_cat = st.multiselect("Filter by Category", options=categories, default=categories)
    with fcol3:
        tiers = suppliers_df["tier"].dropna().unique().tolist()
        filter_tier = st.multiselect("Filter by Tier", options=tiers, default=tiers)

    # Apply filters
    filtered = suppliers_df[
        suppliers_df["risk_level"].isin(filter_risk) &
        suppliers_df["category"].isin(filter_cat) &
        suppliers_df["tier"].isin(filter_tier)
    ]

    # Display columns
    display_cols = ["supplier_name", "category", "city", "country", "tier", "risk_score", "risk_level", "event_summary"]
    available = [c for c in display_cols if c in filtered.columns]
    display_df = filtered[available].copy()
    display_df.columns = ["Supplier", "Category", "City", "Country", "Tier",
                           "Risk Score", "Risk Level", "Event Summary"][:len(available)]

    # Color the Risk Score column
    st.dataframe(
        display_df.sort_values("Risk Score", ascending=False),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Risk Score": st.column_config.ProgressColumn(
                "Risk Score",
                min_value=0,
                max_value=100,
                format="%.0f",
            ),
            "Risk Level": st.column_config.TextColumn("Risk Level"),
        }
    )

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
