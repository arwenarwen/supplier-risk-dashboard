"""
global_news.py - True global news coverage for any country.

Three complementary sources that together cover every country on Earth:

1. GDELT GKG (Global Knowledge Graph)
   - 150+ countries, 65 languages
   - Updates every 15 minutes
   - Free, no API key
   - Weakness: quantity over quality, needs good filtering

2. Google News RSS — per-country targeted queries
   - Generated dynamically from supplier list
   - Searches in local country edition for better local coverage
   - Free, no API key

3. MediaStack / TheNewsAPI (optional paid)
   - Clean, filtered, structured
   - Covers 50+ countries with local sources
   - ~$9/month for meaningful volume

4. Wikipedia Current Events portal RSS
   - Curated by humans — high signal, global
   - Free

5. ReliefWeb (UN humanitarian)
   - Covers disasters, conflicts, crises in 200+ countries
   - Free, authoritative — specifically supply chain relevant

6. AP News, AFP wire feeds
   - Wire services cover every country, every day
   - Free RSS available

7. Country-specific RSS — dynamically generated for any country
   - Pattern: news.google.com with country-specific GL/CEID params
   - Works for virtually every country that has Google News coverage
"""

import re
import time
import requests
import pycountry
import xml.etree.ElementTree as ET
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed


# ─── Country Code Lookup ──────────────────────────────────────────────────────

def _get_country_code(country_name: str) -> tuple[str, str]:
    """
    Get ISO alpha-2 code and language code for a country.
    Returns (alpha2, lang_code) e.g. ("UA", "uk"), ("NG", "en"), ("CN", "zh-CN")
    """
    COUNTRY_LANG = {
        # Country code → (google_gl, google_ceid_lang, local_lang_code)
        "UA": ("UA", "uk"), "CN": ("CN", "zh-CN"), "RU": ("RU", "ru"),
        "JP": ("JP", "ja"), "KR": ("KR", "ko"), "AR": ("AR", "ar"),
        "FR": ("FR", "fr"), "DE": ("DE", "de"), "ES": ("ES", "es"),
        "IT": ("IT", "it"), "PT": ("PT", "pt"), "NL": ("NL", "nl"),
        "PL": ("PL", "pl"), "TR": ("TR", "tr"), "TH": ("TH", "th"),
        "ID": ("ID", "id"), "VN": ("VN", "vi"), "BD": ("BD", "bn"),
        "PK": ("PK", "ur"), "IN": ("IN", "en"), "NG": ("NG", "en"),
        "ZA": ("ZA", "en"), "KE": ("KE", "en"), "GH": ("GH", "en"),
        "EG": ("EG", "ar"), "MA": ("MA", "fr"), "TN": ("TN", "fr"),
        "SN": ("SN", "fr"), "CI": ("CI", "fr"), "CM": ("CM", "fr"),
        "ET": ("ET", "en"), "TZ": ("TZ", "en"), "UG": ("UG", "en"),
        "AO": ("AO", "pt"), "MZ": ("MZ", "pt"), "BR": ("BR", "pt"),
        "MX": ("MX", "es"), "CO": ("CO", "es"), "PE": ("PE", "es"),
        "CL": ("CL", "es"), "VE": ("VE", "es"), "EC": ("EC", "es"),
        "SA": ("SA", "ar"), "AE": ("AE", "ar"), "IQ": ("IQ", "ar"),
        "IR": ("IR", "fa"), "IL": ("IL", "he"), "LB": ("LB", "ar"),
        "PH": ("PH", "en"), "MM": ("MM", "my"), "KH": ("KH", "km"),
        "LK": ("LK", "en"), "NP": ("NP", "ne"), "AF": ("AF", "ps"),
        "KZ": ("KZ", "ru"), "UZ": ("UZ", "ru"), "GE": ("GE", "ka"),
        "AZ": ("AZ", "az"), "AM": ("AM", "hy"), "MD": ("MD", "ro"),
        "BY": ("BY", "ru"), "RS": ("RS", "sr"), "HR": ("HR", "hr"),
        "RO": ("RO", "ro"), "BG": ("BG", "bg"), "HU": ("HU", "hu"),
        "CZ": ("CZ", "cs"), "SK": ("SK", "sk"), "SI": ("SI", "sl"),
        "GR": ("GR", "el"), "SE": ("SE", "sv"), "NO": ("NO", "no"),
        "FI": ("FI", "fi"), "DK": ("DK", "da"), "BE": ("BE", "nl"),
        "AT": ("AT", "de"), "CH": ("CH", "de"),
    }
    try:
        c = pycountry.countries.lookup(country_name)
        a2 = c.alpha_2
        gl, lang = COUNTRY_LANG.get(a2, ("US", "en"))
        return a2, gl, lang
    except Exception:
        return "US", "US", "en"


# ─── Source 1: GDELT — upgraded query targeting ──────────────────────────────

def fetch_gdelt_for_suppliers(suppliers: list[dict]) -> list[dict]:
    """
    Run targeted GDELT queries for each unique country in the supplier list.
    GDELT covers 150+ countries and 65 languages — no country is excluded.
    """
    articles = []
    seen_countries = set()

    for s in suppliers:
        country = str(s.get("country", "") or "").strip()
        city    = str(s.get("city", "") or "").strip()
        if not country or country in seen_countries:
            continue
        seen_countries.add(country)

        # City-specific query first (most precise)
        queries = []
        if city:
            queries.append(
                f"{city} port OR shipping OR factory OR supply chain "
                f"OR strike OR flood OR earthquake OR war OR sanction"
            )
        # Country-level query
        queries.append(
            f"{country} port OR export OR shipping OR factory OR supply chain "
            f"OR strike OR disruption OR conflict OR sanction OR flood"
        )

        for q in queries:
            try:
                url = (
                    "https://api.gdeltproject.org/api/v2/doc/doc"
                    f"?query={requests.utils.quote(q)}"
                    "&mode=artlist&maxrecords=25&sort=DateDesc"
                    "&format=json&timespan=1440"
                )
                r = requests.get(url, headers={"User-Agent": "SupplierRiskDashboard/2.0"}, timeout=15)
                if r.status_code != 200:
                    continue

                for art in r.json().get("articles", []):
                    title        = art.get("title", "")
                    source       = art.get("domain", "GDELT")
                    published    = art.get("seendate", datetime.utcnow().isoformat())
                    country_code = art.get("sourcecountry", "")

                    try:
                        c    = pycountry.countries.get(alpha_2=country_code.upper()) if country_code else None
                        ctry = c.name if c else country
                    except Exception:
                        ctry = country

                    articles.append({
                        "title":         title[:500],
                        "description":   f"Via {source}",
                        "source":        f"GDELT/{source}",
                        "published_date": published,
                        "country":       ctry,
                        "event_type":    "news",
                    })

                time.sleep(0.3)  # be polite to GDELT

            except Exception:
                continue

    return articles


# ─── Source 2: Google News — any country dynamically ─────────────────────────

def build_google_news_url(city: str, country: str, query_suffix: str,
                           gl: str, lang: str) -> str:
    """Build a Google News RSS URL for any country/city combination."""
    location = f"{city}+{country}" if city else country
    location = location.replace(" ", "+")
    q = f"{location}+{query_suffix}"
    return (
        f"https://news.google.com/rss/search"
        f"?q={requests.utils.quote(q, safe='+')}"
        f"&hl={lang}&gl={gl}&ceid={gl}:{lang}"
    )


def build_dynamic_supplier_feeds(suppliers: list[dict]) -> list[tuple]:
    """
    Generate Google News RSS feeds for EVERY supplier city+country — any country.
    No hardcoded list. Works for Burkina Faso, Kazakhstan, Laos — anything.
    """
    feeds = []
    seen = set()

    QUERY_SUFFIXES = [
        "port disruption conflict strike flood war sanction",
        "supply chain factory shipping export disruption",
        "trade ban sanction embargo conflict military",
    ]

    for s in suppliers:
        city    = str(s.get("city", "") or "").strip()
        country = str(s.get("country", "") or "").strip()
        if not country:
            continue

        key = f"{city.lower()}|{country.lower()}"
        if key in seen:
            continue
        seen.add(key)

        _, gl, lang = _get_country_code(country)

        for suffix in QUERY_SUFFIXES:
            url  = build_google_news_url(city, country, suffix, gl, lang)
            name = f"GNews:{city or country}"
            feeds.append((name, url, country))

    return feeds


# ─── Source 3: Always-on global wire feeds ───────────────────────────────────

WIRE_FEEDS = [
    # UN / Humanitarian — covers every country in crises
    ("ReliefWeb", "https://reliefweb.int/updates/rss.xml", "Global"),
    ("UN News",   "https://news.un.org/feed/subscribe/en/news/topic/humanitarian-affairs/feed/rss.xml", "Global"),

    # Wire services — every country, every day
    ("AP News World",      "https://feeds.apnews.com/rss/apf-intlnews", "Global"),
    ("AP News Business",   "https://feeds.apnews.com/rss/apf-business", "Global"),
    ("AFP via Google",     "https://news.google.com/rss/search?q=AFP+supply+chain+port+disruption&hl=en", "Global"),
    ("Reuters World",      "https://feeds.reuters.com/Reuters/worldNews", "Global"),
    ("Reuters Business",   "https://feeds.reuters.com/reuters/businessNews", "Global"),
    ("Reuters Commodities","https://feeds.reuters.com/reuters/commoditiesNews", "Global"),

    # Wikipedia Current Events — human-curated, high signal
    ("Wikipedia Events",   "https://en.wikipedia.org/w/index.php?title=Portal:Current_events&action=history&feed=atom", "Global"),

    # Global logistics specialists
    ("FreightWaves",       "https://www.freightwaves.com/news/feed", "Global"),
    ("Supply Chain Dive",  "https://www.supplychaindive.com/feeds/news/", "Global"),
    ("Lloyd's List",       "https://lloydslist.maritimeintelligence.informa.com/rss", "Global"),
    ("TradeWinds",         "https://www.tradewindsnews.com/rss", "Global"),
    ("Journal of Commerce","https://www.joc.com/rss.xml", "Global"),
    ("Splash247 Shipping", "https://splash247.com/feed/", "Global"),

    # BBC + Al Jazeera — strong global south coverage
    ("BBC World",    "http://feeds.bbci.co.uk/news/world/rss.xml", "Global"),
    ("BBC Business", "http://feeds.bbci.co.uk/news/business/rss.xml", "Global"),
    ("Al Jazeera",   "https://www.aljazeera.com/xml/rss/all.xml", "Global"),
    ("Al Jazeera Economy", "https://www.aljazeera.com/xml/rss/economy.xml", "Global"),
    ("DW News",      "https://rss.dw.com/rdf/rss-en-all", "Global"),

    # Regional — covers areas often missed
    ("Africa News",      "https://www.africanews.com/feed/", "Africa"),
    ("AllAfrica",        "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf", "Africa"),
    ("The East African", "https://www.theeastafrican.co.ke/tea/rss", "East Africa"),
    ("APA News Africa",  "https://apanews.net/feed/", "Africa"),
    ("Latin America Reports", "https://news.google.com/rss/search?q=latin+america+port+supply+chain+disruption&hl=en", "Latin America"),
    ("Middle East Eye",  "https://www.middleeasteye.net/rss", "Middle East"),
    ("The National UAE", "https://www.thenationalnews.com/rss/", "Middle East"),
    ("Asia Times",       "https://asiatimes.com/feed/", "Asia"),
    ("Nikkei Asia",      "https://asia.nikkei.com/rss/feed/nar", "Asia"),
    ("South China Morning Post", "https://www.scmp.com/rss/91/feed", "Asia"),
    ("Channel NewsAsia", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml", "Southeast Asia"),
    ("Bangkok Post",     "https://www.bangkokpost.com/rss/data/topstories.xml", "Thailand"),
    ("Dawn Pakistan",    "https://www.dawn.com/feeds/home", "Pakistan"),
    ("The Hindu",        "https://www.thehindu.com/news/national/?service=rss", "India"),
    ("Hindu BusinessLine","https://www.thehindubusinessline.com/feeder/default.rss", "India"),
    ("Times of India Business", "https://timesofindia.indiatimes.com/rssfeeds/1898055.cms", "India"),
    ("Daily Star Bangladesh", "https://www.thedailystar.net/frontpage/rss.xml", "Bangladesh"),
    ("Jakarta Post",     "https://www.thejakartapost.com/rss/id/frontpage.rss", "Indonesia"),
    ("Korea Herald",     "https://www.koreaherald.com/common/rss_xml.php?ct=020", "Korea"),
    ("Vietnam News",     "https://vietnamnews.vn/rss/home.rss", "Vietnam"),
    ("Philippine Star",  "https://www.philstar.com/rss/headlines", "Philippines"),
    ("Gulf News Business","https://gulfnews.com/rss/business", "UAE"),
    ("Arab News",        "https://www.arabnews.com/rss.xml", "Saudi Arabia"),
    ("Hurriyet Daily",   "https://www.hurriyetdailynews.com/rss.aspx", "Turkey"),
    ("Daily Nation Kenya","https://nation.africa/kenya/rss.xml", "Kenya"),
    ("Business Day SA",  "https://businesslive.co.za/rss/bd/", "South Africa"),
    ("This Day Nigeria", "https://www.thisdaylive.com/index.php/feed/", "Nigeria"),
    ("Business Ghana",   "https://businessghana.com/site/news/rss", "Ghana"),
    ("The East African", "https://www.theeastafrican.co.ke/tea/rss", "East Africa"),
    ("NL Times",         "https://nltimes.nl/rss.xml", "Netherlands"),
    ("DW Germany",       "https://rss.dw.com/rdf/rss-en-bus", "Germany"),
    ("Agencia Brasil",   "https://agenciabrasil.ebc.com.br/rss/ultimasnoticias/feed.xml", "Brazil"),
    ("El Financiero Mexico","https://www.elfinanciero.com.mx/rss/feed.xml", "Mexico"),
]


# ─── RSS Parser ───────────────────────────────────────────────────────────────

def _parse_rss(source_name: str, url: str, default_country: str) -> list[dict]:
    """Fetch and parse a single RSS feed."""
    try:
        headers = {
            "User-Agent": "SupplierRiskDashboard/2.0",
            "Accept": "application/rss+xml, application/xml, text/xml",
        }
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code != 200:
            return []

        root  = ET.fromstring(r.content)
        items = (root.findall(".//item") or
                 root.findall(".//{http://www.w3.org/2005/Atom}entry"))

        articles = []
        for item in items[:20]:
            title_el  = item.find("title") or item.find("{http://www.w3.org/2005/Atom}title")
            title     = title_el.text.strip() if title_el is not None and title_el.text else ""

            desc_el   = (item.find("description") or
                         item.find("{http://www.w3.org/2005/Atom}summary") or
                         item.find("{http://www.w3.org/2005/Atom}content"))
            desc      = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
            desc      = re.sub(r"<[^>]+>", "", desc)[:1000]

            pub_el    = (item.find("pubDate") or
                         item.find("{http://www.w3.org/2005/Atom}published") or
                         item.find("{http://www.w3.org/2005/Atom}updated"))
            published = pub_el.text.strip() if pub_el is not None and pub_el.text else datetime.utcnow().isoformat()

            if not title:
                continue

            articles.append({
                "title":          title,
                "description":    desc,
                "source":         source_name,
                "published_date": published,
                "country":        default_country,
                "event_type":     "news",
            })

        return articles
    except Exception:
        return []


def fetch_all_global_parallel(supplier_feeds: list[tuple] = None) -> list[dict]:
    """
    Fetch all wire feeds + supplier-specific feeds in parallel.
    Returns raw article list before filtering.
    """
    all_feeds = WIRE_FEEDS + (supplier_feeds or [])
    articles  = []

    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = {
            executor.submit(_parse_rss, name, url, country): (name, country)
            for name, url, country in all_feeds
        }
        for future in as_completed(futures, timeout=40):
            try:
                articles.extend(future.result())
            except Exception:
                continue

    return articles
