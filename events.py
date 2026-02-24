"""
events.py - Fetch news and weather/disaster events from free APIs.
APIs used:
  - NewsAPI (free tier): https://newsapi.org
  - OpenWeatherMap (free alerts): https://openweathermap.org/api
"""

import os
import requests
import pycountry
import streamlit as st
from datetime import datetime, timedelta
from database import insert_event, clear_events

# Keywords that suggest supply chain disruption
DISRUPTION_KEYWORDS = [
    "disruption", "strike", "typhoon", "port delay", "shortage",
    "earthquake", "flood", "hurricane", "sanctions", "factory fire",
    "logistics", "supply chain", "cargo", "blockade", "war", "conflict"
]

# Country name variants to ISO mapping (for detection)
def get_country_names() -> dict:
    """Build a dictionary of lowercase country names/aliases -> official name."""
    mapping = {}
    for country in pycountry.countries:
        mapping[country.name.lower()] = country.name
        if hasattr(country, "common_name"):
            mapping[country.common_name.lower()] = country.name
    return mapping

COUNTRY_MAP = get_country_names()


def detect_country_in_text(text: str) -> str:
    """
    Simple heuristic: scan text for any known country name.
    Returns the first matched country name, or 'Unknown'.
    """
    if not text:
        return "Unknown"
    text_lower = text.lower()
    for name, official in COUNTRY_MAP.items():
        if name in text_lower:
            return official
    return "Unknown"


def fetch_news_events(api_key: str, supplier_countries: list[str]) -> int:
    """
    Fetch news from NewsAPI filtered by disruption keywords.
    Stores results in the database.
    Returns count of events inserted.
    """
    if not api_key:
        st.warning("NewsAPI key not provided. Skipping news fetch.")
        return 0

    base_url = "https://newsapi.org/v2/everything"
    query = " OR ".join(DISRUPTION_KEYWORDS[:6])  # API has query length limits
    from_date = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")

    params = {
        "q": query,
        "from": from_date,
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": 50,
        "apiKey": api_key,
    }

    try:
        response = requests.get(base_url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.HTTPError as e:
        st.error(f"NewsAPI error: {e}")
        return 0
    except Exception as e:
        st.error(f"Failed to fetch news: {e}")
        return 0

    articles = data.get("articles", [])
    count = 0

    for article in articles:
        title = article.get("title", "") or ""
        description = article.get("description", "") or ""
        combined_text = f"{title} {description}"

        # Detect country from article text
        detected_country = detect_country_in_text(combined_text)

        insert_event(
            title=title[:500],
            description=description[:1000],
            source=article.get("source", {}).get("name", "Unknown"),
            published_date=article.get("publishedAt", ""),
            country=detected_country,
            event_type="news",
            severity="medium",
            disruption_likely="Yes"
        )
        count += 1

    return count


def fetch_weather_alerts(api_key: str, supplier_countries: list[str]) -> int:
    """
    Fetch weather alerts from OpenWeatherMap for supplier countries.
    Uses the free-tier 'weather' endpoint (alerts available with onecall API).
    Falls back to checking severe weather indicators.
    Returns count of events inserted.
    """
    if not api_key:
        st.warning("OpenWeatherMap key not provided. Skipping weather fetch.")
        return 0

    count = 0
    # Map country names to capital cities for weather lookup
    COUNTRY_CAPITALS = {
        "China": (39.9042, 116.4074),
        "United States": (38.9072, -77.0369),
        "Japan": (35.6762, 139.6503),
        "Germany": (52.5200, 13.4050),
        "India": (28.6139, 77.2090),
        "Bangladesh": (23.8103, 90.4125),
        "Netherlands": (52.3676, 4.9041),
        "Nigeria": (6.5244, 3.3792),
    }

    for country in supplier_countries:
        coords = COUNTRY_CAPITALS.get(country)
        if not coords:
            continue

        lat, lon = coords
        url = f"https://api.openweathermap.org/data/3.0/onecall"
        params = {
            "lat": lat,
            "lon": lon,
            "appid": api_key,
            "exclude": "minutely,hourly,daily",
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 401:
                st.warning("OpenWeatherMap API key invalid or not authorized for One Call API.")
                break
            if response.status_code != 200:
                continue
            data = response.json()
            alerts = data.get("alerts", [])

            for alert in alerts:
                insert_event(
                    title=alert.get("event", "Weather Alert"),
                    description=alert.get("description", "")[:1000],
                    source="OpenWeatherMap",
                    published_date=datetime.utcfromtimestamp(
                        alert.get("start", datetime.utcnow().timestamp())
                    ).isoformat(),
                    country=country,
                    event_type="weather",
                    severity="high",
                    disruption_likely="Yes"
                )
                count += 1

        except Exception:
            continue

    return count


def refresh_all_events(news_api_key: str, weather_api_key: str, supplier_countries: list[str]) -> tuple[int, int]:
    """
    Clear existing events and fetch fresh news + weather events.
    Returns (news_count, weather_count).
    """
    clear_events()
    news_count = fetch_news_events(news_api_key, supplier_countries)
    weather_count = fetch_weather_alerts(weather_api_key, supplier_countries)
    return news_count, weather_count
