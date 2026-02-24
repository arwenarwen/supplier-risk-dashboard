"""
events.py - Multi-source global news ingestion engine.
Sources (all free, no API limits except NewsAPI):
  - Google News RSS (per country, supply chain focused queries)
  - Reuters RSS feeds (global business, trade, commodities)
  - BBC News RSS (global)
  - Al Jazeera RSS (Middle East, Asia, Africa)
  - Channel NewsAsia RSS (Southeast Asia specialist)
  - FreightWaves / Supply Chain Dive (logistics specialists)
  - GDELT Project (150+ countries, 65 languages, near real-time)
  - NewsAPI (English aggregator, 100 req/day free)
  - OpenWeatherMap (weather alerts for major port cities)

Covers all major sourcing & port nations:
  China, Vietnam, Indonesia, India, Bangladesh, Thailand, Malaysia,
  Cambodia, Myanmar, Pakistan, Sri Lanka, Philippines, South Korea,
  Japan, Taiwan, Singapore, UAE, Saudi Arabia, Turkey, Egypt, Morocco,
  Nigeria, South Africa, Kenya, Germany, Netherlands, Spain, Italy,
  France, UK, Mexico, Brazil, USA and more.
"""

import os
import re
import time
import requests
import pycountry
import xml.etree.ElementTree as ET
import streamlit as st
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from database import insert_event, clear_events
from filtering import filter_articles_batch

# ─── Disruption Keywords ──────────────────────────────────────────────────────
# TWO-LAYER FILTER:
# An article must contain at least one SUPPLY_CONTEXT word AND one DISRUPTION_TRIGGER.
# This prevents false matches like medical/sports articles that contain
# broad words like "shortage", "conflict", "disruption".

# Layer 1: Must mention a supply chain context
SUPPLY_CONTEXT_KEYWORDS = [
    # Logistics & shipping
    "port", "shipping", "freight", "cargo", "container", "vessel", "ship",
    "logistics", "supply chain", "warehouse", "customs", "import", "export",
    "dock", "terminal", "harbor", "harbour", "tanker", "bulk carrier",
    "rail freight", "air freight", "truck", "haulage", "last mile",
    # Trade & manufacturing
    "factory", "plant", "manufacturer", "production", "assembly",
    "semiconductor", "raw material", "commodit", "oil", "gas", "steel",
    "textile", "garment", "electronics", "automotive", "pharma",
    "trade", "tariff", "sanction", "embargo", "export ban", "import ban",
    "wto", "trade war", "trade route", "trade deal",
    # Energy & infrastructure
    "pipeline", "refinery", "power grid", "energy supply", "fuel supply",
    "canal", "suez", "panama canal", "strait", "bosphorus",
    # Economic
    "inflation", "supply shortage", "demand shock", "economic crisis",
    "currency crisis", "gdp", "recession",
]

# Layer 2: Must also mention an active disruption event
DISRUPTION_TRIGGER_KEYWORDS = [
    # Natural disasters
    "earthquake", "tsunami", "typhoon", "hurricane", "cyclone", "tornado",
    "flood", "flooding", "landslide", "volcanic eruption", "wildfire",
    "drought", "monsoon", "storm", "blizzard", "snowstorm", "heatwave",
    # Human disruptions
    "strike", "walkout", "labor dispute", "industrial action", "lockout",
    "protest", "riot", "coup", "civil unrest", "war", "conflict",
    "military", "missile", "attack", "explosion", "fire", "accident",
    "blockade", "closure", "shutdown", "disruption", "delay", "congestion",
    "shortage", "outage", "blackout", "rationing",
    # Trade/political
    "sanctions", "banned", "suspended", "halted", "seized", "impounded",
    "tariff hike", "trade restriction", "export control",
    # Infrastructure failures
    "collapsed", "grounded", "stranded", "detained", "diverted",
    "cancelled", "suspended service", "road closed", "bridge closed",
]

# Blocklist: articles containing ANY of these are rejected even if keywords match.
# These are topics that commonly false-match supply chain keywords.
NOISE_BLOCKLIST = [
    # Medical / health (contain words like "shortage", "supply", "disruption")
    "tourette", "syndrome", "autism", "adhd", "alzheimer", "cancer treatment",
    "drug shortage", "blood supply", "hospital supply", "medical shortage",
    "vaccine shortage", "insulin shortage", "medication shortage",
    "mental health", "psychiatric", "therapy session", "clinical trial",
    "patient shortage", "nurse shortage", "doctor shortage",
    # Entertainment / sports (contain "strike", "war", "conflict")
    "hollywood strike", "writers strike", "actors strike", "sag-aftra",
    "nba", "nfl", "fifa", "premier league", "champions league",
    "box office", "movie", "film festival", "celebrity", "bafta", "oscar",
    "grammy", "emmy", "taylor swift", "beyonce",
    # Politics unrelated to trade
    "election fraud", "abortion", "gun control", "immigration policy",
    "supreme court", "criminal trial", "lawsuit",
    # Cybersecurity (contains "attack", "disruption" but not supply chain)
    "ransomware", "cyber attack", "data breach", "hack", "phishing",
    # Generic finance (contains "shortage", "crisis" but not supply chain)  
    "housing shortage", "housing crisis", "rent", "mortgage",
    "stock market crash", "crypto", "bitcoin", "nft",
]

SUPPLY_CONTEXT_SET  = set(SUPPLY_CONTEXT_KEYWORDS)
DISRUPTION_SET      = set(DISRUPTION_TRIGGER_KEYWORDS)
BLOCKLIST_SET       = set(NOISE_BLOCKLIST)


def is_relevant(title: str, description: str = "") -> bool:
    """
    Two-layer relevance filter:
    1. Must NOT contain any blocklist terms
    2. Must contain at least one supply chain context word
    3. Must contain at least one disruption trigger word
    All three conditions required to pass.
    """
    text = f"{title} {description}".lower()

    # Reject if blocklisted topic
    if any(noise in text for noise in BLOCKLIST_SET):
        return False

    # Must have supply context AND disruption trigger
    has_context   = any(kw in text for kw in SUPPLY_CONTEXT_SET)
    has_disruption = any(kw in text for kw in DISRUPTION_SET)

    return has_context and has_disruption


# Legacy alias used elsewhere
KEYWORD_SET = SUPPLY_CONTEXT_SET | DISRUPTION_SET


# ─── Country Detection ────────────────────────────────────────────────────────

def _build_country_map() -> dict:
    mapping = {}
    for c in pycountry.countries:
        mapping[c.name.lower()] = c.name
        if hasattr(c, "common_name"):
            mapping[c.common_name.lower()] = c.name
    aliases = {
        "usa": "United States", "us": "United States", "america": "United States",
        "uk": "United Kingdom", "britain": "United Kingdom", "england": "United Kingdom",
        "uae": "United Arab Emirates", "dubai": "United Arab Emirates",
        "south korea": "Korea, Republic of",
        "taiwan": "Taiwan, Province of China", "hong kong": "Hong Kong",
        "vietnam": "Viet Nam", "russia": "Russian Federation",
        "iran": "Iran, Islamic Republic of",
    }
    mapping.update(aliases)
    return mapping

COUNTRY_MAP = _build_country_map()

PORT_CITY_MAP = {
    "shanghai": "China", "shenzhen": "China", "guangzhou": "China",
    "tianjin": "China", "qingdao": "China", "ningbo": "China",
    "ho chi minh": "Viet Nam", "haiphong": "Viet Nam", "hanoi": "Viet Nam",
    "jakarta": "Indonesia", "surabaya": "Indonesia",
    "mumbai": "India", "chennai": "India", "kolkata": "India", "nhava sheva": "India",
    "chittagong": "Bangladesh", "dhaka": "Bangladesh",
    "bangkok": "Thailand", "laem chabang": "Thailand",
    "port klang": "Malaysia", "kuala lumpur": "Malaysia", "penang": "Malaysia",
    "singapore": "Singapore",
    "manila": "Philippines", "cebu": "Philippines",
    "colombo": "Sri Lanka",
    "karachi": "Pakistan",
    "yangon": "Myanmar",
    "phnom penh": "Cambodia",
    "busan": "Korea, Republic of", "seoul": "Korea, Republic of",
    "tokyo": "Japan", "osaka": "Japan", "yokohama": "Japan",
    "kaohsiung": "Taiwan, Province of China", "taipei": "Taiwan, Province of China",
    "hong kong": "Hong Kong",
    "dubai": "United Arab Emirates", "abu dhabi": "United Arab Emirates", "jebel ali": "United Arab Emirates",
    "jeddah": "Saudi Arabia", "riyadh": "Saudi Arabia",
    "istanbul": "Turkey", "izmir": "Turkey",
    "rotterdam": "Netherlands", "amsterdam": "Netherlands",
    "hamburg": "Germany", "frankfurt": "Germany",
    "barcelona": "Spain", "valencia": "Spain", "madrid": "Spain",
    "genoa": "Italy", "naples": "Italy",
    "felixstowe": "United Kingdom", "london": "United Kingdom",
    "antwerp": "Belgium",
    "le havre": "France", "marseille": "France",
    "gdansk": "Poland",
    "piraeus": "Greece",
    "alexandria": "Egypt", "suez": "Egypt", "cairo": "Egypt",
    "casablanca": "Morocco", "tangier": "Morocco",
    "lagos": "Nigeria", "apapa": "Nigeria",
    "durban": "South Africa", "cape town": "South Africa",
    "mombasa": "Kenya",
    "manzanillo": "Mexico", "veracruz": "Mexico",
    "santos": "Brazil", "sao paulo": "Brazil",
    "long beach": "United States", "los angeles": "United States",
    "new york": "United States", "houston": "United States",
}


def detect_country_in_text(text: str) -> str:
    if not text:
        return "Unknown"
    text_lower = text.lower()
    for city, country in PORT_CITY_MAP.items():
        if city in text_lower:
            return country
    for name, official in COUNTRY_MAP.items():
        if len(name) > 3 and name in text_lower:
            return official
    return "Unknown"


def is_relevant(title: str, description: str = "") -> bool:
    text = f"{title} {description}".lower()
    return any(kw in text for kw in KEYWORD_SET)


def safe_insert(title, description, source, published_date, country, event_type,
                severity="medium", disruption_type="other", confidence=60, reasoning=""):
    """Insert a pre-filtered event — caller is responsible for filtering."""
    if not title:
        return
    insert_event(
        title=str(title)[:500],
        description=str(description)[:1000],
        source=source,
        published_date=published_date,
        country=country,
        event_type=event_type,
        severity=severity,
        disruption_likely="Yes"
    )


# ─── RSS Feed Definitions ─────────────────────────────────────────────────────

GLOBAL_RSS_FEEDS = [
    # Reuters
    ("Reuters Business", "https://feeds.reuters.com/reuters/businessNews", "Global"),
    ("Reuters World", "https://feeds.reuters.com/Reuters/worldNews", "Global"),
    ("Reuters Commodities", "https://feeds.reuters.com/reuters/commoditiesNews", "Global"),
    # BBC
    ("BBC World", "http://feeds.bbci.co.uk/news/world/rss.xml", "Global"),
    ("BBC Business", "http://feeds.bbci.co.uk/news/business/rss.xml", "Global"),
    ("BBC Asia", "http://feeds.bbci.co.uk/news/world/asia/rss.xml", "Asia"),
    # Al Jazeera
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "Global"),
    # Southeast Asia specialist
    ("Channel NewsAsia", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml", "Southeast Asia"),
    # South China Morning Post
    ("SCMP Business", "https://www.scmp.com/rss/91/feed", "China"),
    # India
    ("Hindu BusinessLine", "https://www.thehindubusinessline.com/feeder/default.rss", "India"),
    # Bangladesh
    ("Daily Star Bangladesh", "https://www.thedailystar.net/frontpage/rss.xml", "Bangladesh"),
    # Indonesia
    ("Jakarta Post", "https://www.thejakartapost.com/rss/id/frontpage.rss", "Indonesia"),
    # Thailand
    ("Bangkok Post", "https://www.bangkokpost.com/rss/data/topstories.xml", "Thailand"),
    # Japan/Asia
    ("Nikkei Asia", "https://asia.nikkei.com/rss/feed/nar", "Japan"),
    # South Korea
    ("Korea Herald", "https://www.koreaherald.com/common/rss_xml.php?ct=020", "Korea, Republic of"),
    # Vietnam
    ("Vietnam News", "https://vietnamnews.vn/rss/home.rss", "Viet Nam"),
    # Philippines
    ("Philippine Star", "https://www.philstar.com/rss/headlines", "Philippines"),
    # Pakistan
    ("Dawn Pakistan", "https://www.dawn.com/feeds/home", "Pakistan"),
    # Sri Lanka
    ("Daily FT Sri Lanka", "https://www.ft.lk/rss_feed.php", "Sri Lanka"),
    # Middle East
    ("Gulf News Business", "https://gulfnews.com/rss/business", "United Arab Emirates"),
    ("Arab News", "https://www.arabnews.com/rss.xml", "Saudi Arabia"),
    # Turkey
    ("Hurriyet Turkey", "https://www.hurriyetdailynews.com/rss.aspx", "Turkey"),
    # Africa
    ("Daily Nation Kenya", "https://nation.africa/kenya/rss.xml", "Kenya"),
    ("This Day Nigeria", "https://www.thisdaylive.com/index.php/feed/", "Nigeria"),
    # Europe
    ("DW Germany", "https://rss.dw.com/rdf/rss-en-bus", "Germany"),
    ("NL Times Netherlands", "https://nltimes.nl/rss.xml", "Netherlands"),
    # Americas
    ("Agencia Brasil", "https://agenciabrasil.ebc.com.br/rss/ultimasnoticias/feed.xml", "Brazil"),
    # Logistics specialists
    ("FreightWaves", "https://www.freightwaves.com/news/feed", "Global"),
    ("Supply Chain Dive", "https://www.supplychaindive.com/feeds/news/", "Global"),
    ("Journal of Commerce", "https://www.joc.com/rss.xml", "Global"),
]

# Google News — country + topic specific (most powerful for local news)
GOOGLE_NEWS_FEEDS = [
    ("GNews China Ports", "https://news.google.com/rss/search?q=china+port+supply+chain+disruption&hl=en&gl=CN&ceid=CN:en", "China"),
    ("GNews China Manufacturing", "https://news.google.com/rss/search?q=china+factory+shutdown+strike+flood&hl=en&gl=CN&ceid=CN:en", "China"),
    ("GNews Vietnam", "https://news.google.com/rss/search?q=vietnam+port+manufacturing+disruption+strike&hl=en&gl=VN&ceid=VN:en", "Viet Nam"),
    ("GNews Indonesia", "https://news.google.com/rss/search?q=indonesia+port+supply+chain+disruption&hl=en&gl=ID&ceid=ID:en", "Indonesia"),
    ("GNews India Ports", "https://news.google.com/rss/search?q=india+port+mumbai+chennai+strike+disruption&hl=en&gl=IN&ceid=IN:en", "India"),
    ("GNews Bangladesh", "https://news.google.com/rss/search?q=bangladesh+garment+factory+port+flood&hl=en&gl=BD&ceid=BD:en", "Bangladesh"),
    ("GNews Thailand", "https://news.google.com/rss/search?q=thailand+port+factory+disruption+flood&hl=en&gl=TH&ceid=TH:en", "Thailand"),
    ("GNews Malaysia", "https://news.google.com/rss/search?q=malaysia+port+klang+supply+chain&hl=en&gl=MY&ceid=MY:en", "Malaysia"),
    ("GNews Philippines", "https://news.google.com/rss/search?q=philippines+port+typhoon+supply+disruption&hl=en&gl=PH&ceid=PH:en", "Philippines"),
    ("GNews Pakistan", "https://news.google.com/rss/search?q=pakistan+karachi+port+disruption+flood&hl=en&gl=PK&ceid=PK:en", "Pakistan"),
    ("GNews Sri Lanka", "https://news.google.com/rss/search?q=sri+lanka+colombo+port+supply+disruption&hl=en&gl=LK&ceid=LK:en", "Sri Lanka"),
    ("GNews Myanmar", "https://news.google.com/rss/search?q=myanmar+yangon+factory+disruption+conflict&hl=en&gl=MM&ceid=MM:en", "Myanmar"),
    ("GNews Cambodia", "https://news.google.com/rss/search?q=cambodia+garment+factory+port+disruption&hl=en&gl=KH&ceid=KH:en", "Cambodia"),
    ("GNews South Korea", "https://news.google.com/rss/search?q=south+korea+busan+port+shipping+disruption&hl=en&gl=KR&ceid=KR:en", "Korea, Republic of"),
    ("GNews Japan", "https://news.google.com/rss/search?q=japan+port+earthquake+supply+chain+disruption&hl=en&gl=JP&ceid=JP:en", "Japan"),
    ("GNews Taiwan", "https://news.google.com/rss/search?q=taiwan+port+earthquake+semiconductor+disruption&hl=en&gl=TW&ceid=TW:en", "Taiwan, Province of China"),
    ("GNews Singapore", "https://news.google.com/rss/search?q=singapore+port+shipping+disruption&hl=en&gl=SG&ceid=SG:en", "Singapore"),
    ("GNews UAE", "https://news.google.com/rss/search?q=uae+dubai+jebel+ali+port+disruption&hl=en&gl=AE&ceid=AE:en", "United Arab Emirates"),
    ("GNews Saudi Arabia", "https://news.google.com/rss/search?q=saudi+arabia+jeddah+port+disruption+conflict&hl=en&gl=SA&ceid=SA:en", "Saudi Arabia"),
    ("GNews Turkey", "https://news.google.com/rss/search?q=turkey+istanbul+port+earthquake+disruption&hl=en&gl=TR&ceid=TR:en", "Turkey"),
    ("GNews Egypt Suez", "https://news.google.com/rss/search?q=egypt+suez+canal+port+disruption+blockage&hl=en&gl=EG&ceid=EG:en", "Egypt"),
    ("GNews Morocco", "https://news.google.com/rss/search?q=morocco+tanger+med+port+supply+disruption&hl=en&gl=MA&ceid=MA:en", "Morocco"),
    ("GNews Nigeria", "https://news.google.com/rss/search?q=nigeria+lagos+apapa+port+disruption&hl=en&gl=NG&ceid=NG:en", "Nigeria"),
    ("GNews South Africa", "https://news.google.com/rss/search?q=south+africa+durban+port+strike+disruption&hl=en&gl=ZA&ceid=ZA:en", "South Africa"),
    ("GNews Kenya", "https://news.google.com/rss/search?q=kenya+mombasa+port+disruption&hl=en&gl=KE&ceid=KE:en", "Kenya"),
    ("GNews Germany", "https://news.google.com/rss/search?q=germany+hamburg+port+supply+chain+disruption&hl=en&gl=DE&ceid=DE:en", "Germany"),
    ("GNews Netherlands", "https://news.google.com/rss/search?q=netherlands+rotterdam+port+disruption&hl=en&gl=NL&ceid=NL:en", "Netherlands"),
    ("GNews Spain", "https://news.google.com/rss/search?q=spain+barcelona+valencia+port+strike&hl=en&gl=ES&ceid=ES:en", "Spain"),
    ("GNews Italy", "https://news.google.com/rss/search?q=italy+genoa+port+disruption+strike&hl=en&gl=IT&ceid=IT:en", "Italy"),
    ("GNews France", "https://news.google.com/rss/search?q=france+le+havre+marseille+port+strike&hl=en&gl=FR&ceid=FR:en", "France"),
    ("GNews Poland", "https://news.google.com/rss/search?q=poland+gdansk+port+disruption+supply&hl=en&gl=PL&ceid=PL:en", "Poland"),
    ("GNews Mexico", "https://news.google.com/rss/search?q=mexico+port+manzanillo+veracruz+supply+disruption&hl=en&gl=MX&ceid=MX:en", "Mexico"),
    ("GNews Brazil", "https://news.google.com/rss/search?q=brazil+santos+port+supply+chain+disruption&hl=en&gl=BR&ceid=BR:en", "Brazil"),
    ("GNews USA Ports", "https://news.google.com/rss/search?q=us+port+supply+chain+disruption+strike&hl=en&gl=US&ceid=US:en", "United States"),
    # Global topic feeds
    ("GNews Global Supply Chain", "https://news.google.com/rss/search?q=supply+chain+disruption+port+strike+shortage&hl=en", "Global"),
    ("GNews Shipping Global", "https://news.google.com/rss/search?q=global+shipping+freight+disruption+delay+container&hl=en", "Global"),
    ("GNews Trade Wars", "https://news.google.com/rss/search?q=trade+war+tariff+sanctions+export+ban+supply&hl=en", "Global"),
]

ALL_FEEDS = GLOBAL_RSS_FEEDS + GOOGLE_NEWS_FEEDS


# ─── RSS Parser ───────────────────────────────────────────────────────────────

def fetch_rss_feed(source_name: str, url: str, default_country: str) -> list[dict]:
    """Fetch and parse a single RSS feed. Returns list of article dicts."""
    try:
        headers = {
            "User-Agent": "SupplierRiskDashboard/2.0",
            "Accept": "application/rss+xml, application/xml, text/xml",
        }
        response = requests.get(url, headers=headers, timeout=12)
        if response.status_code != 200:
            return []

        root = ET.fromstring(response.content)
        articles = []
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")

        for item in items[:20]:
            title_el = item.find("title") or item.find("{http://www.w3.org/2005/Atom}title")
            title = title_el.text.strip() if title_el is not None and title_el.text else ""

            desc_el = (item.find("description") or
                      item.find("{http://www.w3.org/2005/Atom}summary") or
                      item.find("{http://www.w3.org/2005/Atom}content"))
            description = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
            description = re.sub(r"<[^>]+>", "", description)[:1000]

            pub_el = (item.find("pubDate") or
                     item.find("{http://www.w3.org/2005/Atom}published") or
                     item.find("{http://www.w3.org/2005/Atom}updated"))
            published = pub_el.text.strip() if pub_el is not None and pub_el.text else datetime.utcnow().isoformat()

            if not title:
                continue

            detected = detect_country_in_text(f"{title} {description}")
            country = detected if detected != "Unknown" else default_country

            articles.append({
                "title": title,
                "description": description,
                "source": source_name,
                "published_date": published,
                "country": country,
                "event_type": "news",
            })

        return articles
    except Exception:
        return []


# ─── GDELT ────────────────────────────────────────────────────────────────────

def fetch_gdelt_events() -> list[dict]:
    """
    Fetch near real-time events from GDELT (updates every 15 minutes).
    Free, no API key, covers 150+ countries in 65 languages.
    """
    articles = []
    try:
        query = "supply chain OR port strike OR shipping disruption OR factory shutdown OR cargo shortage"
        url = (
            "https://api.gdeltproject.org/api/v2/doc/doc"
            f"?query={requests.utils.quote(query)}"
            "&mode=artlist&maxrecords=100&sort=DateDesc&format=json&timespan=1440"
        )
        response = requests.get(url, headers={"User-Agent": "SupplierRiskDashboard/2.0"}, timeout=20)
        if response.status_code != 200:
            return []

        for art in response.json().get("articles", []):
            title = art.get("title", "")
            source = art.get("domain", "GDELT")
            published = art.get("seendate", datetime.utcnow().isoformat())
            country_code = art.get("sourcecountry", "")

            try:
                c = pycountry.countries.get(alpha_2=country_code.upper()) if country_code else None
                country = c.name if c else detect_country_in_text(title)
            except Exception:
                country = detect_country_in_text(title)

            if not is_relevant(title):
                continue

            articles.append({
                "title": title[:500],
                "description": f"Via {source}",
                "source": f"GDELT/{source}",
                "published_date": published,
                "country": country if country != "Unknown" else "Global",
                "event_type": "news",
            })
    except Exception:
        pass
    return articles


# ─── NewsAPI ──────────────────────────────────────────────────────────────────

def fetch_newsapi_events(api_key: str) -> list[dict]:
    """Fetch from NewsAPI (100 req/day free)."""
    if not api_key:
        return []
    articles = []
    query = "port strike OR supply chain disruption OR shipping delay OR factory shutdown OR cargo shortage"
    params = {
        "q": query,
        "from": (datetime.utcnow() - timedelta(days=3)).strftime("%Y-%m-%d"),
        "sortBy": "publishedAt", "language": "en",
        "pageSize": 50, "apiKey": api_key,
    }
    try:
        r = requests.get("https://newsapi.org/v2/everything", params=params, timeout=15)
        if r.status_code != 200:
            return []
        for art in r.json().get("articles", []):
            title = art.get("title", "") or ""
            desc = art.get("description", "") or ""
            articles.append({
                "title": title[:500], "description": desc[:1000],
                "source": art.get("source", {}).get("name", "NewsAPI"),
                "published_date": art.get("publishedAt", ""),
                "country": detect_country_in_text(f"{title} {desc}"),
                "event_type": "news",
            })
    except Exception:
        pass
    return articles


# ─── Weather ─────────────────────────────────────────────────────────────────

PORT_WEATHER_LOCATIONS = {
    "China": [(31.2304, 121.4737, "Shanghai"), (22.5431, 114.0579, "Shenzhen")],
    "Viet Nam": [(10.8231, 106.6297, "Ho Chi Minh City"), (20.8449, 106.6881, "Haiphong")],
    "Indonesia": [(-6.2088, 106.8456, "Jakarta"), (-7.2575, 112.7521, "Surabaya")],
    "India": [(19.0760, 72.8777, "Mumbai"), (13.0827, 80.2707, "Chennai")],
    "Bangladesh": [(22.3569, 91.7832, "Chittagong")],
    "Thailand": [(13.7563, 100.5018, "Bangkok"), (13.0957, 100.8924, "Laem Chabang")],
    "Malaysia": [(3.1390, 101.6869, "Kuala Lumpur"), (5.4164, 100.3327, "Penang")],
    "Philippines": [(14.5995, 120.9842, "Manila")],
    "Pakistan": [(24.8607, 67.0011, "Karachi")],
    "Sri Lanka": [(6.9271, 79.8612, "Colombo")],
    "Myanmar": [(16.8661, 96.1951, "Yangon")],
    "Cambodia": [(11.5564, 104.9282, "Phnom Penh")],
    "Korea, Republic of": [(35.1796, 129.0756, "Busan")],
    "Japan": [(35.6762, 139.6503, "Tokyo"), (34.6937, 135.5023, "Osaka")],
    "Taiwan, Province of China": [(22.6273, 120.3014, "Kaohsiung")],
    "Singapore": [(1.3521, 103.8198, "Singapore")],
    "United Arab Emirates": [(25.2048, 55.2708, "Dubai")],
    "Saudi Arabia": [(21.4858, 39.1925, "Jeddah")],
    "Turkey": [(41.0082, 28.9784, "Istanbul")],
    "Egypt": [(30.0444, 31.2357, "Cairo"), (29.9668, 32.5498, "Suez")],
    "Morocco": [(33.5731, -7.5898, "Casablanca")],
    "Nigeria": [(6.5244, 3.3792, "Lagos")],
    "South Africa": [(-29.8587, 31.0218, "Durban")],
    "Kenya": [(-4.0435, 39.6682, "Mombasa")],
    "Germany": [(53.5753, 10.0153, "Hamburg")],
    "Netherlands": [(51.9244, 4.4777, "Rotterdam")],
    "Spain": [(41.3851, 2.1734, "Barcelona"), (39.4699, -0.3763, "Valencia")],
    "Mexico": [(19.1223, -104.3140, "Manzanillo")],
    "Brazil": [(-23.9619, -46.3042, "Santos")],
    "United States": [(33.7701, -118.1937, "Long Beach"), (40.7128, -74.0060, "New York")],
}


def fetch_weather_alerts(api_key: str, supplier_countries: list[str]) -> list[dict]:
    if not api_key:
        return []
    events = []
    checked = set()
    for country in supplier_countries:
        for lat, lon, city in PORT_WEATHER_LOCATIONS.get(country, []):
            key = f"{lat},{lon}"
            if key in checked:
                continue
            checked.add(key)
            try:
                r = requests.get(
                    "https://api.openweathermap.org/data/3.0/onecall",
                    params={"lat": lat, "lon": lon, "appid": api_key, "exclude": "minutely,hourly,daily,current"},
                    timeout=10
                )
                if r.status_code == 401:
                    break
                if r.status_code != 200:
                    continue
                for alert in r.json().get("alerts", []):
                    events.append({
                        "title": f"⚠️ {alert.get('event', 'Weather Alert')} — {city}",
                        "description": alert.get("description", "")[:1000],
                        "source": "OpenWeatherMap",
                        "published_date": datetime.utcfromtimestamp(alert.get("start", time.time())).isoformat(),
                        "country": country,
                        "event_type": "weather",
                        "severity": "high",
                    })
                time.sleep(0.2)
            except Exception:
                continue
    return events


# ─── Main Engine ──────────────────────────────────────────────────────────────

def fetch_all_rss_parallel() -> list[dict]:
    """Fetch all RSS feeds in parallel — 60+ feeds in ~10 seconds."""
    all_articles = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {
            executor.submit(fetch_rss_feed, name, url, country): (name, country)
            for name, url, country in ALL_FEEDS
        }
        for future in as_completed(futures, timeout=30):
            try:
                all_articles.extend(future.result())
            except Exception:
                continue
    return all_articles


def deduplicate(articles: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for art in articles:
        key = art["title"].lower().strip()[:80]
        if key not in seen:
            seen.add(key)
            unique.append(art)
    return unique


def refresh_all_events(
    news_api_key: str,
    weather_api_key: str,
    supplier_countries: list[str],
    openai_api_key: str = ""
) -> tuple[int, int, int, int, dict]:
    """
    Pull from ALL sources, run three-layer filter, store only validated events.
    Returns (rss_count, gdelt_count, newsapi_count, weather_count, filter_stats).
    """
    clear_events()

    # ── Gather raw articles from all sources ──────────────────────────────────
    all_news = []
    all_news.extend(fetch_all_rss_parallel())
    all_news.extend(fetch_gdelt_events())
    all_news.extend(fetch_newsapi_events(news_api_key))

    unique = deduplicate(all_news)

    # ── Run three-layer filter on all news articles ───────────────────────────
    use_llm = bool(openai_api_key)
    approved, stats = filter_articles_batch(unique, openai_api_key, use_llm)

    # ── Store approved articles ───────────────────────────────────────────────
    rss_count = gdelt_count = newsapi_count = 0
    for art in approved:
        safe_insert(
            title=art["title"],
            description=art.get("description", ""),
            source=art["source"],
            published_date=art.get("published_date", ""),
            country=art.get("country", "Unknown"),
            event_type=art.get("event_type", "news"),
            severity=art.get("severity", "medium"),
            disruption_type=art.get("disruption_type", "other"),
            confidence=art.get("confidence", 60),
            reasoning=art.get("reasoning", ""),
        )
        src = art["source"]
        if "GDELT" in src:           gdelt_count += 1
        elif src in {n for n, _, _ in GLOBAL_RSS_FEEDS + GOOGLE_NEWS_FEEDS}: rss_count += 1
        else:                        newsapi_count += 1

    # ── Weather alerts bypass news filter (they are always relevant) ──────────
    weather_events = fetch_weather_alerts(weather_api_key, supplier_countries)
    for evt in weather_events:
        insert_event(
            title=evt["title"], description=evt["description"],
            source=evt["source"], published_date=evt["published_date"],
            country=evt["country"], event_type="weather",
            severity="high", disruption_likely="Yes"
        )

    return rss_count, gdelt_count, newsapi_count, len(weather_events), stats


def should_auto_refresh(last_refresh_time: datetime | None, interval_minutes: int = 10) -> bool:
    """Returns True if it's time to refresh based on elapsed time."""
    if last_refresh_time is None:
        return True
    elapsed = (datetime.utcnow() - last_refresh_time).total_seconds() / 60
    return elapsed >= interval_minutes
