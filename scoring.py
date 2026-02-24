"""
scoring.py - Rule-based risk scoring engine for suppliers.
Scores are based on event proximity (country/region match).
Optional: AI-enhanced scoring via OpenAI GPT.
"""

import os
import pandas as pd
import pycountry
import pycountry_convert as pc
from database import get_all_events, update_supplier_risk, get_all_suppliers

# Risk score weights
SCORE_SAME_COUNTRY = 70
SCORE_SAME_REGION = 40
SCORE_ELSEWHERE = 10
HIGH_RISK_THRESHOLD = 50


def get_continent(country_name: str) -> str | None:
    """Map a country name to its continent code."""
    try:
        country = pycountry.countries.lookup(country_name)
        alpha2 = country.alpha_2
        continent_code = pc.country_alpha2_to_continent_code(alpha2)
        return continent_code
    except Exception:
        return None


def score_supplier(supplier_country: str, events_df: pd.DataFrame) -> tuple[float, str]:
    """
    Calculate risk score for a supplier based on events DataFrame.
    Returns (risk_score, event_summary_string).
    """
    if events_df.empty:
        return 0.0, "No events detected."

    total_score = 0.0
    matched_events = []
    supplier_continent = get_continent(supplier_country)

    for _, event in events_df.iterrows():
        event_country = str(event.get("detected_country", "Unknown"))
        title = str(event.get("title", ""))

        if event_country == "Unknown" or not event_country:
            total_score += SCORE_ELSEWHERE
            continue

        if event_country.strip().lower() == supplier_country.strip().lower():
            # Same country = highest risk
            total_score += SCORE_SAME_COUNTRY
            matched_events.append(f"[SAME COUNTRY] {title[:80]}")
        else:
            event_continent = get_continent(event_country)
            if event_continent and event_continent == supplier_continent:
                # Same region/continent
                total_score += SCORE_SAME_REGION
                matched_events.append(f"[SAME REGION] {title[:80]}")
            else:
                # Elsewhere
                total_score += SCORE_ELSEWHERE

    # Cap total score at 100
    final_score = min(round(total_score, 1), 100.0)

    # Build summary (top 3 most relevant events)
    summary = "; ".join(matched_events[:3]) if matched_events else "No direct country/region events."
    return final_score, summary


def classify_risk_level(score: float) -> str:
    """Return human-readable risk level based on score."""
    if score > HIGH_RISK_THRESHOLD:
        return "High"
    elif score > 25:
        return "Medium"
    return "Low"


def run_scoring_engine() -> pd.DataFrame:
    """
    Score all suppliers against all events.
    Updates the database and returns the updated suppliers DataFrame.
    """
    suppliers_df = get_all_suppliers()
    events_df = get_all_events()

    if suppliers_df.empty:
        return suppliers_df

    for _, row in suppliers_df.iterrows():
        supplier_name = row["supplier_name"]
        supplier_country = str(row.get("country", ""))

        score, summary = score_supplier(supplier_country, events_df)
        level = classify_risk_level(score)

        update_supplier_risk(supplier_name, score, level, summary)

    return get_all_suppliers()


# ─── Optional: AI-Enhanced Scoring via OpenAI ────────────────────────────────

def ai_parse_event(event_text: str, openai_api_key: str) -> dict:
    """
    Use OpenAI GPT to classify whether a news event is likely to disrupt supply.
    Returns dict with: disruption_likely, country, severity.
    Falls back gracefully if API unavailable.

    Placeholder for future ML/LLM integration.
    """
    if not openai_api_key:
        return {"disruption_likely": "Unknown", "country": "Unknown", "severity": "medium"}

    try:
        import openai
        client = openai.OpenAI(api_key=openai_api_key)

        prompt = f"""Analyze this news article excerpt and respond in JSON format only.
Article: "{event_text}"

Respond with:
{{
  "disruption_likely": "Yes" or "No",
  "country": "country name or Unknown",
  "severity": "low", "medium", or "high"
}}"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",  # Cost-efficient model
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0
        )

        import json
        content = response.choices[0].message.content.strip()
        return json.loads(content)

    except Exception as e:
        return {"disruption_likely": "Unknown", "country": "Unknown", "severity": "medium"}
