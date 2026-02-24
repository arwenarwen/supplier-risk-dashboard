"""
filtering.py - Three-layer supply chain disruption filter.

Architecture (precision-first, cheap-to-expensive):
  Layer 1: Keyword conjunction filter  — requires BOTH disruption + supply context
  Layer 2: Blocklist exclusion         — rejects known false-positive domains
  Layer 3: LLM semantic classification — GPT-4o-mini final gate (optional, costs ~$0.00015/call)

Usage:
    from filtering import filter_article, FilterResult

    result = filter_article(title, description, openai_api_key)
    if result.passes:
        # store in DB
        store_event(..., disruption_type=result.disruption_type, ...)
"""

import os
import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class FilterResult:
    passes: bool                          # Final verdict: store this article?
    layer_rejected: Optional[int] = None  # Which layer rejected it (1, 2, or None)
    reject_reason: str = ""               # Human-readable reason

    # Layer 3 enrichment (only populated if LLM was called)
    is_supply_chain_disruption: bool = False
    confidence: int = 0
    disruption_type: str = "other"        # See DISRUPTION_TYPES below
    location: str = "Unknown"
    severity: str = "medium"
    reasoning: str = ""
    llm_called: bool = False


DISRUPTION_TYPES = [
    "natural_disaster",    # Earthquake, flood, typhoon, cyclone, drought
    "labor_strike",        # Port strike, factory walkout, union action
    "war_conflict",        # Armed conflict, military action, civil war
    "trade_policy",        # Sanctions, tariffs, export bans, trade restrictions
    "logistics_failure",   # Port congestion, vessel grounding, canal blockage
    "infrastructure_damage", # Bridge collapse, pipeline explosion, power grid failure
    "cyberattack",         # Critical infrastructure cyberattack
    "shortage",            # Raw material, semiconductor, energy shortage
    "pandemic_health",     # Disease outbreak affecting workforce/logistics
    "other",               # Verified disruption but doesn't fit above
]


# ─── Layer 1: Keyword Conjunction Filter ─────────────────────────────────────

# DISRUPTION keywords — must have at least one
DISRUPTION_KEYWORDS_L1 = {
    # Active events
    "strike", "walkout", "shutdown", "closure", "closed", "blocked",
    "grounded", "halted", "suspended", "delayed", "congested", "disrupted",
    "disruption", "outage", "blackout", "explosion", "fire", "collapsed",
    "seized", "detained", "diverted", "stranded", "impounded",
    # Natural disasters
    "earthquake", "tsunami", "typhoon", "hurricane", "cyclone", "tornado",
    "flood", "flooding", "landslide", "volcanic", "wildfire", "drought",
    "blizzard", "snowstorm", "monsoon", "heatwave",
    # Human/political — expanded for conflict zones
    "war", "conflict", "military", "invasion", "coup", "sanctions",
    "embargo", "blockade", "tariff", "export ban", "import ban",
    "trade restriction", "trade war", "protest", "riot", "civil unrest",
    "airstrike", "airstrikes", "missile strike", "missile attack",
    "bombing", "shelling", "attack", "damaged", "destroyed", "reduced",
    "hit by", "targeted", "under attack", "affected by conflict",
    "wartime", "military operation", "armed conflict",
    # Shortages
    "shortage", "scarcity", "rationing", "depletion",
    # Countdown / imminent threat language — critical forward signals
    "will decide", "within days", "within 10 days", "within 48 hours",
    "could strike", "may strike", "considering strike", "potential strike",
    "military action", "troops deployed", "carriers repositioned",
    "assets repositioned", "military buildup", "pre-emptive",
    "decision expected", "deadline approaching", "ultimatum",
    "regime change", "naval blockade", "strait closure",
    "hormuz", "bab-el-mandeb", "persian gulf",
}

# SUPPLY CHAIN CONTEXT keywords — must have at least one
SUPPLY_CONTEXT_KEYWORDS_L1 = {
    # Physical logistics
    "port", "shipping", "freight", "cargo", "container", "vessel", "ship",
    "tanker", "dock", "terminal", "harbor", "harbour", "customs",
    "rail freight", "air freight", "trucking", "haulage", "logistics",
    "supply chain", "warehouse", "distribution", "last mile",
    "canal", "suez", "panama canal", "strait", "bosphorus", "shipping lane",
    # Trade & manufacturing
    "import", "export", "trade", "shipment", "manufacturer", "manufacturing",
    "factory", "plant", "production", "assembly line", "industrial",
    "raw material", "inventory", "procurement",
    # Key industries
    "semiconductor", "chip", "automotive", "steel", "aluminum", "copper",
    "textile", "garment", "pharmaceutical", "chemical", "oil", "gas",
    "fuel", "energy supply", "power grid", "pipeline", "refinery",
    "agriculture", "grain", "wheat", "commodity",
    # Conflict-zone supply chain terms
    "iron ore", "sunflower oil", "fertilizer", "ammonia", "black sea",
    "export capacity", "cargo vessel", "inland rail", "freight cost",
    "trade corridor", "supply route", "evacuation corridor",
    # Oil/energy chokepoints — always supply chain relevant
    "oil supply", "oil price", "oil transit", "crude oil", "energy market",
    "strait of hormuz", "persian gulf", "red sea route", "suez alternative",
    "oil tanker", "lng", "natural gas supply", "energy security",
    # Iran / Gulf specific
    "iran", "tehran", "isfahan", "bandar abbas", "kharg island",
    "gulf region", "middle east conflict", "hormuz", "persian",
    "carriers repositioned", "troops deployed", "military assets",
}


def layer1_keyword_filter(title: str, description: str) -> tuple[bool, str]:
    """
    Requires BOTH a disruption keyword AND a supply chain context keyword.
    Returns (passes: bool, reason: str).
    """
    text = f"{title} {description}".lower()

    has_disruption = any(kw in text for kw in DISRUPTION_KEYWORDS_L1)
    has_context    = any(kw in text for kw in SUPPLY_CONTEXT_KEYWORDS_L1)

    if not has_disruption:
        return False, "No disruption keyword found"
    if not has_context:
        return False, "No supply chain context keyword found"
    return True, ""


# ─── Layer 2: Blocklist Filter ────────────────────────────────────────────────

# Topic patterns that commonly produce false positives.
# Uses simple substring matching on lowercased text.

BLOCKLIST_TOPICS = [
    # ── Medical / Health (contains "shortage", "supply", "disruption") ──
    "tourette", "syndrome", "autism", "adhd", "alzheimer", "dementia",
    "cancer treatment", "chemotherapy", "blood supply", "hospital supply",
    "medical shortage", "drug shortage", "medication shortage",
    "insulin shortage", "vaccine shortage", "mental health",
    "psychiatric", "therapy session", "clinical trial", "diagnosis",
    "neurological", "coprolalia", "bafta incide",  # specific false positive
    "nurse shortage", "doctor shortage", "physician shortage",
    "healthcare worker", "patient care",

    # ── Sports (contains "strike", "war", "conflict", "disruption") ──
    "nba strike", "nfl strike", "mlb strike", "nhl lockout",
    "fifa", "premier league", "champions league", "world cup",
    "olympics disruption", "athlete protest", "player strike",
    "sports conflict", "boxing match", "wrestling",

    # ── Entertainment / Media ──
    "hollywood strike", "writers strike", "actors strike", "sag-aftra",
    "film festival", "box office", "movie disruption", "tv show",
    "celebrity", "bafta", "oscar", "grammy", "emmy", "golden globe",
    "taylor swift", "beyonce", "drake", "kanye", "kardashian",
    "streaming service", "netflix",

    # ── Cybersecurity (not physical supply chain) ──
    "ransomware attack", "data breach", "cyber attack on hospital",
    "phishing campaign", "password leak", "social media hack",
    "personal data", "credit card breach",

    # ── Housing / Real Estate ──
    "housing shortage", "housing crisis", "apartment shortage",
    "rent increase", "mortgage", "property market",

    # ── Crypto / Finance (unless trade-related) ──
    "crypto crash", "bitcoin", "ethereum", "nft", "defi",
    "stock market disruption", "hedge fund",

    # ── General Politics (not affecting goods movement) ──
    "abortion rights", "gun control", "immigration debate",
    "election fraud", "voter suppression", "supreme court ruling",
    "criminal trial", "sexual assault", "domestic violence",
]

BLOCKLIST_SET = set(BLOCKLIST_TOPICS)


def layer2_blocklist_filter(title: str, description: str) -> tuple[bool, str]:
    """
    Rejects articles matching known false-positive topics.
    Returns (passes: bool, reason: str).
    """
    text = f"{title} {description}".lower()
    for term in BLOCKLIST_SET:
        if term in text:
            return False, f"Blocklist match: '{term}'"
    return True, ""


# ─── Layer 3: LLM Semantic Classification ────────────────────────────────────

LLM_SYSTEM_PROMPT = """You are a supply chain risk analyst. Your job is to determine whether a news article describes a real-world event that disrupts the production, transportation, storage, trade, or availability of physical goods.

Analyze the article and return ONLY valid JSON with these fields:
{
  "is_supply_chain_disruption": true or false,
  "confidence": 0-100,
  "disruption_type": one of [natural_disaster, labor_strike, war_conflict, trade_policy, logistics_failure, infrastructure_damage, cyberattack, shortage, pandemic_health, other],
  "location": "country or region name",
  "severity": "low", "medium", or "high",
  "reasoning": "one sentence explaining your decision"
}

Return true ONLY if the article describes disruption to: ports, shipping lanes, freight, cargo, manufacturing plants, factories, rail/road/air freight networks, energy supply chains, agricultural supply, or trade routes.

Return false for: medical/health topics, sports, entertainment, cybersecurity affecting non-logistics targets, housing, cryptocurrency, or general political news not directly affecting goods movement.

Be strict. If in doubt, return false. Prioritize precision over recall."""

LLM_USER_TEMPLATE = """Article title: {title}
Article description: {description}

Classify this article."""

# Track daily LLM call count to avoid runaway costs
_llm_call_count = {"date": "", "count": 0}
LLM_DAILY_BUDGET = int(os.getenv("LLM_DAILY_BUDGET", "500"))  # max GPT calls/day


def _check_llm_budget() -> bool:
    """Returns True if we're within daily LLM budget."""
    today = time.strftime("%Y-%m-%d")
    if _llm_call_count["date"] != today:
        _llm_call_count["date"] = today
        _llm_call_count["count"] = 0
    return _llm_call_count["count"] < LLM_DAILY_BUDGET


def _increment_llm_count():
    _llm_call_count["count"] += 1


def layer3_llm_filter(
    title: str,
    description: str,
    openai_api_key: str,
    min_confidence: int = 70
) -> tuple[bool, dict]:
    """
    Uses GPT-4o-mini to semantically classify the article.
    Returns (passes: bool, structured_result: dict).

    If no API key or budget exhausted, returns (True, {}) — passes through
    so the article isn't lost just because LLM is unavailable.
    """
    if not openai_api_key:
        return True, {"llm_skipped": True, "reason": "No OpenAI key provided"}

    if not _check_llm_budget():
        return True, {"llm_skipped": True, "reason": f"Daily budget of {LLM_DAILY_BUDGET} calls reached"}

    try:
        import openai
        client = openai.OpenAI(api_key=openai_api_key)

        _increment_llm_count()

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": LLM_USER_TEMPLATE.format(
                    title=title[:300],
                    description=description[:500]
                )}
            ],
            max_tokens=200,
            temperature=0,
            response_format={"type": "json_object"}
        )

        raw = response.choices[0].message.content.strip()
        result = json.loads(raw)

        is_disruption = bool(result.get("is_supply_chain_disruption", False))
        confidence    = int(result.get("confidence", 0))
        passes        = is_disruption and confidence >= min_confidence

        return passes, {
            "is_supply_chain_disruption": is_disruption,
            "confidence":      confidence,
            "disruption_type": result.get("disruption_type", "other"),
            "location":        result.get("location", "Unknown"),
            "severity":        result.get("severity", "medium"),
            "reasoning":       result.get("reasoning", ""),
            "llm_called":      True,
        }

    except json.JSONDecodeError:
        # LLM returned non-JSON — pass through with warning
        return True, {"llm_skipped": True, "reason": "LLM returned non-JSON"}
    except Exception as e:
        # Any API error — don't lose the article
        return True, {"llm_skipped": True, "reason": str(e)[:100]}


# ─── Master Filter ────────────────────────────────────────────────────────────

def filter_article(
    title: str,
    description: str,
    openai_api_key: str = "",
    use_llm: bool = True
) -> FilterResult:
    """
    Run all three filter layers in order (cheapest first).
    Returns a FilterResult with full audit trail.

    Layer 1 and 2 are free and instant.
    Layer 3 (LLM) only runs if article passes layers 1 and 2.
    """
    title       = str(title or "").strip()
    description = str(description or "").strip()

    # ── Layer 1: Keyword conjunction ──────────────────────────────────────────
    l1_passes, l1_reason = layer1_keyword_filter(title, description)
    if not l1_passes:
        return FilterResult(
            passes=False,
            layer_rejected=1,
            reject_reason=f"L1: {l1_reason}"
        )

    # ── Layer 2: Blocklist ────────────────────────────────────────────────────
    l2_passes, l2_reason = layer2_blocklist_filter(title, description)
    if not l2_passes:
        return FilterResult(
            passes=False,
            layer_rejected=2,
            reject_reason=f"L2: {l2_reason}"
        )

    # ── Layer 3: LLM semantic classification ──────────────────────────────────
    if use_llm and openai_api_key:
        l3_passes, l3_data = layer3_llm_filter(title, description, openai_api_key)

        if l3_data.get("llm_skipped"):
            # LLM unavailable — pass through based on layers 1+2
            return FilterResult(
                passes=True,
                is_supply_chain_disruption=True,
                confidence=50,
                disruption_type="other",
                location="Unknown",
                severity="medium",
                reasoning=f"LLM skipped: {l3_data.get('reason', '')}",
                llm_called=False
            )

        if not l3_passes:
            return FilterResult(
                passes=False,
                layer_rejected=3,
                reject_reason=f"L3: LLM classified as non-disruption "
                              f"(confidence={l3_data.get('confidence', 0)}, "
                              f"disruption={l3_data.get('is_supply_chain_disruption')})",
                **{k: v for k, v in l3_data.items() if k != "llm_skipped"},
                llm_called=True
            )

        return FilterResult(
            passes=True,
            is_supply_chain_disruption=l3_data.get("is_supply_chain_disruption", True),
            confidence=l3_data.get("confidence", 75),
            disruption_type=l3_data.get("disruption_type", "other"),
            location=l3_data.get("location", "Unknown"),
            severity=l3_data.get("severity", "medium"),
            reasoning=l3_data.get("reasoning", ""),
            llm_called=True
        )

    # No LLM — passed layers 1+2, approve with moderate confidence
    return FilterResult(
        passes=True,
        is_supply_chain_disruption=True,
        confidence=60,
        disruption_type="other",
        location="Unknown",
        severity="medium",
        reasoning="Passed keyword + blocklist filters (LLM not configured)",
        llm_called=False
    )


# ─── Batch Filter (for processing many articles efficiently) ─────────────────

def filter_articles_batch(
    articles: list[dict],
    openai_api_key: str = "",
    use_llm: bool = True
) -> tuple[list[dict], dict]:
    """
    Filter a batch of articles dicts (each with 'title' and 'description').
    Returns (approved_articles, stats_dict).

    Approved articles get enriched with filter result fields:
    disruption_type, severity, confidence, reasoning.
    """
    approved = []
    stats = {
        "total":       len(articles),
        "approved":    0,
        "rejected_l1": 0,
        "rejected_l2": 0,
        "rejected_l3": 0,
        "llm_calls":   0,
    }

    for article in articles:
        title       = article.get("title", "")
        description = article.get("description", "")

        result = filter_article(title, description, openai_api_key, use_llm)

        if result.llm_called:
            stats["llm_calls"] += 1

        if result.passes:
            # Enrich article with LLM classification data
            article["disruption_type"] = result.disruption_type
            article["severity"]        = result.severity
            article["confidence"]      = result.confidence
            article["reasoning"]       = result.reasoning
            # Use LLM location if better than detected country
            if result.location not in ("Unknown", "") and article.get("country") in ("Unknown", "Global", ""):
                article["country"] = result.location
            approved.append(article)
            stats["approved"] += 1
        else:
            layer = result.layer_rejected
            if layer == 1:   stats["rejected_l1"] += 1
            elif layer == 2: stats["rejected_l2"] += 1
            elif layer == 3: stats["rejected_l3"] += 1

    return approved, stats
