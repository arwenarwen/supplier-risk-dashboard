"""
recommendations.py - Decision support engine for supplier risk.

Given a supplier's risk profile and the events driving that risk,
generates actionable recommendations: what to do, how urgently,
and whether to stock up, redirect, dual-source, or monitor.

Works in two modes:
  1. Rule-based (always available, no API key needed)
  2. GPT-enhanced (richer, context-aware, requires OpenAI key)
"""

import os
import json
from dataclasses import dataclass, field
from typing import Optional

# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Action:
    priority: int           # 1 = most urgent
    action: str             # Short action label
    detail: str             # Full explanation
    timeframe: str          # "Immediate", "Within 48h", "This week", "Monitor"
    icon: str               # Emoji for UI
    category: str           # inventory | redirect | dual_source | monitor | escalate


@dataclass
class RecommendationReport:
    supplier_name: str
    risk_score: float
    risk_level: str
    situation_summary: str          # One-paragraph plain-English summary
    actions: list[Action]           # Ordered list of recommended actions
    lead_time_warning: str          # Specific lead time concern if applicable
    alternative_note: str           # Note about finding alternatives
    do_not_do: list[str]            # Common mistakes to avoid in this situation
    confidence: str                 # "rule-based" or "ai-enhanced"


# ─── Disruption Type Profiles ─────────────────────────────────────────────────
# Each disruption type has different characteristics that drive different advice

DISRUPTION_PROFILES = {
    "natural_disaster": {
        "typical_duration":  "1–4 weeks",
        "predictability":    "moderate",   # typhoons have warning, earthquakes don't
        "recovery_speed":    "slow",
        "stock_up_advice":   True,
        "redirect_advice":   True,
        "typical_notice":    "0–10 days depending on type",
    },
    "labor_strike": {
        "typical_duration":  "1–3 weeks",
        "predictability":    "high",       # usually announced in advance
        "recovery_speed":    "moderate",
        "stock_up_advice":   True,
        "redirect_advice":   True,
        "typical_notice":    "3–14 days (usually announced)",
    },
    "war_conflict": {
        "typical_duration":  "months to years",
        "predictability":    "low",
        "recovery_speed":    "very slow",
        "stock_up_advice":   False,        # stocking up doesn't help long-term conflicts
        "redirect_advice":   True,
        "typical_notice":    "little to none",
    },
    "trade_policy": {
        "typical_duration":  "months to years",
        "predictability":    "moderate",
        "recovery_speed":    "slow",
        "stock_up_advice":   True,         # tariff hikes — stock before they hit
        "redirect_advice":   True,
        "typical_notice":    "days to weeks (policy announcements)",
    },
    "logistics_failure": {
        "typical_duration":  "days to 2 weeks",
        "predictability":    "low",
        "recovery_speed":    "fast",
        "stock_up_advice":   True,
        "redirect_advice":   False,        # usually resolves before redirect is practical
        "typical_notice":    "0–3 days",
    },
    "infrastructure_damage": {
        "typical_duration":  "1–8 weeks",
        "predictability":    "low",
        "recovery_speed":    "moderate",
        "stock_up_advice":   True,
        "redirect_advice":   True,
        "typical_notice":    "0–2 days",
    },
    "shortage": {
        "typical_duration":  "weeks to months",
        "predictability":    "moderate",
        "recovery_speed":    "slow",
        "stock_up_advice":   True,
        "redirect_advice":   True,
        "typical_notice":    "days to weeks",
    },
    "other": {
        "typical_duration":  "unknown",
        "predictability":    "low",
        "recovery_speed":    "moderate",
        "stock_up_advice":   True,
        "redirect_advice":   False,
        "typical_notice":    "unknown",
    },
}

# ─── Tier-Specific Advice ─────────────────────────────────────────────────────

TIER_CONTEXT = {
    "1": "This is a Tier 1 (direct) supplier — disruption impacts you immediately.",
    "2": "This is a Tier 2 supplier — disruption affects your Tier 1 suppliers first, giving you slightly more buffer time.",
    "3": "This is a Tier 3 (raw material) supplier — you likely have 2–4 weeks buffer before this reaches your production.",
}

# ─── Rule-Based Recommendation Engine ────────────────────────────────────────

def _score_to_urgency(score: float) -> str:
    if score >= 75: return "CRITICAL"
    if score >= 60: return "HIGH"
    if score >= 40: return "MEDIUM"
    if score >= 20: return "LOW"
    return "WATCH"


def _detect_disruption_types(breakdown: list[dict]) -> list[str]:
    """Infer disruption types from event titles in the breakdown."""
    types = set()
    for ev in breakdown[:5]:
        title = ev.get("title", "").lower()
        if any(w in title for w in ["earthquake", "tsunami", "flood", "typhoon",
                                     "hurricane", "cyclone", "storm", "wildfire"]):
            types.add("natural_disaster")
        if any(w in title for w in ["strike", "walkout", "labor", "union", "worker"]):
            types.add("labor_strike")
        if any(w in title for w in ["war", "conflict", "military", "invasion",
                                     "missile", "attack", "coup"]):
            types.add("war_conflict")
        if any(w in title for w in ["tariff", "sanction", "embargo", "trade war",
                                     "export ban", "import ban"]):
            types.add("trade_policy")
        if any(w in title for w in ["port congestion", "vessel", "grounded",
                                     "canal", "shipping delay", "blocked"]):
            types.add("logistics_failure")
        if any(w in title for w in ["explosion", "fire", "collapse", "pipeline",
                                     "power outage", "blackout"]):
            types.add("infrastructure_damage")
        if any(w in title for w in ["shortage", "scarcity", "rationing",
                                     "semiconductor", "chip shortage"]):
            types.add("shortage")
    return list(types) if types else ["other"]


def _closest_event_distance(breakdown: list[dict]) -> Optional[int]:
    """Return the distance in miles of the closest scored event, if any."""
    for ev in breakdown:
        if ev.get("miles") is not None and ev.get("counted"):
            return ev["miles"]
    return None


def generate_rule_based_recommendations(
    supplier_name: str,
    supplier_city: str,
    supplier_country: str,
    supplier_tier: str,
    supplier_category: str,
    risk_score: float,
    risk_level: str,
    breakdown: list[dict],
    events_summary: str,
) -> RecommendationReport:
    """
    Generate actionable recommendations using rule-based logic.
    No API key required. Always available.
    """
    urgency          = _score_to_urgency(risk_score)
    disruption_types = _detect_disruption_types(breakdown)
    closest_miles    = _closest_event_distance(breakdown)
    tier_note        = TIER_CONTEXT.get(str(supplier_tier), "")
    top_event_title  = breakdown[0]["title"][:100] if breakdown else "No events detected"
    counted_events   = [e for e in breakdown if e.get("counted")]

    # Build situation summary
    dist_str = f"{closest_miles:,} miles from {supplier_city}" if closest_miles else f"in {supplier_country}"
    dtype_str = " and ".join(disruption_types).replace("_", " ")

    situation_summary = (
        f"{supplier_name} in {supplier_city}, {supplier_country} is currently rated "
        f"**{risk_level} risk** ({risk_score:.0f}/100). "
        f"The primary driver is **{dtype_str}** activity detected {dist_str}. "
        f"Most recent signal: \"{top_event_title}\". "
        f"{tier_note} "
        f"{'Immediate action is recommended.' if risk_score >= 60 else 'Situation warrants close monitoring.'}"
    )

    actions: list[Action] = []
    do_not_do: list[str] = []
    lead_time_warning = ""
    alternative_note  = ""

    # ── Decision tree based on disruption type + score + tier ────────────────

    for dtype in disruption_types:
        profile = DISRUPTION_PROFILES.get(dtype, DISRUPTION_PROFILES["other"])

        # ── Natural Disaster ──────────────────────────────────────────────────
        if dtype == "natural_disaster":
            if closest_miles is not None and closest_miles < 200:
                if risk_score >= 60:
                    actions.append(Action(
                        priority=1,
                        action="📦 Stock Up Immediately",
                        detail=(
                            f"A natural disaster is active within {closest_miles} miles of {supplier_city}. "
                            f"Contact {supplier_name} TODAY to confirm current operational status. "
                            f"If operational, place an emergency purchase order for 4–8 weeks of safety stock "
                            f"before the disruption worsens. Typical recovery: {profile['typical_duration']}."
                        ),
                        timeframe="Immediate (within 24h)",
                        icon="📦",
                        category="inventory"
                    ))
                    actions.append(Action(
                        priority=2,
                        action="🔄 Identify Backup Supplier",
                        detail=(
                            f"In parallel, identify at least one alternative supplier for {supplier_category} "
                            f"outside of {supplier_country}. Even if you don't activate them, having a confirmed "
                            f"backup with quoted lead times and pricing protects you if the situation escalates."
                        ),
                        timeframe="Within 48 hours",
                        icon="🔄",
                        category="redirect"
                    ))
                    do_not_do.append("Don't wait for official confirmation — natural disasters move faster than communications.")
                    do_not_do.append("Don't cancel existing orders without confirming the supplier is actually affected.")
                elif risk_score >= 30:
                    actions.append(Action(
                        priority=1,
                        action="📞 Contact Supplier for Status Update",
                        detail=(
                            f"A {dtype.replace('_',' ')} event is within {closest_miles} miles. "
                            f"Reach out to {supplier_name} to confirm their facilities are unaffected. "
                            f"Request a contingency plan and ask about their own backup production capacity."
                        ),
                        timeframe="Within 48 hours",
                        icon="📞",
                        category="monitor"
                    ))
                    actions.append(Action(
                        priority=2,
                        action="📊 Review Current Inventory Levels",
                        detail=(
                            f"Check how many weeks of stock you currently hold for {supplier_category} "
                            f"from this supplier. If below 4 weeks, consider a precautionary top-up order."
                        ),
                        timeframe="This week",
                        icon="📊",
                        category="inventory"
                    ))

        # ── Labor Strike ─────────────────────────────────────────────────────
        elif dtype == "labor_strike":
            actions.append(Action(
                priority=1,
                action="📞 Confirm Strike Scope with Supplier",
                detail=(
                    f"Strikes are often announced 3–14 days in advance. Contact {supplier_name} immediately "
                    f"to understand: (1) Is it at their specific facility or a nearby port? "
                    f"(2) What is the expected duration? (3) Do they have a contingency shipping plan? "
                    f"Port strikes affect outbound shipping even if the factory is unaffected."
                ),
                timeframe="Immediate",
                icon="📞",
                category="escalate"
            ))
            if risk_score >= 50:
                actions.append(Action(
                    priority=2,
                    action="📦 Accelerate In-Transit Orders",
                    detail=(
                        f"If you have any orders currently in production or awaiting shipment, "
                        f"request {supplier_name} to expedite shipping before the strike begins. "
                        f"Even a partial shipment now reduces your exposure significantly."
                    ),
                    timeframe="Within 48 hours",
                    icon="📦",
                    category="inventory"
                ))
                actions.append(Action(
                    priority=3,
                    action="🔀 Map Alternative Shipping Routes",
                    detail=(
                        f"If the strike is port-specific, ask {supplier_name} if they can route "
                        f"through an alternative port (e.g., if Valencia port is striking, can they "
                        f"ship via Barcelona or Algeciras?). Air freight may be cost-justified for "
                        f"high-value or time-critical items."
                    ),
                    timeframe="Within 48 hours",
                    icon="🔀",
                    category="redirect"
                ))
            do_not_do.append("Don't assume the strike will resolve quickly — port strikes in Europe often last 2–4 weeks.")
            lead_time_warning = f"Strike-related delays typically add 2–6 weeks to lead times from {supplier_country}."

        # ── War / Conflict ────────────────────────────────────────────────────
        elif dtype == "war_conflict":
            actions.append(Action(
                priority=1,
                action="🚨 Escalate to Procurement Leadership",
                detail=(
                    f"Active conflict near {supplier_city} poses a serious long-term supply risk. "
                    f"This requires executive-level decision making. Escalate immediately with a "
                    f"brief on current inventory levels, open orders, and alternative suppliers."
                ),
                timeframe="Immediate",
                icon="🚨",
                category="escalate"
            ))
            actions.append(Action(
                priority=2,
                action="🔄 Begin Supplier Qualification in Safe Region",
                detail=(
                    f"Start qualification of an alternative {supplier_category} supplier outside "
                    f"the conflict zone. Target a country with no geopolitical overlap with {supplier_country}. "
                    f"Conflicts rarely resolve within months — plan for 3–12 month supply gap."
                ),
                timeframe="This week",
                icon="🔄",
                category="dual_source"
            ))
            actions.append(Action(
                priority=3,
                action="📦 Build 60–90 Day Safety Stock",
                detail=(
                    f"While the supplier is still operational, build a larger buffer than usual. "
                    f"Conflict situations deteriorate unpredictably. 60–90 days of stock gives you "
                    f"time to qualify and onboard an alternative supplier without production stoppage."
                ),
                timeframe="Within 2 weeks",
                icon="📦",
                category="inventory"
            ))
            do_not_do.append("Don't assume the conflict will stay localized — supply chain impacts spread faster than news coverage.")
            do_not_do.append("Don't place large new orders that may be undeliverable or create financial risk if supplier becomes unreachable.")
            alternative_note = f"Finding a {supplier_category} supplier outside {supplier_country} should be treated as a priority project, not a contingency plan."

        # ── Trade Policy / Sanctions / Tariffs ───────────────────────────────
        elif dtype == "trade_policy":
            actions.append(Action(
                priority=1,
                action="⚖️ Assess Tariff / Sanction Impact",
                detail=(
                    f"Trade policy changes affecting {supplier_country} could significantly change "
                    f"your landed cost or legality of imports. Engage your trade compliance team or "
                    f"customs broker immediately to understand: (1) Which HS codes are affected? "
                    f"(2) What is the effective date? (3) Are there exemptions?"
                ),
                timeframe="Within 48 hours",
                icon="⚖️",
                category="escalate"
            ))
            if risk_score >= 50:
                actions.append(Action(
                    priority=2,
                    action="📦 Front-Load Orders Before Effective Date",
                    detail=(
                        f"If a tariff hike or import ban has an announced effective date, "
                        f"place larger orders now to build inventory at the current duty rate. "
                        f"Calculate the cost differential — even 3–4 months of extra stock may "
                        f"be cheaper than paying higher tariffs on every future shipment."
                    ),
                    timeframe="This week",
                    icon="📦",
                    category="inventory"
                ))
                actions.append(Action(
                    priority=3,
                    action="🌍 Evaluate Country-of-Origin Shift",
                    detail=(
                        f"Explore whether {supplier_name} or a comparable supplier can produce "
                        f"in a country not subject to the new restrictions. Many manufacturers have "
                        f"facilities in multiple countries for exactly this reason. Ask {supplier_name} "
                        f"if they can ship equivalent product from a non-affected facility."
                    ),
                    timeframe="This month",
                    icon="🌍",
                    category="redirect"
                ))
            do_not_do.append("Don't assume your current classification is correct — tariff schedules are complex and misclassification is common.")

        # ── Logistics Failure ─────────────────────────────────────────────────
        elif dtype == "logistics_failure":
            actions.append(Action(
                priority=1,
                action="🚢 Check In-Transit Shipment Status",
                detail=(
                    f"Port congestion or logistics failures affect shipments already en route. "
                    f"Check the status of all open purchase orders from {supplier_name} immediately. "
                    f"Contact your freight forwarder for real-time vessel/container tracking updates."
                ),
                timeframe="Immediate",
                icon="🚢",
                category="monitor"
            ))
            actions.append(Action(
                priority=2,
                action="✈️ Evaluate Air Freight for Critical Orders",
                detail=(
                    f"For urgent or high-value orders stuck in congested ports, air freight may be "
                    f"worth the premium. Logistics failures typically resolve in 1–2 weeks, so "
                    f"air freight makes sense only for items that will cause production stoppage."
                ),
                timeframe="Within 48 hours",
                icon="✈️",
                category="redirect"
            ))
            lead_time_warning = f"Port congestion typically adds 1–3 weeks to ocean freight lead times from {supplier_country}."
            do_not_do.append("Don't cancel orders that are already in transit — rerouting is usually cheaper than cancellation fees.")

        # ── Shortage ──────────────────────────────────────────────────────────
        elif dtype == "shortage":
            actions.append(Action(
                priority=1,
                action="📦 Secure Allocation with Supplier",
                detail=(
                    f"During shortages, suppliers often allocate to their largest or longest-standing "
                    f"customers first. Contact {supplier_name} to formally confirm your allocation "
                    f"and understand if there are quantity limits per order period."
                ),
                timeframe="Immediate",
                icon="📦",
                category="inventory"
            ))
            actions.append(Action(
                priority=2,
                action="🔍 Qualify Alternative Sources",
                detail=(
                    f"Shortages in {supplier_category} typically affect multiple suppliers in the "
                    f"same region. Start qualifying an alternative supplier in a different geography "
                    f"that may have better access to the scarce input material."
                ),
                timeframe="This week",
                icon="🔍",
                category="dual_source"
            ))
            do_not_do.append("Don't rely on spot market purchases — during shortages, spot prices can be 2–5x contract rates.")

    # ── Universal monitoring action (always added) ────────────────────────────
    actions.append(Action(
        priority=len(actions) + 1,
        action="📡 Set Up Daily Monitoring",
        detail=(
            f"Enable auto-refresh in the dashboard sidebar to track {supplier_name}'s risk score "
            f"daily. Set a Slack or email alert for when the score crosses 60. "
            f"Re-evaluate your response plan if the score increases by more than 15 points."
        ),
        timeframe="Ongoing",
        icon="📡",
        category="monitor"
    ))

    # ── Tier-specific lead time warning ──────────────────────────────────────
    if not lead_time_warning:
        tier_num = str(supplier_tier)
        if tier_num == "1":
            lead_time_warning = "As a Tier 1 supplier, any disruption here impacts your production directly. No buffer from downstream."
        elif tier_num == "2":
            lead_time_warning = "As a Tier 2 supplier, your Tier 1 suppliers absorb the first impact. You typically have 1–3 weeks before it reaches you."
        else:
            lead_time_warning = "As a Tier 3 supplier, you have the most buffer time — typically 3–6 weeks before raw material shortages affect production."

    # ── Alternative supplier note ─────────────────────────────────────────────
    if not alternative_note:
        if risk_score >= 60:
            alternative_note = (
                f"Given the High risk score, begin searching for an alternative {supplier_category} "
                f"supplier in a different region. Even a qualified backup gives you negotiating leverage "
                f"and insurance against escalation."
            )
        elif risk_score >= 30:
            alternative_note = (
                f"Consider identifying 1–2 backup {supplier_category} suppliers as a precaution. "
                f"You don't need to activate them, but having vetted alternatives on file dramatically "
                f"reduces response time if the situation worsens."
            )
        else:
            alternative_note = (
                f"Risk is currently low. Use this as an opportunity to map potential backup {supplier_category} "
                f"suppliers so you're prepared if conditions change."
            )

    # Sort actions by priority
    actions.sort(key=lambda a: a.priority)

    return RecommendationReport(
        supplier_name=supplier_name,
        risk_score=risk_score,
        risk_level=risk_level,
        situation_summary=situation_summary,
        actions=actions,
        lead_time_warning=lead_time_warning,
        alternative_note=alternative_note,
        do_not_do=do_not_do[:4],  # Max 4 don'ts
        confidence="rule-based"
    )


# ─── GPT-Enhanced Recommendations ────────────────────────────────────────────

GPT_RECOMMENDATION_PROMPT = """You are a senior supply chain risk consultant. A client needs urgent, practical advice about a supplier risk situation.

Supplier: {supplier_name}
Location: {city}, {country}
Category: {category}
Tier: {tier}
Risk Score: {score}/100 ({level})
Top Events Driving Risk:
{events_text}

Provide a JSON response with this exact structure:
{{
  "situation_summary": "2-3 sentence plain English summary of what is happening and why it matters",
  "urgency": "CRITICAL|HIGH|MEDIUM|LOW|WATCH",
  "primary_recommendation": "The single most important thing to do right now",
  "actions": [
    {{
      "priority": 1,
      "action": "Short action label",
      "detail": "2-3 sentence specific instruction including who to contact and what to ask",
      "timeframe": "Immediate|Within 48h|This week|This month|Ongoing",
      "category": "inventory|redirect|dual_source|monitor|escalate"
    }}
  ],
  "stock_up_recommendation": {{
    "should_stock_up": true or false,
    "weeks_of_stock": "recommended safety stock in weeks e.g. 6-8",
    "reasoning": "one sentence"
  }},
  "redirect_recommendation": {{
    "should_redirect": true or false,
    "alternative_regions": ["list of alternative regions/countries to source from"],
    "reasoning": "one sentence"
  }},
  "lead_time_warning": "Specific lead time concern for this situation",
  "do_not_do": ["mistake 1", "mistake 2", "mistake 3"],
  "confidence": 0-100
}}

Be specific, practical, and direct. No generic advice. Reference the actual events and location."""


def generate_ai_recommendations(
    supplier_name: str,
    supplier_city: str,
    supplier_country: str,
    supplier_tier: str,
    supplier_category: str,
    risk_score: float,
    risk_level: str,
    breakdown: list[dict],
    openai_api_key: str
) -> Optional[RecommendationReport]:
    """
    Generate GPT-enhanced recommendations. Falls back to None if unavailable.
    """
    if not openai_api_key:
        return None

    # Build events text for prompt
    counted = [e for e in breakdown if e.get("counted")][:5]
    events_text = "\n".join(
        f"- [{e['signal'].upper()}] {e['title']} ({e['proximity']}, {e['published']})"
        for e in counted
    ) or "No specific events detected."

    prompt = GPT_RECOMMENDATION_PROMPT.format(
        supplier_name=supplier_name,
        city=supplier_city,
        country=supplier_country,
        category=supplier_category,
        tier=supplier_tier,
        score=f"{risk_score:.0f}",
        level=risk_level,
        events_text=events_text
    )

    try:
        import openai
        client = openai.OpenAI(api_key=openai_api_key)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1200,
            temperature=0.3,
            response_format={"type": "json_object"}
        )

        data = json.loads(response.choices[0].message.content)

        # Convert to RecommendationReport
        actions = []
        for a in data.get("actions", []):
            icon_map = {
                "inventory":    "📦",
                "redirect":     "🔄",
                "dual_source":  "🔀",
                "monitor":      "📡",
                "escalate":     "🚨",
            }
            cat = a.get("category", "monitor")
            actions.append(Action(
                priority=a.get("priority", 99),
                action=a.get("action", ""),
                detail=a.get("detail", ""),
                timeframe=a.get("timeframe", "This week"),
                icon=icon_map.get(cat, "📋"),
                category=cat
            ))

        stock = data.get("stock_up_recommendation", {})
        redirect = data.get("redirect_recommendation", {})

        alt_regions = redirect.get("alternative_regions", [])
        alt_note = redirect.get("reasoning", "")
        if alt_regions:
            alt_note = f"Consider sourcing from: {', '.join(alt_regions[:3])}. {alt_note}"

        return RecommendationReport(
            supplier_name=supplier_name,
            risk_score=risk_score,
            risk_level=risk_level,
            situation_summary=data.get("situation_summary", ""),
            actions=sorted(actions, key=lambda a: a.priority),
            lead_time_warning=data.get("lead_time_warning", ""),
            alternative_note=alt_note,
            do_not_do=data.get("do_not_do", [])[:4],
            confidence="ai-enhanced"
        )

    except Exception:
        return None


# ─── Master Entry Point ───────────────────────────────────────────────────────

def get_recommendations(
    supplier_name: str,
    supplier_city: str,
    supplier_country: str,
    supplier_tier: str,
    supplier_category: str,
    risk_score: float,
    risk_level: str,
    breakdown: list[dict],
    events_summary: str = "",
    openai_api_key: str = ""
) -> RecommendationReport:
    """
    Generate recommendations using AI if available, otherwise rule-based.
    Always returns a valid RecommendationReport.
    """
    # Try AI first
    if openai_api_key:
        ai_report = generate_ai_recommendations(
            supplier_name, supplier_city, supplier_country,
            supplier_tier, supplier_category,
            risk_score, risk_level, breakdown, openai_api_key
        )
        if ai_report:
            return ai_report

    # Fallback to rule-based
    return generate_rule_based_recommendations(
        supplier_name, supplier_city, supplier_country,
        supplier_tier, supplier_category,
        risk_score, risk_level, breakdown, events_summary
    )
