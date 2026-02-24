"""
predictions.py - City-aware forward-looking supply chain risk predictions.

Reads the supplier's exact city, country, lat/lon and active events,
then generates predictions across 3 time horizons:
  - 72 hours  (immediate)
  - 7 days    (short-term)
  - 30 days   (medium-term)

Works in two modes:
  1. Rule-based: uses city geography, industry vulnerability, seasonal patterns
  2. GPT-enhanced: full LLM reasoning with city-specific knowledge

The key insight: a supplier in Kaohsiung (typhoon belt, semiconductor hub) has
completely different risk patterns than a supplier in Munich (landlocked, stable).
The city IS the prediction context.
"""

import json
from dataclasses import dataclass, field
from typing import Optional

# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class PredictionHorizon:
    timeframe: str               # "72 hours", "7 days", "30 days"
    risk_trajectory: str         # "ESCALATING" | "STABLE" | "IMPROVING" | "UNCERTAIN"
    probability_of_disruption: int  # 0-100
    expected_impact: str         # "None" | "Minor" | "Moderate" | "Severe"
    narrative: str               # Plain English: what will likely happen
    triggers_to_watch: list[str] # Specific signals that would change this prediction
    icon: str


@dataclass
class PredictionReport:
    supplier_name: str
    city: str
    country: str
    category: str
    lat: Optional[float]
    lon: Optional[float]

    city_risk_profile: str       # Plain English description of this city's inherent risks
    active_threat: str           # What is happening RIGHT NOW near this city
    seasonal_context: str        # Is this a high-risk season for this region?

    horizons: list[PredictionHorizon]  # [72h, 7d, 30d]

    cascade_risks: list[str]     # Secondary effects: if this disrupts, what else breaks?
    confidence: str              # "rule-based" | "ai-enhanced"
    data_sources_used: list[str] # Which events fed this prediction


# ─── City Risk Profiles ───────────────────────────────────────────────────────
# Built-in knowledge about each city's geographic and geopolitical risk factors.
# This is what makes predictions city-specific rather than country-generic.

CITY_RISK_PROFILES = {
    # ── East Asia ──────────────────────────────────────────────────────────────
    "shanghai": {
        "natural_risks":   ["typhoon (Jun–Nov)", "flooding (summer)", "fog-related port delays"],
        "geopolitical":    ["US-China trade tensions", "Taiwan Strait proximity"],
        "infrastructure":  ["world's busiest container port", "heavy fog causes closures"],
        "industry_notes":  "Global hub for electronics, automotive, machinery exports",
        "peak_risk_months": [6, 7, 8, 9, 10],  # typhoon + flood season
    },
    "shenzhen": {
        "natural_risks":   ["typhoon (Jun–Nov)", "flooding"],
        "geopolitical":    ["US-China tech sanctions", "proximity to Hong Kong instability"],
        "infrastructure":  ["Yantian port — frequent congestion", "electronics manufacturing hub"],
        "industry_notes":  "World center for electronics, PCBs, consumer goods",
        "peak_risk_months": [6, 7, 8, 9, 10],
    },
    "guangzhou": {
        "natural_risks":   ["typhoon (Jun–Nov)", "flooding"],
        "geopolitical":    ["US-China trade tensions"],
        "infrastructure":  ["Nansha port", "major automotive + textile hub"],
        "industry_notes":  "Automotive, textiles, electronics, trade fair city",
        "peak_risk_months": [6, 7, 8, 9],
    },
    "kaohsiung": {
        "natural_risks":   ["typhoon (Jul–Oct)", "earthquake (high seismic zone)"],
        "geopolitical":    ["Taiwan Strait tensions — highest geopolitical risk in Asia"],
        "infrastructure":  ["Major semiconductor export hub", "TSMC proximity"],
        "industry_notes":  "Critical global semiconductor and electronics supply chain node",
        "peak_risk_months": [7, 8, 9, 10],
    },
    "taipei": {
        "natural_risks":   ["typhoon (Jul–Oct)", "earthquake (very high seismic zone)"],
        "geopolitical":    ["Taiwan Strait tensions — existential geopolitical risk"],
        "infrastructure":  ["TSMC HQ", "major tech R&D center"],
        "industry_notes":  "Global semiconductor design and fab capital",
        "peak_risk_months": [7, 8, 9, 10],
    },
    "tokyo": {
        "natural_risks":   ["earthquake (very high)", "tsunami risk", "typhoon (Aug–Oct)"],
        "geopolitical":    ["Low — stable democracy", "North Korea proximity"],
        "infrastructure":  ["Highly resilient infrastructure", "multiple redundant ports"],
        "industry_notes":  "Automotive, electronics, precision manufacturing",
        "peak_risk_months": [8, 9, 10],
    },
    "osaka": {
        "natural_risks":   ["earthquake", "typhoon (Aug–Oct)"],
        "geopolitical":    ["Low"],
        "infrastructure":  ["Kobe port nearby — experienced 1995 earthquake closure"],
        "industry_notes":  "Pharmaceutical, chemicals, machinery",
        "peak_risk_months": [8, 9],
    },
    "busan": {
        "natural_risks":   ["typhoon (occasional)", "winter ice delays"],
        "geopolitical":    ["North Korea — proximity creates latent risk"],
        "infrastructure":  ["Korea's main container port", "highly efficient"],
        "industry_notes":  "Automotive parts, electronics, steel",
        "peak_risk_months": [8, 9],
    },

    # ── Southeast Asia ────────────────────────────────────────────────────────
    "ho chi minh city": {
        "natural_risks":   ["flooding (Oct–Nov)", "typhoon (rare but possible)"],
        "geopolitical":    ["Low — improving trade relations"],
        "infrastructure":  ["Cat Lai port — frequent congestion", "road infrastructure developing"],
        "industry_notes":  "Electronics, garments, footwear — major China+1 destination",
        "peak_risk_months": [10, 11],
    },
    "haiphong": {
        "natural_risks":   ["typhoon (Jul–Oct)", "flooding"],
        "geopolitical":    ["South China Sea tensions"],
        "infrastructure":  ["Lach Huyen deep-water port", "northern Vietnam manufacturing hub"],
        "industry_notes":  "Samsung electronics, automotive, heavy manufacturing",
        "peak_risk_months": [7, 8, 9, 10],
    },
    "jakarta": {
        "natural_risks":   ["flooding (Dec–Feb)", "earthquake", "volcanic activity"],
        "geopolitical":    ["Low — stable"],
        "infrastructure":  ["Tanjung Priok port — congestion common", "road infrastructure poor"],
        "industry_notes":  "Consumer goods, textiles, palm oil, mining",
        "peak_risk_months": [12, 1, 2],
    },
    "bangkok": {
        "natural_risks":   ["flooding (Oct–Nov — severe in 2011, 2022)", "drought"],
        "geopolitical":    ["Moderate — periodic political instability/coups"],
        "infrastructure":  ["Laem Chabang port", "2011 floods shut factories for months"],
        "industry_notes":  "Automotive (HDD), electronics, food processing",
        "peak_risk_months": [10, 11],
    },
    "manila": {
        "natural_risks":   ["typhoon (HIGH — 20+ per year)", "earthquake", "flooding"],
        "geopolitical":    ["South China Sea tensions", "US-China proxy concerns"],
        "infrastructure":  ["Port of Manila — chronic congestion", "power outages common"],
        "industry_notes":  "Electronics, garments, BPO — HIGH natural disaster exposure",
        "peak_risk_months": [7, 8, 9, 10, 11],
    },
    "dhaka": {
        "natural_risks":   ["flooding (Jun–Sep)", "cyclone (Apr–May, Oct–Nov)"],
        "geopolitical":    ["Moderate — labor unrest, political instability"],
        "infrastructure":  ["Chittagong port — 200km away, road delays common"],
        "industry_notes":  "World's #2 garment exporter — labor strikes are frequent",
        "peak_risk_months": [4, 5, 6, 7, 8, 9, 10, 11],
    },
    "chittagong": {
        "natural_risks":   ["cyclone (HIGH)", "flooding", "storm surge"],
        "geopolitical":    ["Labor unrest in garment sector"],
        "infrastructure":  ["Only major port in Bangladesh — single point of failure"],
        "industry_notes":  "All Bangladesh garment/textile exports pass through here",
        "peak_risk_months": [4, 5, 10, 11],
    },
    "karachi": {
        "natural_risks":   ["cyclone (Jun, Oct)", "flooding (monsoon)", "heatwave"],
        "geopolitical":    ["HIGH — political instability, terrorism risk, India tensions"],
        "infrastructure":  ["Port Qasim + Karachi port", "power outages frequent"],
        "industry_notes":  "Textiles, chemicals, leather — high geopolitical risk",
        "peak_risk_months": [6, 7, 8, 9, 10],
    },
    "mumbai": {
        "natural_risks":   ["flooding (Jun–Sep — severe)", "cyclone (rare)"],
        "geopolitical":    ["Low-moderate — India-Pakistan tensions"],
        "infrastructure":  ["JNPT port — busiest in India but congestion-prone"],
        "industry_notes":  "Pharmaceuticals, textiles, chemicals, finance",
        "peak_risk_months": [6, 7, 8, 9],
    },
    "colombo": {
        "natural_risks":   ["monsoon (May–Aug, Dec–Jan)", "tsunami risk (Indian Ocean)"],
        "geopolitical":    ["Moderate — post-2022 economic crisis still recovering"],
        "infrastructure":  ["Colombo port — Indian Ocean hub", "economic instability affects reliability"],
        "industry_notes":  "Garments, tea, rubber — recovering from 2022 economic collapse",
        "peak_risk_months": [5, 6, 7, 8, 12, 1],
    },
    "singapore": {
        "natural_risks":   ["Low — no typhoons, minimal earthquake risk"],
        "geopolitical":    ["Low — neutral hub", "South China Sea proximity"],
        "infrastructure":  ["World's most efficient port", "highly redundant"],
        "industry_notes":  "Global transshipment hub — disruption here affects all Asia-EU routes",
        "peak_risk_months": [],  # no strong seasonal risk
    },

    # ── Middle East ───────────────────────────────────────────────────────────
    "dubai": {
        "natural_risks":   ["extreme heat (Jun–Sep)", "rare flooding"],
        "geopolitical":    ["Iran tensions", "Yemen conflict proximity", "Strait of Hormuz risk"],
        "infrastructure":  ["Jebel Ali — world's 9th busiest port", "highly efficient"],
        "industry_notes":  "Global transshipment, re-export hub for Middle East + Africa",
        "peak_risk_months": [1, 2, 3],  # geopolitical tension periods
    },
    "jeddah": {
        "natural_risks":   ["extreme heat", "flooding (rare but severe)"],
        "geopolitical":    ["Yemen war proximity", "Houthi attacks on Red Sea shipping"],
        "infrastructure":  ["Islamic Port of Jeddah", "Red Sea route disruptions ongoing"],
        "industry_notes":  "Red Sea shipping crisis directly affects all Jeddah cargo",
        "peak_risk_months": [1, 2, 3, 4, 5, 6],  # ongoing Houthi threat
    },

    # ── Europe ────────────────────────────────────────────────────────────────
    "rotterdam": {
        "natural_risks":   ["flooding (sea level — managed)", "winter storms"],
        "geopolitical":    ["Low — EU stability", "Russia-Ukraine energy impacts"],
        "infrastructure":  ["Europe's largest port", "highly resilient"],
        "industry_notes":  "Gateway for European imports — disruption cascades continent-wide",
        "peak_risk_months": [11, 12, 1, 2],  # winter storms
    },
    "hamburg": {
        "natural_risks":   ["winter storms", "Elbe river flooding"],
        "geopolitical":    ["Low", "Russia-Ukraine energy crisis impacts"],
        "infrastructure":  ["Germany's largest port", "rail connections to all of Europe"],
        "industry_notes":  "Automotive, machinery, chemicals — Germany's export engine",
        "peak_risk_months": [11, 12, 1, 2],
    },
    "barcelona": {
        "natural_risks":   ["drought (increasing)", "heat waves", "occasional flooding"],
        "geopolitical":    ["Moderate — Catalan independence tensions, periodic strikes"],
        "infrastructure":  ["Major Mediterranean port", "frequent labor strikes"],
        "industry_notes":  "Automotive, chemicals, food — labor strikes are recurring",
        "peak_risk_months": [6, 7, 8],  # heat + labor action
    },
    "valencia": {
        "natural_risks":   ["flooding (HIGH — DANA storms Oct–Nov)", "drought", "heat"],
        "geopolitical":    ["Low"],
        "infrastructure":  ["Spain's busiest port by volume"],
        "industry_notes":  "Automotive (Ford), ceramics, citrus — DANA flash floods are severe",
        "peak_risk_months": [10, 11],  # DANA cold drop storms
    },
    "istanbul": {
        "natural_risks":   ["earthquake (HIGH — North Anatolian Fault)", "winter storms"],
        "geopolitical":    ["HIGH — Ukraine war proximity", "Syria border", "Kurdish tensions"],
        "infrastructure":  ["Bosphorus — global chokepoint for Black Sea shipping"],
        "industry_notes":  "Textiles, automotive, chemicals — earthquake risk is significant",
        "peak_risk_months": [1, 2, 11, 12],
    },
    "genoa": {
        "natural_risks":   ["flooding (Liguria — frequent)", "storms"],
        "geopolitical":    ["Low"],
        "infrastructure":  ["Italy's busiest port", "Morandi bridge collapse history"],
        "industry_notes":  "Automotive, steel, chemicals",
        "peak_risk_months": [10, 11, 12],
    },

    # ── Americas ──────────────────────────────────────────────────────────────
    "los angeles": {
        "natural_risks":   ["earthquake (HIGH — San Andreas)", "wildfire", "drought"],
        "geopolitical":    ["Labor disputes — ILWU longshore strikes recurring"],
        "infrastructure":  ["LA/Long Beach — US's busiest port complex"],
        "industry_notes":  "Gateway for 40% of US imports — any disruption is national news",
        "peak_risk_months": [9, 10, 11],  # wildfire + occasional labor action
    },
    "long beach": {
        "natural_risks":   ["earthquake", "wildfire"],
        "geopolitical":    ["ILWU labor disputes"],
        "infrastructure":  ["Part of LA/LB complex — shared congestion risk"],
        "industry_notes":  "Co-manages US West Coast import gateway",
        "peak_risk_months": [9, 10, 11],
    },
    "houston": {
        "natural_risks":   ["hurricane (Jun–Nov)", "flooding (Harvey-scale risk)", "tornado"],
        "geopolitical":    ["Low"],
        "infrastructure":  ["Port of Houston — US energy + chemical hub"],
        "industry_notes":  "Petrochemicals, energy, aerospace — hurricane season is serious",
        "peak_risk_months": [8, 9, 10],
    },
    "manzanillo": {
        "natural_risks":   ["hurricane (Jun–Nov)", "earthquake", "tsunami"],
        "geopolitical":    ["Cartel activity in Colima state"],
        "infrastructure":  ["Mexico's busiest Pacific port"],
        "industry_notes":  "Automotive, electronics from Asia to Mexico factories",
        "peak_risk_months": [7, 8, 9, 10],
    },
    "santos": {
        "natural_risks":   ["flooding", "landslides (Jan–Mar)"],
        "geopolitical":    ["Moderate — port worker strikes periodic"],
        "infrastructure":  ["Latin America's largest port — chronic congestion"],
        "industry_notes":  "Agricultural exports (soy, coffee), automotive imports",
        "peak_risk_months": [1, 2, 3],
    },

    # ── Africa ────────────────────────────────────────────────────────────────
    "lagos": {
        "natural_risks":   ["flooding (Jun–Sep)", "oil spills"],
        "geopolitical":    ["HIGH — political instability, port corruption, security"],
        "infrastructure":  ["Apapa port — chronic congestion, one of world's worst"],
        "industry_notes":  "Nigeria oil, consumer goods — infrastructure is major risk factor",
        "peak_risk_months": [6, 7, 8, 9],
    },
    "durban": {
        "natural_risks":   ["flooding (Apr 2022 — historic)", "cyclone (rare)"],
        "geopolitical":    ["Moderate — labor strikes, political instability"],
        "infrastructure":  ["Africa's busiest port — 2022 floods caused 3-month disruption"],
        "industry_notes":  "Automotive, mining, agricultural exports",
        "peak_risk_months": [3, 4, 5],
    },
    "suez": {
        "natural_risks":   ["sandstorm", "extreme heat"],
        "geopolitical":    ["HIGH — Ever Given 2021", "Houthi Red Sea attacks ongoing"],
        "infrastructure":  ["Suez Canal — 12% of global trade passes through"],
        "industry_notes":  "Any Suez closure affects EVERY global supply chain simultaneously",
        "peak_risk_months": [1, 2, 3, 4],  # ongoing Red Sea crisis
    },
}

# ─── Seasonal Risk Calendar ────────────────────────────────────────────────────
# Used when city profile isn't in our database

REGIONAL_SEASONAL_RISK = {
    "AS": {  # Asia
        "high_months": [6, 7, 8, 9, 10],
        "reason": "Typhoon and monsoon season across Southeast and East Asia"
    },
    "SA": {  # South America
        "high_months": [1, 2, 3, 12],
        "reason": "Summer flooding and landslide season in Brazil, Colombia, Peru"
    },
    "NA": {  # North America
        "high_months": [8, 9, 10],
        "reason": "Atlantic hurricane season affecting Gulf Coast and East Coast ports"
    },
    "EU": {  # Europe
        "high_months": [11, 12, 1, 2],
        "reason": "Winter storms affecting North Sea and Baltic port operations"
    },
    "AF": {  # Africa
        "high_months": [6, 7, 8, 9],
        "reason": "West African monsoon season; East African long rains"
    },
    "OC": {  # Oceania
        "high_months": [12, 1, 2, 3],
        "reason": "Southern hemisphere cyclone season"
    },
}

# ─── Industry Vulnerability Profiles ─────────────────────────────────────────

INDUSTRY_VULNERABILITY = {
    "electronics":      {"lead_time_weeks": 12, "substitutability": "low",  "just_in_time": True},
    "semiconductor":    {"lead_time_weeks": 26, "substitutability": "very low", "just_in_time": False},
    "automotive":       {"lead_time_weeks": 8,  "substitutability": "low",  "just_in_time": True},
    "apparel":          {"lead_time_weeks": 16, "substitutability": "medium","just_in_time": False},
    "garment":          {"lead_time_weeks": 16, "substitutability": "medium","just_in_time": False},
    "pharma":           {"lead_time_weeks": 24, "substitutability": "very low","just_in_time": False},
    "pharmaceutical":   {"lead_time_weeks": 24, "substitutability": "very low","just_in_time": False},
    "chemicals":        {"lead_time_weeks": 6,  "substitutability": "medium","just_in_time": False},
    "raw materials":    {"lead_time_weeks": 4,  "substitutability": "high",  "just_in_time": False},
    "food":             {"lead_time_weeks": 2,  "substitutability": "medium","just_in_time": True},
    "logistics":        {"lead_time_weeks": 1,  "substitutability": "high",  "just_in_time": True},
    "manufacturing":    {"lead_time_weeks": 8,  "substitutability": "medium","just_in_time": False},
}


def _get_industry_profile(category: str) -> dict:
    cat_lower = category.lower()
    for key, profile in INDUSTRY_VULNERABILITY.items():
        if key in cat_lower:
            return profile
    return {"lead_time_weeks": 8, "substitutability": "medium", "just_in_time": False}


def _get_city_profile(city: str) -> Optional[dict]:
    return CITY_RISK_PROFILES.get(city.lower().strip())


def _get_current_month() -> int:
    from datetime import datetime
    return datetime.now().month


def _is_peak_risk_season(city_profile: Optional[dict], continent: str) -> tuple[bool, str]:
    """Returns (is_peak, explanation)."""
    month = _get_current_month()

    if city_profile:
        peak_months = city_profile.get("peak_risk_months", [])
        if month in peak_months:
            return True, f"Currently in peak risk season for this city ({city_profile.get('natural_risks', [''])[0]})"
        return False, "Not currently in peak risk season for this city"

    regional = REGIONAL_SEASONAL_RISK.get(continent, {})
    if month in regional.get("high_months", []):
        return True, regional.get("reason", "Regional high-risk season")
    return False, "Not currently in regional peak risk period"


# ─── Rule-Based Prediction Engine ────────────────────────────────────────────

def generate_rule_based_predictions(
    supplier_name: str,
    city: str,
    country: str,
    category: str,
    tier: str,
    risk_score: float,
    risk_level: str,
    lat: Optional[float],
    lon: Optional[float],
    breakdown: list[dict],
    continent: str = "AS"
) -> PredictionReport:
    """
    Generate city-aware forward-looking predictions using rule-based logic.
    """
    from datetime import datetime

    city_profile    = _get_city_profile(city)
    industry        = _get_industry_profile(category)
    is_peak, season_note = _get_peak_risk_season(city_profile, continent)
    counted_events  = [e for e in breakdown if e.get("counted")]
    closest_miles   = next((e["miles"] for e in counted_events if e.get("miles")), None)
    top_signals     = [e["signal"] for e in counted_events[:3]]
    has_high_signal = "high" in top_signals
    lead_time_wks   = industry["lead_time_weeks"]

    # Build city risk profile text
    if city_profile:
        nat_risks = ", ".join(city_profile.get("natural_risks", [])[:2])
        geo_risks = city_profile.get("geopolitical", "Unknown")
        city_risk_text = (
            f"{city} has known exposure to: {nat_risks}. "
            f"Geopolitical context: {geo_risks}. "
            f"{city_profile.get('industry_notes', '')}"
        )
    else:
        city_risk_text = (
            f"No specific city risk profile for {city}. "
            f"Using regional patterns for {country}. "
            f"Standard supply chain risk applies."
        )

    # Active threat description
    if counted_events:
        top = counted_events[0]
        dist_str = f"{top['miles']:,} miles from {city}" if top.get("miles") else f"in {country}"
        active_threat = (
            f"Active {top['signal']}-signal event detected {dist_str}: "
            f"\"{top['title'][:80]}\""
        )
    else:
        active_threat = f"No high-impact events currently detected near {city}."

    # ── 72-hour horizon ──────────────────────────────────────────────────────
    if risk_score >= 60 and closest_miles and closest_miles < 100:
        h72_trajectory   = "ESCALATING"
        h72_probability  = 75
        h72_impact       = "Severe"
        h72_narrative    = (
            f"High-signal disruption within {closest_miles} miles of {city}. "
            f"Expect immediate operational impact within 72 hours. "
            f"Contact {supplier_name} TODAY to confirm facility status and "
            f"request emergency shipment of any in-progress orders."
        )
        h72_triggers     = [
            "Supplier confirms facility closure or reduced capacity",
            "Event moves within 50 miles of city center",
            "Port serving this city announces closure or delays",
        ]
        h72_icon = "🔴"
    elif risk_score >= 40:
        h72_trajectory   = "UNCERTAIN"
        h72_probability  = 40
        h72_impact       = "Moderate"
        h72_narrative    = (
            f"Elevated activity near {city} but direct impact in 72 hours is not certain. "
            f"Monitor closely. Check supplier's communication channels for updates. "
            f"Prepare contingency order list in case rapid action is needed."
        )
        h72_triggers     = [
            "Risk score increases above 60",
            "New high-signal event appears within 150 miles",
            "Supplier fails to respond to status inquiry",
        ]
        h72_icon = "🟡"
    else:
        h72_trajectory   = "STABLE"
        h72_probability  = 10
        h72_impact       = "None"
        h72_narrative    = (
            f"No immediate threat to {city} operations in the next 72 hours. "
            f"Current events are distant or low-signal. Normal operations expected."
        )
        h72_triggers     = ["Any new high-signal event within 200 miles"]
        h72_icon = "🟢"

    # ── 7-day horizon ────────────────────────────────────────────────────────
    if is_peak and risk_score >= 30:
        h7_trajectory    = "ESCALATING"
        h7_probability   = 60
        h7_impact        = "Moderate to Severe"
        h7_narrative     = (
            f"{city} is currently in its peak risk season ({season_note}). "
            f"Combined with active events, the 7-day outlook is concerning. "
            f"Lead time for {category} from {city} is typically {lead_time_wks} weeks — "
            f"any order placed today will not arrive before the risk window passes. "
            f"Stock up from existing inventory NOW if possible."
        )
        h7_triggers      = [
            f"Weather service issues warnings for {country}",
            "Risk score stays above 40 for 3+ consecutive days",
            f"Other {category} suppliers in region also show elevated scores",
        ]
        h7_icon = "🔴"
    elif risk_score >= 50:
        h7_trajectory    = "UNCERTAIN"
        h7_probability   = 50
        h7_impact        = "Moderate"
        h7_narrative     = (
            f"Current disruption risk near {city} may persist or intensify over 7 days. "
            f"With a {lead_time_wks}-week lead time for {category}, "
            f"any new order placed this week will arrive after the risk window. "
            f"Focus on expediting existing orders and building buffer from current stock."
        )
        h7_triggers      = [
            "Disruption confirmed at supplier facility",
            "Port serving city announces operational changes",
        ]
        h7_icon = "🟡"
    else:
        h7_trajectory    = "STABLE"
        h7_probability   = 15
        h7_impact        = "Minor"
        h7_narrative     = (
            f"7-day outlook for {city} is stable. Current events do not suggest "
            f"escalation. Normal ordering cadence is appropriate. "
            f"Use this window to review safety stock levels for {category}."
        )
        h7_triggers      = [f"Peak season arrival (if applicable to {city})"]
        h7_icon = "🟢"

    # ── 30-day horizon ───────────────────────────────────────────────────────
    if city_profile and is_peak:
        h30_trajectory   = "ELEVATED"
        h30_probability  = 45
        h30_impact       = "Moderate"
        h30_narrative    = (
            f"The 30-day outlook for {city} reflects its known seasonal patterns. "
            f"{season_note}. "
            f"Industry lead time for {category} is {lead_time_wks} weeks, meaning "
            f"decisions made today directly affect your supply 30 days from now. "
            f"Consider placing a larger-than-usual order this week as a buffer."
        )
        h30_triggers     = [
            f"Seasonal risk materializes (e.g., typhoon, flooding, labor action)",
            f"Geopolitical situation in {country} deteriorates",
            "Risk score trend increases week-over-week",
        ]
        h30_icon = "🟡"
    elif risk_score >= 60:
        h30_trajectory   = "UNCERTAIN"
        h30_probability  = 35
        h30_impact       = "Moderate"
        h30_narrative    = (
            f"High current risk score suggests the situation in {city} may not resolve "
            f"within 30 days. Begin qualifying alternative {category} suppliers now — "
            f"qualification typically takes 4–12 weeks, so starting today is already late."
        )
        h30_triggers     = [
            "Situation escalates to conflict or infrastructure failure",
            "Supplier requests force majeure",
        ]
        h30_icon = "🟡"
    else:
        h30_trajectory   = "STABLE"
        h30_probability  = 10
        h30_impact       = "None to Minor"
        h30_narrative    = (
            f"No significant 30-day risk factors identified for {city}. "
            f"Standard supply chain planning applies. "
            f"Use this stable period to review dual-sourcing options as a long-term resilience measure."
        )
        h30_triggers     = [f"New geopolitical development in {country}"]
        h30_icon = "🟢"

    # ── Cascade risks ────────────────────────────────────────────────────────
    cascade = []
    if city_profile:
        if "port" in city_profile.get("infrastructure", "").lower():
            cascade.append(f"Port disruption in {city} affects ALL exporters in the region, not just {supplier_name}")
        if "semiconductor" in city_profile.get("industry_notes", "").lower():
            cascade.append(f"Semiconductor supply from {city} has 6–18 month ripple effects across electronics globally")
        if "automotive" in city_profile.get("industry_notes", "").lower():
            cascade.append(f"Automotive JIT supply chains from {city} can halt assembly lines within days")
    if industry.get("just_in_time"):
        cascade.append(f"{category} from {city} is likely on JIT schedules — even 3-day delays trigger production stops")
    if industry.get("substitutability") in ("low", "very low"):
        cascade.append(f"{category} has low substitutability — alternative sourcing takes weeks to qualify")

    # Data sources
    data_sources = [e["source"][:30] for e in counted_events[:3]] or ["No events matched"]

    return PredictionReport(
        supplier_name     = supplier_name,
        city              = city,
        country           = country,
        category          = category,
        lat               = lat,
        lon               = lon,
        city_risk_profile = city_risk_text,
        active_threat     = active_threat,
        seasonal_context  = season_note,
        horizons          = [
            PredictionHorizon("72 hours", h72_trajectory, h72_probability, h72_impact,
                              h72_narrative, h72_triggers, h72_icon),
            PredictionHorizon("7 days",   h7_trajectory,  h7_probability,  h7_impact,
                              h7_narrative,  h7_triggers,  h7_icon),
            PredictionHorizon("30 days",  h30_trajectory, h30_probability, h30_impact,
                              h30_narrative, h30_triggers, h30_icon),
        ],
        cascade_risks     = cascade,
        confidence        = "rule-based",
        data_sources_used = data_sources,
    )


def _get_peak_risk_season(city_profile, continent):
    return _is_peak_risk_season(city_profile, continent)


# ─── GPT-Enhanced Predictions ─────────────────────────────────────────────────

GPT_PREDICTION_PROMPT = """You are a senior supply chain risk analyst with deep knowledge of global geography, port operations, weather patterns, and geopolitical risk.

A client needs forward-looking risk predictions for this specific supplier:

Supplier: {supplier_name}
City: {city}, {country}  
Coordinates: {lat}, {lon}
Category: {category} (Industry lead time: ~{lead_time_wks} weeks)
Tier: {tier}
Current Risk Score: {score}/100 ({level})

City Risk Context:
{city_profile_text}

Active events near this city:
{events_text}

Provide a JSON prediction with this exact structure:
{{
  "city_risk_profile": "2 sentences about this city's inherent supply chain risk characteristics",
  "active_threat": "1 sentence describing the current most significant threat to this city",
  "seasonal_context": "1 sentence about whether this is a high-risk season for this city/region",
  "horizons": [
    {{
      "timeframe": "72 hours",
      "risk_trajectory": "ESCALATING|STABLE|IMPROVING|UNCERTAIN",
      "probability_of_disruption": 0-100,
      "expected_impact": "None|Minor|Moderate|Severe",
      "narrative": "2-3 sentences: what will likely happen in this timeframe and why",
      "triggers_to_watch": ["specific signal 1", "specific signal 2"]
    }},
    {{
      "timeframe": "7 days",
      "risk_trajectory": "...",
      "probability_of_disruption": 0-100,
      "expected_impact": "...",
      "narrative": "2-3 sentences",
      "triggers_to_watch": ["..."]
    }},
    {{
      "timeframe": "30 days",
      "risk_trajectory": "...",
      "probability_of_disruption": 0-100,
      "expected_impact": "...",
      "narrative": "2-3 sentences",
      "triggers_to_watch": ["..."]
    }}
  ],
  "cascade_risks": ["secondary effect 1", "secondary effect 2", "secondary effect 3"],
  "confidence": 0-100
}}

Be city-specific. Reference actual geography, known risks for this city, and the active events. Do not give generic country-level advice."""


def generate_ai_predictions(
    supplier_name: str,
    city: str,
    country: str,
    category: str,
    tier: str,
    risk_score: float,
    risk_level: str,
    lat: Optional[float],
    lon: Optional[float],
    breakdown: list[dict],
    openai_api_key: str
) -> Optional[PredictionReport]:
    """GPT-enhanced city-aware predictions. Returns None if unavailable."""
    if not openai_api_key:
        return None

    city_profile   = _get_city_profile(city)
    industry       = _get_industry_profile(category)
    counted_events = [e for e in breakdown if e.get("counted")][:5]

    city_profile_text = ""
    if city_profile:
        city_profile_text = (
            f"Natural risks: {', '.join(city_profile.get('natural_risks', []))}\n"
            f"Geopolitical: {city_profile.get('geopolitical', 'Unknown')}\n"
            f"Infrastructure: {city_profile.get('infrastructure', 'Unknown')}\n"
            f"Industry notes: {city_profile.get('industry_notes', 'Unknown')}"
        )
    else:
        city_profile_text = f"No specific profile. General {country} risk applies."

    events_text = "\n".join(
        f"- [{e['signal'].upper()}] {e['title']} ({e.get('proximity','unknown distance')}, {e['published']})"
        for e in counted_events
    ) or "No specific events detected near this city."

    prompt = GPT_PREDICTION_PROMPT.format(
        supplier_name     = supplier_name,
        city              = city,
        country           = country,
        lat               = f"{lat:.4f}" if lat else "unknown",
        lon               = f"{lon:.4f}" if lon else "unknown",
        category          = category,
        lead_time_wks     = industry["lead_time_weeks"],
        tier              = tier,
        score             = f"{risk_score:.0f}",
        level             = risk_level,
        city_profile_text = city_profile_text,
        events_text       = events_text
    )

    try:
        import openai
    except ImportError:
        openai = None
        client = openai.OpenAI(api_key=openai_api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.2,
            response_format={"type": "json_object"}
        )

        data = json.loads(response.choices[0].message.content)

        TRAJ_ICONS = {
            "ESCALATING": "🔴", "STABLE": "🟢",
            "IMPROVING": "🔵", "UNCERTAIN": "🟡", "ELEVATED": "🟡"
        }

        horizons = []
        for h in data.get("horizons", []):
            traj = h.get("risk_trajectory", "UNCERTAIN")
            horizons.append(PredictionHorizon(
                timeframe                = h.get("timeframe", ""),
                risk_trajectory          = traj,
                probability_of_disruption= int(h.get("probability_of_disruption", 20)),
                expected_impact          = h.get("expected_impact", "Unknown"),
                narrative                = h.get("narrative", ""),
                triggers_to_watch        = h.get("triggers_to_watch", []),
                icon                     = TRAJ_ICONS.get(traj, "⚪")
            ))

        data_sources = [e["source"][:30] for e in counted_events] or ["No events matched"]

        return PredictionReport(
            supplier_name     = supplier_name,
            city              = city,
            country           = country,
            category          = category,
            lat               = lat,
            lon               = lon,
            city_risk_profile = data.get("city_risk_profile", city_profile_text),
            active_threat     = data.get("active_threat", "No active threat identified"),
            seasonal_context  = data.get("seasonal_context", ""),
            horizons          = horizons,
            cascade_risks     = data.get("cascade_risks", []),
            confidence        = "ai-enhanced",
            data_sources_used = data_sources,
        )

    except Exception:
        return None


# ─── Master Entry Point ───────────────────────────────────────────────────────

def get_predictions(
    supplier_name: str,
    city: str,
    country: str,
    category: str,
    tier: str,
    risk_score: float,
    risk_level: str,
    lat: Optional[float],
    lon: Optional[float],
    breakdown: list[dict],
    openai_api_key: str = "",
    continent: str = "AS"
) -> PredictionReport:
    """
    Get city-aware predictions. Uses AI if available, otherwise rule-based.
    Always returns a valid PredictionReport.
    """
    if openai_api_key:
        ai_report = generate_ai_predictions(
            supplier_name, city, country, category, tier,
            risk_score, risk_level, lat, lon, breakdown, openai_api_key
        )
        if ai_report:
            return ai_report

    return generate_rule_based_predictions(
        supplier_name, city, country, category, tier,
        risk_score, risk_level, lat, lon, breakdown, continent
    )
