"""
scoring.py - Geo-precise risk scoring engine v3.

Key upgrade: city-level proximity scoring using haversine distance.
- Events are matched to supplier cities, not just countries
- A snowstorm in Boston does NOT affect a supplier in Los Angeles
- Scoring zones:
    < 50 miles  → Direct impact zone     (full score)
    50-300 mi   → Regional impact zone   (60% score)
    300-1000 mi → Same country, far away (25% score)
    > 1000 mi   → Different region       (5% score)
    Different country, no geo match      → 2% score (near zero)

Events are also geocoded by extracting city mentions from their text,
using a large built-in city→(lat,lon) lookup — no API calls needed.
"""

import math
import re
import pandas as pd
try:
    import pycountry
except ImportError:
    pycountry = None
try:
    import pycountry_convert as pc
except ImportError:
    pc = None
from datetime import datetime, timezone
from database import get_all_events, update_supplier_risk, get_all_suppliers
from city_geocoder import geocode_city_fast, geocode_city

# ─── Thresholds ───────────────────────────────────────────────────────────────

HIGH_RISK_THRESHOLD   = 60
MEDIUM_RISK_THRESHOLD = 26
MAX_POINTS_PER_EVENT  = 25
MAX_EVENTS_COUNTED    = 5

# ─── Distance Scoring Zones (miles) ──────────────────────────────────────────

ZONE_DIRECT   = 50     # Full impact
ZONE_REGIONAL = 300    # Same metro region
ZONE_NATIONAL = 1000   # Same country but far

def distance_multiplier(miles: float) -> float:
    """Convert distance in miles to a scoring multiplier."""
    if miles <= ZONE_DIRECT:
        return 1.0       # Same city / immediate area
    elif miles <= ZONE_REGIONAL:
        return 0.6       # Same region (e.g. east coast US)
    elif miles <= ZONE_NATIONAL:
        return 0.25      # Same country, different region
    elif miles <= 3000:
        return 0.08      # Neighboring country / same continent
    else:
        return 0.02      # Different continent entirely

# ─── Signal Keywords ──────────────────────────────────────────────────────────

HIGH_SIGNAL_KEYWORDS = [
    "port closure", "port closed", "port strike", "dock strike",
    "earthquake", "tsunami", "typhoon", "hurricane", "cyclone",
    "factory fire", "explosion", "factory shutdown", "plant shutdown",
    "sanctions", "export ban", "import ban", "trade blockade",
    "war", "military", "invasion", "blockade",
    "canal blocked", "suez", "panama canal",
    "power outage", "blackout", "energy crisis",
    "flood", "severe flooding", "landslide",
]

MEDIUM_SIGNAL_KEYWORDS = [
    "strike", "walkout", "protest", "labor dispute",
    "shortage", "supply shortage",
    "delay", "port delay", "shipping delay",
    "disruption", "supply disruption",
    "tariff", "trade war",
    "storm", "typhoon warning", "snowstorm", "blizzard",
]

SEVERITY_MULTIPLIER = {"high": 1.0, "medium": 0.5, "low": 0.2}

# ─── City Coordinate Lookup ───────────────────────────────────────────────────
# Large built-in dict: city name (lowercase) → (lat, lon)
# Covers major sourcing cities, port cities, and regional centers worldwide

CITY_COORDS = {
    # USA
    "los angeles": (34.0522, -118.2437), "long beach": (33.7701, -118.1937),
    "new york": (40.7128, -74.0060), "new york city": (40.7128, -74.0060),
    "boston": (42.3601, -71.0589), "chicago": (41.8781, -87.6298),
    "houston": (29.7604, -95.3698), "miami": (25.7617, -80.1918),
    "seattle": (47.6062, -122.3321), "san francisco": (37.7749, -122.4194),
    "detroit": (42.3314, -83.0458), "atlanta": (33.7490, -84.3880),
    "dallas": (32.7767, -96.7970), "phoenix": (33.4484, -112.0740),
    "portland": (45.5051, -122.6750), "savannah": (32.0835, -81.0998),
    "baltimore": (39.2904, -76.6122), "norfolk": (36.8508, -76.2859),
    "new jersey": (40.0583, -74.4057), "newark": (40.7357, -74.1724),
    "charleston": (32.7765, -79.9311), "jacksonville": (30.3322, -81.6557),
    "memphis": (35.1495, -90.0490), "new orleans": (29.9511, -90.0715),
    "minneapolis": (44.9778, -93.2650), "kansas city": (39.0997, -94.5786),
    "denver": (39.7392, -104.9903), "salt lake city": (40.7608, -111.8910),
    "las vegas": (36.1699, -115.1398), "san diego": (32.7157, -117.1611),
    "washington dc": (38.9072, -77.0369), "washington": (38.9072, -77.0369),
    "philadelphia": (39.9526, -75.1652), "pittsburgh": (40.4406, -79.9959),
    "cleveland": (41.4993, -81.6944), "cincinnati": (39.1031, -84.5120),
    "st louis": (38.6270, -90.1994), "nashville": (36.1627, -86.7816),

    # China
    "shanghai": (31.2304, 121.4737), "beijing": (39.9042, 116.4074),
    "shenzhen": (22.5431, 114.0579), "guangzhou": (23.1291, 113.2644),
    "tianjin": (39.3434, 117.3616), "qingdao": (36.0671, 120.3826),
    "ningbo": (29.8683, 121.5440), "wuhan": (30.5928, 114.3055),
    "chengdu": (30.5728, 104.0668), "xian": (34.3416, 108.9398),
    "dalian": (38.9140, 121.6147), "xiamen": (24.4798, 118.0894),
    "nanjing": (32.0603, 118.7969), "hangzhou": (30.2741, 120.1551),
    "suzhou": (31.2990, 120.5853), "dongguan": (23.0207, 113.7518),
    "foshan": (23.0219, 113.1215), "zhengzhou": (34.7466, 113.6253),
    "hong kong": (22.3193, 114.1694), "macau": (22.1987, 113.5439),

    # Vietnam
    "ho chi minh city": (10.8231, 106.6297), "ho chi minh": (10.8231, 106.6297),
    "saigon": (10.8231, 106.6297), "hanoi": (21.0285, 105.8542),
    "haiphong": (20.8449, 106.6881), "da nang": (16.0544, 108.2022),
    "bien hoa": (10.9574, 106.8426), "can tho": (10.0452, 105.7469),

    # Indonesia
    "jakarta": (-6.2088, 106.8456), "surabaya": (-7.2575, 112.7521),
    "bandung": (-6.9175, 107.6191), "medan": (3.5952, 98.6722),
    "batam": (1.0456, 104.0305), "semarang": (-6.9932, 110.4203),

    # India
    "mumbai": (19.0760, 72.8777), "delhi": (28.7041, 77.1025),
    "new delhi": (28.6139, 77.2090), "chennai": (13.0827, 80.2707),
    "kolkata": (22.5726, 88.3639), "bangalore": (12.9716, 77.5946),
    "bengaluru": (12.9716, 77.5946), "hyderabad": (17.3850, 78.4867),
    "pune": (18.5204, 73.8567), "ahmedabad": (23.0225, 72.5714),
    "surat": (21.1702, 72.8311), "nhava sheva": (18.9500, 72.9500),
    "jnpt": (18.9500, 72.9500), "kochi": (9.9312, 76.2673),

    # Bangladesh
    "dhaka": (23.8103, 90.4125), "chittagong": (22.3569, 91.7832),
    "gazipur": (23.9999, 90.4203), "narayanganj": (23.6238, 90.4994),

    # Thailand
    "bangkok": (13.7563, 100.5018), "laem chabang": (13.0957, 100.8924),
    "chiang mai": (18.7883, 98.9853), "rayong": (12.6814, 101.2816),

    # Malaysia
    "kuala lumpur": (3.1390, 101.6869), "port klang": (3.0000, 101.3833),
    "penang": (5.4164, 100.3327), "johor bahru": (1.4927, 103.7414),
    "iskandar": (1.4655, 103.7578),

    # Philippines
    "manila": (14.5995, 120.9842), "cebu": (10.3157, 123.8854),
    "davao": (7.1907, 125.4553), "clark": (15.1800, 120.5600),

    # Pakistan
    "karachi": (24.8607, 67.0011), "lahore": (31.5204, 74.3587),
    "faisalabad": (31.4504, 73.1350), "islamabad": (33.6844, 73.0479),
    "sialkot": (32.4945, 74.5229),

    # Sri Lanka
    "colombo": (6.9271, 79.8612), "kandy": (7.2906, 80.6337),

    # Myanmar
    "yangon": (16.8661, 96.1951), "mandalay": (21.9588, 96.0891),

    # Cambodia
    "phnom penh": (11.5564, 104.9282), "sihanoukville": (10.6278, 103.5228),

    # South Korea
    "busan": (35.1796, 129.0756), "seoul": (37.5665, 126.9780),
    "incheon": (37.4563, 126.7052), "ulsan": (35.5384, 129.3114),

    # Japan
    "tokyo": (35.6762, 139.6503), "osaka": (34.6937, 135.5023),
    "yokohama": (35.4437, 139.6380), "nagoya": (35.1815, 136.9066),
    "kobe": (34.6901, 135.1955), "fukuoka": (33.5904, 130.4017),
    "sendai": (38.2688, 140.8721),

    # Taiwan
    "taipei": (25.0330, 121.5654), "kaohsiung": (22.6273, 120.3014),
    "taichung": (24.1477, 120.6736), "tainan": (22.9998, 120.2269),

    # Singapore
    "singapore": (1.3521, 103.8198),

    # UAE
    "dubai": (25.2048, 55.2708), "abu dhabi": (24.4539, 54.3773),
    "sharjah": (25.3573, 55.4033), "jebel ali": (24.9964, 55.0542),

    # Saudi Arabia
    "jeddah": (21.4858, 39.1925), "riyadh": (24.6877, 46.7219),
    "dammam": (26.4207, 50.0888),

    # Turkey
    "istanbul": (41.0082, 28.9784), "izmir": (38.4192, 27.1287),
    "ankara": (39.9334, 32.8597), "bursa": (40.1885, 29.0610),
    "mersin": (36.8000, 34.6333), "adana": (37.0000, 35.3213),

    # Egypt
    "cairo": (30.0444, 31.2357), "suez": (29.9668, 32.5498),
    "alexandria": (31.2001, 29.9187), "port said": (31.2565, 32.2841),

    # Morocco
    "casablanca": (33.5731, -7.5898), "tangier": (35.7595, -5.8340),
    "rabat": (34.0209, -6.8416),

    # Nigeria
    "lagos": (6.5244, 3.3792), "apapa": (6.4490, 3.3636),
    "abuja": (9.0765, 7.3986), "kano": (12.0022, 8.5920),

    # South Africa
    "durban": (-29.8587, 31.0218), "cape town": (-33.9249, 18.4241),
    "johannesburg": (-26.2041, 28.0473), "port elizabeth": (-33.9608, 25.6022),

    # Kenya
    "mombasa": (-4.0435, 39.6682), "nairobi": (-1.2921, 36.8219),

    # Germany
    "hamburg": (53.5753, 10.0153), "frankfurt": (50.1109, 8.6821),
    "munich": (48.1351, 11.5820), "berlin": (52.5200, 13.4050),
    "bremen": (53.0793, 8.8017), "dusseldorf": (51.2217, 6.7762),
    "cologne": (50.9333, 6.9500), "stuttgart": (48.7758, 9.1829),

    # Netherlands
    "rotterdam": (51.9244, 4.4777), "amsterdam": (52.3676, 4.9041),
    "eindhoven": (51.4416, 5.4697),

    # Spain
    "barcelona": (41.3851, 2.1734), "valencia": (39.4699, -0.3763),
    "madrid": (40.4168, -3.7038), "bilbao": (43.2630, -2.9350),
    "algeciras": (36.1408, -5.4536),

    # Italy
    "genoa": (44.4056, 8.9463), "naples": (40.8518, 14.2681),
    "milan": (45.4654, 9.1859), "rome": (41.9028, 12.4964),
    "trieste": (45.6495, 13.7768), "venice": (45.4408, 12.3155),

    # France
    "le havre": (49.4938, 0.1077), "marseille": (43.2965, 5.3698),
    "paris": (48.8566, 2.3522), "lyon": (45.7640, 4.8357),

    # UK
    "london": (51.5074, -0.1278), "felixstowe": (51.9600, 1.3500),
    "southampton": (50.9097, -1.4044), "liverpool": (53.4084, -2.9916),
    "bristol": (51.4545, -2.5879), "birmingham": (52.4862, -1.8904),

    # Belgium
    "antwerp": (51.2194, 4.4025), "brussels": (50.8503, 4.3517),
    "ghent": (51.0543, 3.7174),

    # Poland
    "gdansk": (54.3520, 18.6466), "warsaw": (52.2297, 21.0122),

    # Greece
    "piraeus": (37.9422, 23.6475), "athens": (37.9838, 23.7275),
    "thessaloniki": (40.6401, 22.9444),

    # Mexico
    "manzanillo": (19.1223, -104.3140), "veracruz": (19.1738, -96.1342),
    "mexico city": (19.4326, -99.1332), "guadalajara": (20.6597, -103.3496),
    "monterrey": (25.6866, -100.3161), "tijuana": (32.5149, -117.0382),

    # Brazil
    "santos": (-23.9619, -46.3042), "sao paulo": (-23.5505, -46.6333),
    # Ukraine & Black Sea region
    "kyiv": (50.4501, 30.5234), "kiev": (50.4501, 30.5234),
    "kharkiv": (49.9935, 36.2304), "odesa": (46.4825, 30.7233),
    "odessa": (46.4825, 30.7233), "dnipro": (48.4647, 35.0462),
    "lviv": (49.8397, 24.0297), "zaporizhzhia": (47.8388, 35.1396),
    "mariupol": (47.0951, 37.5397), "mykolaiv": (46.9750, 31.9946),
    "chornomorsk": (46.3025, 30.6558), "pivdennyi": (46.6000, 31.2000),
    # Russia
    "moscow": (55.7558, 37.6173), "saint petersburg": (59.9343, 30.3351),
    "novorossiysk": (44.7236, 37.7688), "vladivostok": (43.1332, 131.9113),
    # Moldova / Romania / Black Sea
    "constanta": (44.1598, 28.6348), "bucharest": (44.4268, 26.1025),
    "chisinau": (47.0105, 28.8638),
    # Central Asia (common sourcing region)
    "almaty": (43.2220, 76.8512), "tashkent": (41.2995, 69.2401),
    "baku": (40.4093, 49.8671), "tbilisi": (41.6938, 44.8015),
    # Israel / Middle East conflict zone
    "tel aviv": (32.0853, 34.7818), "haifa": (32.8191, 34.9983),
    "ashdod": (31.7972, 34.6471), "beirut": (33.8938, 35.5018),
    "amman": (31.9454, 35.9284), "baghdad": (33.3152, 44.3661),
    "tehran": (35.6892, 51.3890), "muscat": (23.5880, 58.3829),

    "rio de janeiro": (-22.9068, -43.1729), "belem": (-1.4558, -48.5039),
    "manaus": (-3.1190, -60.0217), "fortaleza": (-3.7172, -38.5434),

    # Canada
    "vancouver": (49.2827, -123.1207), "toronto": (43.6532, -79.3832),
    "montreal": (45.5017, -73.5673), "halifax": (44.6488, -63.5752),
    "prince rupert": (54.3150, -130.3208),
}


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in miles between two lat/lon points."""
    R = 3958.8  # Earth radius in miles
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def extract_city_coords(text: str) -> tuple[float, float] | None:
    """
    Scan article text for city names and return the first match's coordinates.
    Uses the dynamic geocoding cache — covers any city in any country.
    Longer city names checked first to avoid partial matches.
    """
    if not text:
        return None
    text_lower = text.lower()

    # First check our static coords dict for instant lookup (most common cities)
    for city in sorted(CITY_COORDS.keys(), key=len, reverse=True):
        if city in text_lower:
            return CITY_COORDS[city]

    # Then check the dynamic cache (cities seen in previous geocoding runs)
    # Extract candidate city names using simple NLP: capitalized words 3+ chars
    import re as _re
    candidates = _re.findall(r'\b([A-Z][a-z]{2,}(?:\s[A-Z][a-z]{2,})?)\b', text)
    for candidate in candidates:
        if len(candidate) < 3:
            continue
        # Skip common non-city words
        skip = {"The", "This", "That", "With", "From", "Into", "Over",
                "After", "Before", "During", "Monday", "Tuesday", "Wednesday",
                "Thursday", "Friday", "Saturday", "Sunday", "January",
                "February", "March", "April", "June", "July", "August",
                "September", "October", "November", "December",
                "Reuters", "Bloomberg", "Associated", "Press", "News"}
        if candidate in skip:
            continue
        coords = geocode_city_fast(candidate)
        if coords:
            return coords

    return None


# ─── Time Window: Pure Forward-Looking ──────────────────────────────────────
#
# Core philosophy: score what WILL affect your supplier, not what already did.
#
# THREE signal types drive scoring:
#
#  1. FORECAST signals (2.0×) — explicit future warnings, still time to act
#     "typhoon warning issued", "strike vote scheduled", "sanctions expected"
#
#  2. ACTIVE/PERSISTENT signals — started recently, future impact still real
#     <48h: 1.0×  |  2–7 days: 0.7× (persistent) / 0.3× (fast)
#     7–30 days: 0.5× (persistent only, e.g. war/sanctions) / 0.0×
#     >30 days: 0.0× always
#
#  3. SEASONAL/SCHEDULED signals — injected from calendar, no article needed
#     Typhoon season, monsoon, election windows, labor cycles, harvest disruption
#     Represented as synthetic events from SEASONAL_RISK_CALENDAR

HIGH_SIGNAL_PERSISTENT = {
    "war", "conflict", "invasion", "occupation", "sanctions", "embargo",
    "blockade", "trade war", "export ban", "import ban", "military",
    "armed conflict", "civil war", "coup", "trade restriction",
    "tariff", "levy", "duty hike", "trade barrier",
}

FORECAST_SIGNALS = {
    "warning issued", "watch issued", "alert issued", "advisory issued",
    "tropical storm warning", "typhoon warning", "hurricane warning",
    "cyclone warning", "flood warning", "storm surge warning",
    "forecast", "expected to hit", "predicted to", "projected to",
    "approaching", "will hit", "set to make landfall", "tracking toward",
    "heading toward", "moving toward", "on course for",
    "imminent", "impending", "expected to impose", "sanctions expected",
    "proposed tariff", "new sanctions", "planned sanctions",
    "threatened with", "considering sanctions", "mulling tariffs",
    "upcoming strike", "planned strike", "announced strike", "strike vote",
    "strike ballot", "walkout planned", "workers threatening",
    "union negotiation", "contract expiry", "labor talks",
    "scheduled closure", "planned maintenance", "port closure planned",
    "in the next", "over the coming", "within days", "within weeks",
    "risk of", "threat of", "danger of", "possibility of",
    "election risk", "political uncertainty ahead",
}


def _parse_published(date_str: str):
    """Parse a published date string into a UTC-aware datetime."""
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
        "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT",
        "%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
    ):
        try:
            pub = datetime.strptime(date_str[:26].strip(), fmt)
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            return pub
        except Exception:
            continue
    return None


def is_forecast(title: str, description: str = "") -> bool:
    """Detect forward-looking articles warning of upcoming disruption."""
    text = f"{title} {description}".lower()
    return any(sig in text for sig in FORECAST_SIGNALS)


def recency_weight(published_date_str: str, title: str = "", description: str = "") -> float:
    """
    Pure forward-looking time weight.

    FORECAST article (warning/alert):      2.0  — Future threat, act NOW
    Active / breaking (<48h):              1.0  — Ongoing situation
    Unfolding (2–7 days):
      Persistent (war/sanctions):          0.7  — Future impact still real
      Fast-resolving (weather/strike):     0.3  — Mostly over
    Sustained (7–30 days):
      Persistent only:                     0.5  — Sanctions/war window open
      Fast-resolving:                      0.0  — Ignored
    >30 days:                              0.0  — Always ignored
    """
    if not published_date_str:
        return 0.5

    if is_forecast(title, description):
        return 2.0

    pub = _parse_published(published_date_str)
    if pub is None:
        return 0.5

    age_hours  = (datetime.now(timezone.utc) - pub).total_seconds() / 3600
    if age_hours < 0:
        return 2.0  # Future-dated / scheduled event

    text       = f"{title} {description}".lower()
    persistent = any(kw in text for kw in HIGH_SIGNAL_PERSISTENT)

    if age_hours <= 48:
        return 1.0
    elif age_hours <= 168:
        return 0.7 if persistent else 0.3
    elif age_hours <= 720:
        return 0.5 if persistent else 0.0
    else:
        return 0.0


# ─── Seasonal & Scheduled Forward Risk Calendar ───────────────────────────────
# Known future risks injected directly into scoring — no news article required.
# A Manila supplier scores elevated in August even before any typhoon hits.

SEASONAL_RISK_CALENDAR = [
    # ── Typhoon / Cyclone ─────────────────────────────────────────────────────
    {"countries": ["Philippines","Taiwan","Japan","China","Vietnam","South Korea"],
     "months": [5,6,7,8,9,10,11], "peak_months": [8,9,10],
     "signal": "high", "type": "🌀 Typhoon season — Western Pacific",
     "weight": 0.7, "horizon_days": 30},
    {"countries": ["Bangladesh","India","Myanmar","Sri Lanka","Thailand","Malaysia"],
     "months": [4,5,10,11], "peak_months": [4,5,10,11],
     "signal": "high", "type": "🌀 Cyclone season — Bay of Bengal",
     "weight": 0.65, "horizon_days": 30},
    {"countries": ["Pakistan","India","Bangladesh","Sri Lanka"],
     "months": [6,7,8,9], "peak_months": [7,8],
     "signal": "medium", "type": "🌧 Monsoon season — South Asia",
     "weight": 0.5, "horizon_days": 30},
    {"countries": ["United States","Mexico","Cuba","Haiti","Dominican Republic"],
     "cities": ["houston","miami","new orleans","savannah","charleston",
                "jacksonville","manzanillo","veracruz"],
     "months": [6,7,8,9,10,11], "peak_months": [8,9,10],
     "signal": "high", "type": "🌀 Atlantic hurricane season",
     "weight": 0.6, "horizon_days": 30},
    {"countries": ["Indonesia","Philippines","Fiji","Papua New Guinea",
                   "Australia","Madagascar","Mozambique"],
     "months": [11,12,1,2,3,4], "peak_months": [1,2,3],
     "signal": "medium", "type": "🌀 Southern hemisphere cyclone season",
     "weight": 0.5, "horizon_days": 30},
    # ── Flooding ──────────────────────────────────────────────────────────────
    {"countries": ["China","Vietnam","Thailand","Myanmar","Cambodia","Laos"],
     "months": [6,7,8,9], "peak_months": [7,8],
     "signal": "medium", "type": "🌊 Summer flooding — SE/East Asia",
     "weight": 0.45, "horizon_days": 30},
    {"countries": ["Spain","Italy","France","Greece"],
     "cities": ["valencia","barcelona","genoa","marseille"],
     "months": [10,11], "peak_months": [10,11],
     "signal": "medium", "type": "🌊 DANA flash flood season — Mediterranean",
     "weight": 0.55, "horizon_days": 20},
    {"countries": ["Nigeria","Ghana","Cameroon","Ivory Coast","Senegal"],
     "months": [6,7,8,9], "peak_months": [8,9],
     "signal": "medium", "type": "🌧 West African monsoon / flooding",
     "weight": 0.4, "horizon_days": 30},
    {"countries": ["Brazil","Colombia","Peru","Ecuador"],
     "months": [12,1,2,3], "peak_months": [1,2],
     "signal": "medium", "type": "🌊 South American wet / flood season",
     "weight": 0.4, "horizon_days": 30},
    # ── Winter ────────────────────────────────────────────────────────────────
    {"countries": ["Germany","Netherlands","Belgium","Poland","Denmark","Sweden"],
     "months": [12,1,2], "peak_months": [1,2],
     "signal": "low", "type": "❄️ North Sea / Baltic winter storm season",
     "weight": 0.25, "horizon_days": 30},
    {"countries": ["Canada","United States"],
     "cities": ["chicago","detroit","cleveland","minneapolis",
                "toronto","montreal","boston","new york"],
     "months": [12,1,2,3], "peak_months": [1,2],
     "signal": "low", "type": "❄️ North American winter storm / port freeze",
     "weight": 0.25, "horizon_days": 20},
    # ── Seismic ───────────────────────────────────────────────────────────────
    {"countries": ["Turkey"],
     "months": [1,2,3,4,5,6,7,8,9,10,11,12], "peak_months": [1,2,3],
     "signal": "high", "type": "🏔 Seismic risk — North Anatolian Fault (year-round)",
     "weight": 0.35, "horizon_days": 30},
    {"countries": ["Taiwan","Japan","Philippines","Indonesia","Nepal","Pakistan"],
     "months": [1,2,3,4,5,6,7,8,9,10,11,12], "peak_months": [3,4],
     "signal": "medium", "type": "🏔 High seismic zone (year-round)",
     "weight": 0.3, "horizon_days": 30},
    # ── Labor / Strike Cycles ─────────────────────────────────────────────────
    {"countries": ["Bangladesh"],
     "months": [10,11,12,1], "peak_months": [11,12],
     "signal": "high", "type": "✊ Bangladesh garment labor unrest — year-end wage negotiations",
     "weight": 0.6, "horizon_days": 30},
    {"countries": ["France","Belgium","Italy","Spain","Greece"],
     "months": [3,4,9,10,11], "peak_months": [9,10],
     "signal": "medium", "type": "✊ European autumn labor strike season",
     "weight": 0.4, "horizon_days": 20},
    {"countries": ["United States"],
     "cities": ["los angeles","long beach","seattle","new york","houston","savannah"],
     "months": [6,7,8,9,10], "peak_months": [7,8,9],
     "signal": "medium", "type": "✊ US West Coast longshoremen contract cycle (ILWU)",
     "weight": 0.4, "horizon_days": 30},
    {"countries": ["South Africa"],
     "months": [7,8,9,10], "peak_months": [8,9],
     "signal": "medium", "type": "✊ South African mining / port strike season",
     "weight": 0.4, "horizon_days": 20},
    {"countries": ["India"],
     "months": [1,2,11,12], "peak_months": [1,2],
     "signal": "low", "type": "✊ Indian trade union general strike season",
     "weight": 0.3, "horizon_days": 14},
    # ── Elections / Political Instability ─────────────────────────────────────
    {"countries": ["Nigeria","Kenya","Ghana","Zimbabwe","Democratic Republic of Congo"],
     "months": [2,3,8,9,10,11], "peak_months": [2,3,10,11],
     "signal": "medium", "type": "🗳 African election instability window",
     "weight": 0.35, "horizon_days": 30},
    {"countries": ["Pakistan","Bangladesh","Myanmar"],
     "months": [1,2,11,12], "peak_months": [1,2],
     "signal": "medium", "type": "🗳 South/SE Asian political instability window",
     "weight": 0.35, "horizon_days": 30},
    # ── Agricultural / Harvest ─────────────────────────────────────────────────
    {"countries": ["Ukraine","Russia"],
     "months": [6,7,8,9], "peak_months": [7,8],
     "signal": "high", "type": "🌾 Black Sea grain harvest — conflict risk to export capacity",
     "weight": 0.65, "horizon_days": 30},
    {"countries": ["Brazil","Argentina"],
     "months": [3,4,5], "peak_months": [4,5],
     "signal": "low", "type": "🌾 South American soy/corn harvest — port congestion",
     "weight": 0.3, "horizon_days": 20},
    # ── Red Sea / Chokepoints ─────────────────────────────────────────────────
    {"countries": ["Yemen","Egypt","Saudi Arabia","Djibouti","Eritrea","Somalia"],
     "cities": ["suez","jeddah","aden","djibouti"],
     "months": [1,2,3,4,5,6,7,8,9,10,11,12], "peak_months": [1,2,3,4,5,6],
     "signal": "high", "type": "⚓ Red Sea / Bab-el-Mandeb — Houthi attack risk",
     "weight": 0.75, "horizon_days": 30},
    # ── Iran / Strait of Hormuz ────────────────────────────────────────────────
    # 21% of global oil and LNG passes through Hormuz — any Iran escalation
    # creates immediate risk for ALL energy-linked supply chains globally.
    {"countries": ["Iran","Iraq","UAE","Kuwait","Bahrain","Qatar","Oman"],
     "cities": ["tehran","bandar abbas","dubai","abu dhabi","kuwait city",
                "muscat","basra","bushehr"],
     "months": [1,2,3,4,5,6,7,8,9,10,11,12], "peak_months": [1,2,3,4,5,6,7,8],
     "signal": "high", "type": "⚓ Strait of Hormuz / Persian Gulf — military escalation & oil transit risk",
     "weight": 0.80, "horizon_days": 30},
    {"countries": ["Israel","Lebanon","Jordan","Syria","Palestine"],
     "cities": ["tel aviv","haifa","ashdod","beirut","amman"],
     "months": [1,2,3,4,5,6,7,8,9,10,11,12], "peak_months": [1,2,3,4,5,6,7,8,9,10,11,12],
     "signal": "high", "type": "⚔️ Middle East conflict zone — ongoing regional escalation risk",
     "weight": 0.70, "horizon_days": 30},
    # ── Wildfire / Drought ────────────────────────────────────────────────────
    {"countries": ["United States","Canada","Australia"],
     "months": [6,7,8,9,10], "peak_months": [8,9],
     "signal": "low", "type": "🔥 Wildfire season — logistics disruption risk",
     "weight": 0.25, "horizon_days": 20},
    {"countries": ["Morocco","Algeria","Spain","Italy","Greece","Portugal"],
     "months": [7,8,9], "peak_months": [7,8],
     "signal": "low", "type": "🔥 Mediterranean wildfire / drought season",
     "weight": 0.25, "horizon_days": 20},
]


def get_forward_risk_signals(supplier_country: str, supplier_city: str) -> list[dict]:
    """
    Return synthetic forward-looking events from the seasonal calendar.
    Injected into scoring alongside real news — no news article required.
    Returns event dicts compatible with the scoring engine.
    """
    from datetime import date
    current_month = date.today().month
    signals       = []
    city_lower    = supplier_city.lower().strip()
    country_lower = supplier_country.lower().strip()

    for entry in SEASONAL_RISK_CALENDAR:
        countries_lower = [c.lower() for c in entry["countries"]]
        if country_lower not in countries_lower:
            continue

        # City filter: if specified, supplier city must match
        if "cities" in entry:
            cities_lower = [c.lower() for c in entry["cities"]]
            if not any(c in city_lower or city_lower in c for c in cities_lower):
                continue

        if current_month not in entry["months"]:
            continue

        is_peak = current_month in entry["peak_months"]
        weight  = entry["weight"] if is_peak else entry["weight"] * 0.5

        signals.append({
            "title":            f"[SEASONAL] {entry['type']} — {supplier_country}",
            "description":      (
                f"Seasonal risk window active for {supplier_country}. "
                f"{entry['type']}. Window: ~{entry['horizon_days']} days. "
                f"{'PEAK risk period.' if is_peak else 'Approaching peak.'}"
            ),
            "source":           "Seasonal Risk Calendar",
            "published_date":   datetime.now(timezone.utc).isoformat(),
            "detected_country": supplier_country,
            "event_type":       "seasonal",
            "severity":         entry["signal"],
            "_seasonal_weight": weight,
            "_is_seasonal":     True,
            "_horizon_days":    entry["horizon_days"],
        })

    return signals

def classify_signal(title: str, description: str = "") -> str:
    text = f"{title} {description}".lower()
    if any(kw in text for kw in HIGH_SIGNAL_KEYWORDS): return "high"
    if any(kw in text for kw in MEDIUM_SIGNAL_KEYWORDS): return "medium"
    return "low"


def _relative_date(date_str: str) -> str:
    """Convert a date string to a human-readable relative label."""
    if not date_str:
        return "Unknown date"
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        pub = None

        try:
            pub = parsedate_to_datetime(str(date_str))
        except Exception:
            pass
        if pub is None:
            for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
                        "%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d"):
                try:
                    pub = datetime.strptime(str(date_str)[:26].strip(), fmt)
                    break
                except Exception:
                    continue
        if pub is None:
            return date_str[:16].replace("T", " ")
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)

        delta = now - pub
        days  = delta.days
        hours = int(delta.total_seconds() // 3600)

        if hours < 1:
            return "Just now"
        elif hours < 6:
            return f"{hours}h ago"
        elif hours < 24:
            return "Today"
        elif days == 1:
            return "Yesterday"
        elif days <= 6:
            return f"{days} days ago"
        elif days <= 13:
            return "Last week"
        elif days <= 20:
            return f"{days} days ago"
        else:
            return pub.strftime("%b %d, %Y")
    except Exception:
        return str(date_str)[:16].replace("T", " ")


def get_continent(country_name: str) -> str | None:
    try:
        if pycountry is None or pc is None:
            return None
        country = pycountry.countries.lookup(country_name)
        return pc.country_alpha2_to_continent_code(country.alpha_2)
    except Exception:
        return None


# ─── Core Scorer ─────────────────────────────────────────────────────────────

def score_supplier(
    supplier_country: str,
    supplier_lat: float | None,
    supplier_lon: float | None,
    supplier_city: str,
    events_df: pd.DataFrame
) -> tuple[float, str]:
    """
    Score a supplier using geo-precise distance-based matching.

    For each event:
      1. Try to extract city coordinates from the article text
      2. If supplier is geocoded: compute haversine distance → distance multiplier
      3. If no city found in article: fall back to country/continent match
      4. Multiply by signal quality and recency
      5. Top 5 events contribute; each capped at 25 pts
    """
    if events_df.empty:
        return 0.0, "No events detected."

    supplier_continent = get_continent(supplier_country)
    has_geocode = supplier_lat is not None and supplier_lon is not None

    # Also get supplier city coords from our lookup as a fallback
    supplier_city_coords = CITY_COORDS.get(str(supplier_city).lower())

    # Use geocoded coords if available, else lookup
    if has_geocode:
        sup_lat, sup_lon = supplier_lat, supplier_lon
    elif supplier_city_coords:
        sup_lat, sup_lon = supplier_city_coords
    else:
        sup_lat, sup_lon = None, None

    # ── Inject seasonal/scheduled forward signals ────────────────────────────
    seasonal_signals = get_forward_risk_signals(supplier_country, supplier_city)

    scored_events = []

    # Process seasonal signals first (pre-weighted, distance is implicit)
    for sig in seasonal_signals:
        sev_mult   = SEVERITY_MULTIPLIER.get(sig["severity"], 0.2)
        base_pts   = 25.0 * sig["_seasonal_weight"] * sev_mult
        base_pts   = min(base_pts, MAX_POINTS_PER_EVENT)
        if base_pts > 0.3:
            scored_events.append({
                "score":       base_pts,
                "label":       f"Seasonal pattern — {supplier_city}, {supplier_country}",
                "signal":      sig["severity"],
                "title":       sig["title"],
                "dist_mult":   sig["_seasonal_weight"],
                "time_mult":   1.0,
                "is_forecast": True,
                "_is_seasonal": True,
            })

    for _, event in events_df.iterrows():
        title       = str(event.get("title", ""))
        description = str(event.get("description", ""))
        published   = str(event.get("published_date", ""))
        event_country = str(event.get("detected_country", "Unknown")).strip()
        full_text   = f"{title} {description}"

        # ── Step 1: Try to find exact city coords in the article ──
        event_coords = extract_city_coords(full_text)

        if event_coords and sup_lat is not None:
            # Best case: both supplier and event have coordinates
            miles = haversine_miles(sup_lat, sup_lon, event_coords[0], event_coords[1])
            dist_mult = distance_multiplier(miles)
            proximity_label = f"{int(miles)} miles away"
            base = 25.0

        elif event_country.lower() == supplier_country.lower():
            # Event is IN the supplier's country — direct national impact
            # This is a strong match. Use 0.6x — meaningful but below city-level precision.
            # High-signal events (war, sanctions, strikes) in the same country
            # affect ALL suppliers in that country, not just one city.
            base = 25.0
            dist_mult = 0.6
            proximity_label = f"National impact — {supplier_country}"

        elif event_country not in ("Unknown", "Global", ""):
            event_continent = get_continent(event_country)
            if event_continent and event_continent == supplier_continent:
                base = 25.0
                dist_mult = 0.05
                proximity_label = f"Same continent ({event_country})"
            else:
                base = 25.0
                dist_mult = 0.02
                proximity_label = f"Distant ({event_country})"
        else:
            base = 25.0
            dist_mult = 0.01
            proximity_label = "Global/Unknown location"

        # ── Step 2: Signal quality ──
        signal = classify_signal(title, description)
        # Boost national-match distance for high-signal geopolitical events
        # e.g. "US will strike Iran" affects every supplier in Iran at full weight
        if proximity_label.startswith("National impact") and signal == "high":
            dist_mult = min(dist_mult * 1.4, 1.0)
        sev_mult    = SEVERITY_MULTIPLIER.get(signal, 0.2)

        # ── Step 3: Recency / Forward-looking weight ──
        time_mult   = recency_weight(published, title, description)
        # Skip events that are too old (recency_weight returns 0.0)
        if time_mult == 0.0:
            continue
        is_fwd = time_mult > 1.0  # forecast/warning article

        # ── Final per-event score ──
        event_score = base * dist_mult * sev_mult * time_mult
        event_score = min(event_score, MAX_POINTS_PER_EVENT)

        if event_score > 0.3:
            scored_events.append({
                "score":       event_score,
                "label":       proximity_label,
                "signal":      signal,
                "title":       title,
                "dist_mult":   dist_mult,
                "time_mult":   time_mult,
                "is_forecast": time_mult > 1.0,
            })

    # Top N events only
    scored_events.sort(key=lambda x: x["score"], reverse=True)
    top_events = scored_events[:MAX_EVENTS_COUNTED]
    total_score = sum(e["score"] for e in top_events)

    # Normalize to 0–100
    max_possible = MAX_EVENTS_COUNTED * MAX_POINTS_PER_EVENT
    normalized = min(round((total_score / max_possible) * 100, 1), 100.0)

    # Build summary — only show events that actually scored meaningfully
    meaningful = [e for e in top_events if e["dist_mult"] >= 0.15]
    if meaningful:
        summary = "; ".join(
            f"[{e['signal'].upper()} · {e['label']}] {e['title'][:70]}"
            for e in meaningful[:3]
        )
    elif top_events:
        summary = f"No nearby events. Global monitoring active."
    else:
        summary = "No significant disruption events detected."

    return normalized, summary


def classify_risk_level(score: float) -> str:
    if score >= HIGH_RISK_THRESHOLD:   return "High"
    elif score >= MEDIUM_RISK_THRESHOLD: return "Medium"
    return "Low"


def run_scoring_engine() -> pd.DataFrame:
    """Score all suppliers using geo-precise distance matching."""
    suppliers_df = get_all_suppliers()
    events_df    = get_all_events()

    if suppliers_df.empty:
        return suppliers_df

    for _, row in suppliers_df.iterrows():
        score, summary = score_supplier(
            supplier_country = str(row.get("country", "")),
            supplier_lat     = row.get("latitude"),
            supplier_lon     = row.get("longitude"),
            supplier_city    = str(row.get("city", "")),
            events_df        = events_df
        )
        level = classify_risk_level(score)
        update_supplier_risk(row["supplier_name"], score, level, summary)

    return get_all_suppliers()



def get_score_breakdown(
    supplier_country: str,
    supplier_lat,
    supplier_lon,
    supplier_city: str,
    events_df
) -> list[dict]:
    """
    Return a full per-event breakdown for the drill-down panel.
    Each item has: title, source, published, signal, proximity_label,
                   miles, dist_mult, sev_mult, time_mult, event_score, event_country
    Returns ALL scored events (not just top 5) sorted by score desc.
    """
    if events_df.empty:
        return []

    supplier_continent = get_continent(supplier_country)
    supplier_city_coords = CITY_COORDS.get(str(supplier_city).lower())

    if supplier_lat is not None and supplier_lon is not None:
        sup_lat, sup_lon = supplier_lat, supplier_lon
    elif supplier_city_coords:
        sup_lat, sup_lon = supplier_city_coords
    else:
        sup_lat, sup_lon = None, None

    breakdown = []

    # Inject seasonal signals
    for sig in get_forward_risk_signals(supplier_country, supplier_city):
        sev_mult  = SEVERITY_MULTIPLIER.get(sig["severity"], 0.2)
        pts       = min(25.0 * sig["_seasonal_weight"] * sev_mult, MAX_POINTS_PER_EVENT)
        if pts > 0.1:
            breakdown.append({
                "title":          sig["title"],
                "source":         "Seasonal Risk Calendar",
                "published":      "Ongoing",
                "event_country":  supplier_country,
                "signal":         sig["severity"],
                "proximity":      f"Seasonal pattern — {supplier_city}, {supplier_country}",
                "miles":          None,
                "dist_mult":      round(sig["_seasonal_weight"], 3),
                "sev_mult":       sev_mult,
                "time_mult":      1.0,
                "points":         round(pts, 2),
                "counted":        False,
                "is_forecast":    True,
                "_is_seasonal":   True,
            })

    for _, event in events_df.iterrows():
        title         = str(event.get("title", ""))
        description   = str(event.get("description", ""))
        published     = str(event.get("published_date", ""))
        source        = str(event.get("source", ""))
        event_country = str(event.get("detected_country", "Unknown")).strip()
        full_text     = f"{title} {description}"

        event_coords = extract_city_coords(full_text)
        miles = None

        if event_coords and sup_lat is not None:
            miles = round(haversine_miles(sup_lat, sup_lon, event_coords[0], event_coords[1]))
            dist_mult = distance_multiplier(miles)
            proximity_label = f"{miles:,} miles away"
        elif event_country.lower() == supplier_country.lower():
            dist_mult = 0.15
            proximity_label = f"Same country — city unknown"
        elif event_country not in ("Unknown", "Global", ""):
            ec = get_continent(event_country)
            if ec and ec == supplier_continent:
                dist_mult = 0.05
                proximity_label = f"Same continent ({event_country})"
            else:
                dist_mult = 0.02
                proximity_label = f"Different continent ({event_country})"
        else:
            dist_mult = 0.01
            proximity_label = "Unknown location"

        signal    = classify_signal(title, description)
        sev_mult  = SEVERITY_MULTIPLIER.get(signal, 0.2)
        time_mult = recency_weight(published, title, description)

        # Skip events too old to matter
        if time_mult == 0.0:
            continue

        event_score = min(25.0 * dist_mult * sev_mult * time_mult, MAX_POINTS_PER_EVENT)

        if event_score > 0.1:
            breakdown.append({
                "title":          title,
                "source":         source,
                "published":      _relative_date(published),
                "event_country":  event_country,
                "signal":         signal,
                "proximity":      proximity_label,
                "miles":          miles,
                "dist_mult":      round(dist_mult, 3),
                "sev_mult":       sev_mult,
                "time_mult":      round(time_mult, 2),
                "points":         round(event_score, 2),
                "counted":        False,
                "is_forecast":    time_mult > 1.0,
                "url":            str(event.get("url", "") or ""),
            })

    # Sort and mark which events actually count (top 5)
    breakdown.sort(key=lambda x: x["points"], reverse=True)
    for i, ev in enumerate(breakdown):
        ev["counted"] = i < MAX_EVENTS_COUNTED
        ev["rank"] = i + 1

    return breakdown

# ─── Optional: AI-Enhanced Scoring ───────────────────────────────────────────

def ai_parse_event(event_text: str, openai_api_key: str) -> dict:
    """Use GPT to parse event severity/country. Falls back gracefully if unavailable."""
    if not openai_api_key or openai is None:
        return {"disruption_likely": "Unknown", "country": "Unknown", "severity": "medium"}
    try:
        import json as _json
        client = openai.OpenAI(api_key=openai_api_key)
        prompt = (f'Analyze this news excerpt. Respond ONLY in JSON.\n'
                  f'Article: "{event_text}"\n'
                  f'{{"disruption_likely":"Yes/No","country":"name or Unknown","severity":"low/medium/high"}}')
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100, temperature=0
        )
        return _json.loads(r.choices[0].message.content.strip())
    except Exception:
        return {"disruption_likely": "Unknown", "country": "Unknown", "severity": "medium"}
