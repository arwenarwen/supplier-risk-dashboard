"""
mapping.py - Interactive supplier map using Plotly.
High-risk suppliers are highlighted in red; others in blue/yellow.
"""

import pandas as pd
import plotly.graph_objects as go


RISK_COLORS = {
    "High": "#ef4444",    # Red
    "Medium": "#f59e0b",  # Amber
    "Low": "#22c55e",     # Green
}

RISK_SIZES = {
    "High": 18,
    "Medium": 12,
    "Low": 9,
}


def build_supplier_map(df: pd.DataFrame) -> go.Figure:
    """
    Build a Plotly scatter_geo map of all suppliers.
    - Color coded by risk level (green/amber/red)
    - Hover shows: Name, Category, Tier, Country, Risk Score
    """
    # Filter rows that have been geocoded
    mapped = df.dropna(subset=["latitude", "longitude"]).copy()

    if mapped.empty:
        fig = go.Figure()
        fig.update_layout(
            title="No geocoded suppliers to display. Run geocoding first.",
            template="plotly_dark"
        )
        return fig

    # Normalize column names (database returns lowercase)
    col_map = {
        "supplier_name": "Supplier Name",
        "category": "Category",
        "city": "City",
        "country": "Country",
        "tier": "Tier",
        "risk_score": "Risk Score",
        "risk_level": "Risk Level",
        "event_summary": "Event Summary",
    }
    mapped = mapped.rename(columns={k: v for k, v in col_map.items() if k in mapped.columns})

    # Ensure required columns exist
    for col in ["Risk Level", "Risk Score", "Category", "Tier", "Supplier Name"]:
        if col not in mapped.columns:
            mapped[col] = "N/A"

    fig = go.Figure()

    for risk_level in ["High", "Medium", "Low"]:
        subset = mapped[mapped["Risk Level"] == risk_level]
        if subset.empty:
            continue

        fig.add_trace(go.Scattergeo(
            lat=subset["latitude"],
            lon=subset["longitude"],
            mode="markers",
            marker=dict(
                size=RISK_SIZES[risk_level],
                color=RISK_COLORS[risk_level],
                opacity=0.85,
                line=dict(width=1, color="white"),
                symbol="circle",
            ),
            name=f"{risk_level} Risk",
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Category: %{customdata[1]}<br>"
                "Tier: %{customdata[2]}<br>"
                "Location: %{customdata[3]}<br>"
                "Risk Score: <b>%{customdata[4]}</b><br>"
                "Level: %{customdata[5]}<br>"
                "<i>%{customdata[6]}</i>"
                "<extra></extra>"
            ),
            customdata=subset[[
                "Supplier Name", "Category", "Tier", "Country",
                "Risk Score", "Risk Level", "Event Summary"
            ]].values,
        ))

    fig.update_layout(
        title=dict(
            text="🌍 Global Supplier Risk Map",
            font=dict(size=18, color="#f1f5f9"),
            x=0.01
        ),
        geo=dict(
            showframe=False,
            showcoastlines=True,
            coastlinecolor="#334155",
            showland=True,
            landcolor="#1e293b",
            showocean=True,
            oceancolor="#0f172a",
            showcountries=True,
            countrycolor="#334155",
            projection_type="natural earth",
            bgcolor="#0f172a",
        ),
        paper_bgcolor="#0f172a",
        plot_bgcolor="#0f172a",
        legend=dict(
            bgcolor="#1e293b",
            bordercolor="#334155",
            borderwidth=1,
            font=dict(color="#f1f5f9"),
        ),
        margin=dict(l=0, r=0, t=40, b=0),
        height=520,
    )

    return fig
