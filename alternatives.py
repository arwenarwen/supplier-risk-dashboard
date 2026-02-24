"""
alternatives.py - Two systems working together:

1. COUNTDOWN PARSER
   Detects articles that contain a future decision/action deadline
   e.g. "will decide within 10 days", "strike vote in 5 days", "ultimatum expires Friday"
   Converts them into a scored countdown event with a deadline date and
   a confidence-weighted risk score that increases as the deadline approaches.

2. ALTERNATIVE SUPPLIER ENGINE
   When a supplier is flagged High/Medium risk, searches the uploaded supplier
   list for alternatives in the same category. If none exist in the same
   country/region, suggests 2-3 alternative sourcing regions based on:
   - Lower geopolitical risk
   - Shorter typical lead time
   - Category-specific sourcing knowledge
"""

import re
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional
import math


# ─── Part 1: Countdown Event Parser ──────────────────────────────────────────

@dataclass
class CountdownEvent:
    headline: str
    published_date: datetime
    deadline_date: datetime
    days_remaining: int           # Negative = deadline passed
    confidence: int               # 0–100: how likely is the event to happen
    risk_type: str                # "military_strike" | "sanctions" | "trade_decision" | etc.
    affected_regions: list[str]   # Countries/regions directly affected
    supply_chain_impact: str      # Plain English: what breaks if this happens
    score_multiplier: float       # How much this boosts the supplier risk score


# Patterns to detect countdown language in article text
COUNTDOWN_PATTERNS = [
    # "will decide within X days"
    (r"will decide within[^\d]*(\d+)\s*days?", 1.0),
    # "within X days"
    (r"within[^\d]*(\d+)\s*days?", 0.9),
    # "in the next X days"
    (r"in the next[^\d]*(\d+)\s*days?", 0.9),
    # "decision expected within X days"
    (r"(?:decision|ruling|verdict|announcement) expected[^\d]*(\d+)\s*days?", 0.85),
    # "deadline in X days" / "expires in X days"
    (r"(?:deadline|ultimatum|expires?)[^\d]*(\d+)\s*days?", 0.9),
    # "X-day ultimatum" / "10-day window"
    (r"(\d+)[\s-]day[s]? (?:ultimatum|deadline|window|warning|period|countdown)", 0.85),
    # "by [day of week]" — harder to parse, use 3-day estimate
    (r"by (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)", 0.7),
    # "hours to decide" 
    (r"(\d+)\s*hours? to decide", 0.95),
    # "strike within the week"
    (r"strike within the week", 0.8),
    # "imminent" — treat as 3-day window
    (r"\bimminent\b", 0.75),
]

# Risk type detection
RISK_TYPE_PATTERNS = {
    "military_strike": [
        "strike iran", "military strike", "attack iran", "bomb", "strike on",
        "military action", "air strike", "naval strike", "troops deployed",
        "carriers repositioned", "military buildup", "pre-emptive strike",
    ],
    "sanctions": [
        "sanctions", "embargo", "export ban", "import ban", "trade ban",
        "will sanction", "considering sanctions", "new tariff",
    ],
    "trade_decision": [
        "trade deal", "trade agreement", "tariff decision", "wto ruling",
        "trade war", "trade negotiation",
    ],
    "labor_action": [
        "strike vote", "walkout", "work stoppage", "labor action",
        "union vote", "industrial action",
    ],
    "political_crisis": [
        "coup", "election dispute", "political crisis", "regime change",
        "government collapse", "civil unrest",
    ],
    "infrastructure_closure": [
        "port closure", "canal closure", "strait closure", "border closure",
        "airspace closure", "terminal shutdown",
    ],
}

# Supply chain impact library — what breaks by risk type
SUPPLY_CHAIN_IMPACTS = {
    "military_strike": (
        "Military action in the Gulf region would likely close the Strait of Hormuz "
        "— 21% of global oil transit. Expect immediate oil price spikes (+20–40%), "
        "LNG shortages, and shipping rerouting via Cape of Good Hope adding 10–14 days "
        "and $500–800K per voyage. All Middle East-origin manufacturing costs rise immediately."
    ),
    "sanctions": (
        "New sanctions create immediate compliance risk on open orders and payments. "
        "Suppliers in sanctioned countries may become unreachable. "
        "Expect 2–6 week delays as banks suspend transactions and logistics providers "
        "suspend routes pending legal review."
    ),
    "trade_decision": (
        "Trade policy changes can immediately increase landed costs by the tariff percentage. "
        "Existing orders in transit may face unexpected duties on arrival. "
        "Qualification of alternative suppliers typically takes 8–16 weeks."
    ),
    "labor_action": (
        "Port or factory strikes halt outbound shipments immediately. "
        "Typical duration 1–3 weeks. In-transit cargo may be stranded. "
        "Air freight alternative adds $3–8/kg premium."
    ),
    "political_crisis": (
        "Political instability creates unpredictable factory shutdowns, "
        "border closures, and banking system disruptions. "
        "Lead times become unreliable and force majeure clauses activate."
    ),
    "infrastructure_closure": (
        "Infrastructure closure creates hard stop on all shipments through that route. "
        "Rerouting adds days/weeks and significant freight cost premium."
    ),
}

# Regions affected by risk type (for geo-matching)
RISK_REGION_MAP = {
    "military_strike": {
        "iran":         ["Iran", "Iraq", "UAE", "Saudi Arabia", "Kuwait", "Bahrain",
                         "Qatar", "Oman", "Jordan", "Israel", "Lebanon"],
        "ukraine":      ["Ukraine", "Russia", "Belarus", "Poland", "Romania", "Moldova"],
        "taiwan strait":["Taiwan", "China", "Philippines", "Japan", "South Korea"],
    },
    "sanctions": {},   # Dynamic — depends on target country
}


def detect_countdown(title: str, description: str, published_date_str: str) -> Optional[CountdownEvent]:
    """
    Scan an article for countdown language.
    Returns a CountdownEvent if found, None otherwise.
    """
    text = f"{title} {description}".lower()

    # Parse published date
    pub_date = None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
                "%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d"):
        try:
            pub_date = datetime.strptime(published_date_str[:26].strip(), fmt)
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)
            break
        except Exception:
            continue
    if pub_date is None:
        pub_date = datetime.now(timezone.utc)

    # Try each countdown pattern
    days_from_pub = None
    base_confidence = 0.7

    for pattern, conf in COUNTDOWN_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            base_confidence = conf
            if m.lastindex and m.lastindex >= 1:
                try:
                    days_from_pub = int(m.group(1))
                except (ValueError, IndexError):
                    days_from_pub = 3  # Default for patterns without capture group
            else:
                # Pattern matched but no day number — use defaults
                if "imminent" in pattern:
                    days_from_pub = 2
                elif "week" in pattern:
                    days_from_pub = 7
                else:
                    days_from_pub = 3
            break

    if days_from_pub is None:
        return None  # No countdown language found

    # Detect risk type
    risk_type = "political_crisis"  # Default
    for rtype, keywords in RISK_TYPE_PATTERNS.items():
        if any(kw in text for kw in keywords):
            risk_type = rtype
            break

    # Calculate deadline
    deadline = pub_date + timedelta(days=days_from_pub)
    now = datetime.now(timezone.utc)
    days_remaining = (deadline - now).days

    # Confidence increases as deadline approaches, caps when passed
    if days_remaining > days_from_pub:
        confidence = int(base_confidence * 60)        # Far out — lower confidence
    elif days_remaining > 3:
        confidence = int(base_confidence * 75)        # Getting close
    elif days_remaining >= 0:
        confidence = int(base_confidence * 95)        # Imminent
    else:
        confidence = int(base_confidence * 40)        # Deadline passed — may or may not have happened

    # Score multiplier: higher as deadline approaches
    if days_remaining < 0:
        multiplier = 0.6   # Passed — reduce but keep (event may have occurred)
    elif days_remaining == 0:
        multiplier = 2.0   # Today is the deadline
    elif days_remaining <= 3:
        multiplier = 1.8   # Imminent
    elif days_remaining <= 7:
        multiplier = 1.5   # This week
    elif days_remaining <= 14:
        multiplier = 1.2   # Next 2 weeks
    else:
        multiplier = 0.9   # Further out

    # Affected regions
    affected = []
    for region_key, countries in RISK_REGION_MAP.get(risk_type, {}).items():
        if region_key in text:
            affected = countries
            break
    if not affected:
        # Extract country mentions from title
        try:
            import pycountry as _pc
            for word in title.split():
                try:
                    c = _pc.countries.lookup(word.strip(",."))
                    affected.append(c.name)
                except Exception:
                    pass
        except ImportError:
            pass
        if False:  # placeholder
            except Exception:
                pass

    return CountdownEvent(
        headline=title[:200],
        published_date=pub_date,
        deadline_date=deadline,
        days_remaining=days_remaining,
        confidence=confidence,
        risk_type=risk_type,
        affected_regions=affected[:10],
        supply_chain_impact=SUPPLY_CHAIN_IMPACTS.get(risk_type, "Supply chain impact being assessed."),
        score_multiplier=multiplier,
    )


def countdown_to_score_boost(event: CountdownEvent) -> float:
    """
    Convert a countdown event into a score boost for affected suppliers.
    Returns a points value (0–25) to add to the supplier's risk score.
    """
    if event.days_remaining < -7:
        return 0.0  # Well past deadline, likely resolved
    base = 15.0 * (event.confidence / 100) * event.score_multiplier
    return min(base, 25.0)


# ─── Part 2: Alternative Supplier Engine ─────────────────────────────────────

# Regional risk tiers — lower is safer/more stable
REGION_RISK_TIER = {
    # Tier 1 — Very Stable
    "Germany": 1, "Netherlands": 1, "Japan": 1, "South Korea": 1,
    "Taiwan": 1, "Singapore": 1, "United States": 1, "Canada": 1,
    "Australia": 1, "Sweden": 1, "Denmark": 1, "Finland": 1,
    "Switzerland": 1, "Austria": 1, "Czech Republic": 1, "Poland": 1,
    # Tier 2 — Mostly Stable
    "China": 2, "Vietnam": 2, "Malaysia": 2, "Thailand": 2, "Mexico": 2,
    "Brazil": 2, "India": 2, "Indonesia": 2, "Philippines": 2,
    "Morocco": 2, "Turkey": 2, "Portugal": 2, "Spain": 2,
    "Italy": 2, "France": 2, "United Kingdom": 2,
    # Tier 3 — Elevated Risk
    "Bangladesh": 3, "Sri Lanka": 3, "Cambodia": 3, "Myanmar": 3,
    "Pakistan": 3, "Egypt": 3, "Nigeria": 3, "Kenya": 3,
    "Ethiopia": 3, "Ghana": 3, "South Africa": 3, "Colombia": 3,
    # Tier 4 — High Risk
    "Iran": 4, "Iraq": 4, "Libya": 4, "Yemen": 4, "Syria": 4,
    "Afghanistan": 4, "Sudan": 4, "Haiti": 4, "Venezuela": 4,
    "Russia": 4, "Belarus": 4, "North Korea": 4, "Ukraine": 4,
    "Lebanon": 4, "Somalia": 4, "Mali": 4, "Burkina Faso": 4,
}

# Category → best alternative sourcing regions (ordered by preference)
CATEGORY_ALTERNATIVES = {
    "electronics": [
        ("Vietnam",     "Strong electronics base, China+1 destination, 3–4 week lead time"),
        ("Malaysia",    "Penang electronics hub, established supply chain, English-speaking"),
        ("South Korea", "High-quality components, stable politics, 3–4 week lead time"),
        ("Taiwan",      "World-class electronics, but Taiwan Strait risk to consider"),
        ("Thailand",    "Growing electronics sector, Bangkok-area industrial zones"),
        ("Mexico",      "Nearshore for US buyers, 1–2 week lead time, USMCA advantage"),
    ],
    "semiconductor": [
        ("South Korea", "Samsung, SK Hynix — world-class fabs, stable"),
        ("Taiwan",      "TSMC — highest quality but geopolitical risk"),
        ("Japan",       "Mature semiconductor industry, stable, high quality"),
        ("United States", "Reshoring incentives, CHIPS Act investment, longer lead time"),
        ("Germany",     "Infineon, Bosch — European fab, stable, premium pricing"),
    ],
    "automotive": [
        ("Mexico",      "Major automotive hub, USMCA, 1–2 week nearshore for US"),
        ("Germany",     "Premium automotive supply chain, stable, higher cost"),
        ("South Korea", "Hyundai/Kia ecosystem, competitive pricing"),
        ("Japan",       "Toyota supplier network, world-class quality systems"),
        ("Czech Republic", "Central European automotive hub, EU stability"),
        ("Poland",      "Growing automotive sector, EU member, competitive cost"),
    ],
    "apparel": [
        ("Vietnam",     "World's #2 apparel exporter, competitive pricing, improving quality"),
        ("Cambodia",    "Low cost, garment-specialist country, improving infrastructure"),
        ("Indonesia",   "Large garment sector, Jakarta/Surabaya factories, stable"),
        ("Morocco",     "Fast-fashion nearshore for Europe, 1–2 week lead time"),
        ("Turkey",      "Premium fabrics, Europe-nearshore, 1–2 week lead time"),
        ("Ethiopia",    "Very low cost, improving infrastructure, growing sector"),
    ],
    "garment": [
        ("Vietnam",     "Strong garment base, improving quality, competitive"),
        ("Cambodia",    "Specialist garment country, low cost"),
        ("Indonesia",   "Large garment capacity"),
        ("Turkey",      "Nearshore for Europe, premium positioning"),
        ("Morocco",     "Ultra-nearshore for Europe, fast lead times"),
    ],
    "pharma": [
        ("India",       "World's pharmacy — generic drugs, APIs, FDA-approved plants"),
        ("Germany",     "Premium pharma, stable, highest regulatory standards"),
        ("Switzerland", "Precision pharma, Novartis/Roche ecosystem"),
        ("United States", "FDA-compliant, reshoring incentives, higher cost"),
        ("Ireland",     "Major pharma hub, EU regulatory, English-speaking"),
    ],
    "chemicals": [
        ("Germany",     "BASF — world's largest chemical company, stable"),
        ("Netherlands", "Rotterdam chemical cluster, global hub"),
        ("India",       "Growing chemical sector, competitive pricing"),
        ("South Korea", "LG Chem, SK Innovation — advanced chemicals"),
        ("United States", "Gulf Coast chemical cluster, competitive for North America"),
    ],
    "raw materials": [
        ("Australia",   "Mining stable, major iron ore/coal exporter"),
        ("Canada",      "Stable mining sector, diversified minerals"),
        ("Chile",       "Copper dominant, stable mining regulation"),
        ("Brazil",      "Iron ore, soybeans — large volumes, port congestion watch"),
        ("South Africa","Platinum, chrome — stable but labor strike risk"),
    ],
    "food": [
        ("Netherlands", "AgriFood innovation hub, EU compliant, global distributor"),
        ("Brazil",      "Huge food producer, soy/coffee/sugar/chicken"),
        ("United States","Diversified food production, stable"),
        ("Argentina",   "Soy, corn, beef — competitive pricing"),
        ("Australia",   "Premium food, clean supply chains, stable"),
    ],
    "logistics": [
        ("Singapore",   "World's most efficient logistics hub"),
        ("Netherlands", "Rotterdam — Europe's gateway, excellent infrastructure"),
        ("UAE",         "Dubai — Middle East/Africa hub, Jebel Ali efficiency"),
        ("Germany",     "DHL, DB Schenker HQ, central Europe logistics"),
    ],
    "manufacturing": [
        ("Vietnam",     "Fastest-growing manufacturing hub, competitive cost"),
        ("Mexico",      "Nearshore for North America, USMCA, growing capacity"),
        ("Malaysia",    "Stable, English-speaking, quality manufacturing"),
        ("Poland",      "EU manufacturing, educated workforce, central location"),
        ("Czech Republic","Central European manufacturing excellence"),
    ],
    "default": [
        ("Vietnam",     "Diversified manufacturing, fastest-growing sourcing hub"),
        ("Malaysia",    "Stable, English-speaking, strong infrastructure"),
        ("Mexico",      "Nearshore Americas option, USMCA, 1–2 week lead time"),
        ("Germany",     "Premium European source, highest stability"),
        ("India",       "Scale and cost-competitive across most categories"),
    ],
}


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def find_alternatives(
    at_risk_supplier: dict,
    all_suppliers: list[dict],
    risk_reason: str = "",
    countdown_event: Optional[CountdownEvent] = None,
) -> dict:
    """
    Given a high-risk supplier, find 2-3 alternatives.

    Strategy:
    1. First look in your own uploaded supplier list — same category, lower risk
    2. If none found in same country/region, suggest regional alternatives
    3. If none in region, suggest globally based on category knowledge

    Returns dict with:
      - internal_alternatives: suppliers from your own list
      - regional_suggestions: curated region recommendations
      - recommendation: plain English summary of what to do
      - urgency: "immediate" | "this_week" | "plan_ahead"
    """
    name     = at_risk_supplier.get("supplier_name", "")
    category = str(at_risk_supplier.get("category", "")).lower().strip()
    country  = at_risk_supplier.get("country", "")
    lat      = at_risk_supplier.get("latitude")
    lon      = at_risk_supplier.get("longitude")

    # ── Step 1: Search your own supplier list ─────────────────────────────────
    internal = []
    for s in all_suppliers:
        if s.get("supplier_name") == name:
            continue  # Skip the at-risk supplier itself
        s_cat  = str(s.get("category", "")).lower().strip()
        s_risk = str(s.get("risk_level", "Low"))
        s_score= float(s.get("risk_score", 0))

        # Must be same or related category
        if not (category in s_cat or s_cat in category or
                any(word in s_cat for word in category.split())):
            continue

        # Must be lower risk
        if s_risk == "High" or s_score >= 60:
            continue

        s_lat = s.get("latitude")
        s_lon = s.get("longitude")
        dist_km = None
        if lat and lon and s_lat and s_lon:
            dist_km = _haversine_km(float(lat), float(lon), float(s_lat), float(s_lon))

        s_country  = s.get("country", "")
        s_risk_tier = REGION_RISK_TIER.get(s_country, 3)
        at_risk_tier = REGION_RISK_TIER.get(country, 3)

        # Score this alternative: lower risk tier = better, same category = better
        # distance doesn't penalize (different country can be fine)
        alt_quality = (5 - s_risk_tier) * 20 + max(0, 40 - s_score)

        internal.append({
            "supplier_name":  s.get("supplier_name"),
            "city":           s.get("city"),
            "country":        s_country,
            "category":       s.get("category"),
            "tier":           s.get("tier"),
            "risk_score":     s_score,
            "risk_level":     s_risk,
            "risk_tier":      s_risk_tier,
            "distance_km":    round(dist_km) if dist_km else None,
            "alt_quality":    alt_quality,
            "already_in_list": True,
        })

    # Sort by quality score
    internal.sort(key=lambda x: x["alt_quality"], reverse=True)

    # ── Step 2: Build regional suggestions ───────────────────────────────────
    # Find best match in CATEGORY_ALTERNATIVES
    cat_key = "default"
    for key in CATEGORY_ALTERNATIVES:
        if key in category or category in key:
            cat_key = key
            break

    # Filter out the at-risk country and any high-risk regions
    at_risk_tier = REGION_RISK_TIER.get(country, 3)
    regional = []
    for alt_country, reason in CATEGORY_ALTERNATIVES[cat_key]:
        if alt_country == country:
            continue  # Don't suggest the same country
        tier = REGION_RISK_TIER.get(alt_country, 3)
        if tier <= at_risk_tier:  # Only suggest equal or safer regions
            regional.append({
                "country":   alt_country,
                "reason":    reason,
                "risk_tier": tier,
                "tier_label": ["Very Stable", "Mostly Stable", "Elevated", "High Risk"][tier-1],
            })

    regional = regional[:3]  # Top 3 only

    # ── Step 3: Build urgency and recommendation ──────────────────────────────
    if countdown_event:
        days = countdown_event.days_remaining
        if days <= 3:
            urgency = "immediate"
            rec = (
                f"⚠️ URGENT: {countdown_event.headline[:80]}... "
                f"Deadline in {max(0, days)} day(s). "
                f"Do NOT place new orders with {name}. "
                f"Contact alternative suppliers TODAY and request emergency quotes."
            )
        elif days <= 10:
            urgency = "this_week"
            rec = (
                f"🟡 Act this week: {countdown_event.headline[:80]}... "
                f"Deadline in {days} days. "
                f"Begin qualification of alternatives now — supplier transition "
                f"typically takes longer than the warning window."
            )
        else:
            urgency = "plan_ahead"
            rec = (
                f"📋 Plan ahead: {countdown_event.headline[:80]}... "
                f"Deadline in {days} days. "
                f"This is enough time to qualify an alternative. Start the process this week."
            )
    else:
        urgency = "this_week"
        rec = (
            f"{name} in {country} is at elevated risk. "
            f"Consider dual-sourcing or qualifying an alternative in a lower-risk region."
        )

    return {
        "internal_alternatives": internal[:3],
        "regional_suggestions":  regional,
        "recommendation":        rec,
        "urgency":               urgency,
        "risk_reason":           risk_reason,
        "countdown":             countdown_event,
    }
