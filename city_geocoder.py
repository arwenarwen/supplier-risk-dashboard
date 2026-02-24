"""
city_geocoder.py - Dynamic city geocoding for any city in any country.

Replaces the hardcoded CITY_COORDS dict in scoring.py with a two-tier system:
  Tier 1: In-memory LRU cache (instant — already seen cities)
  Tier 2: Nominatim (OpenStreetMap) — any city, any country, any language, free

This means:
  - Odesa, Ukraine      → works
  - Ouagadougou, Burkina Faso → works  
  - Mazar-i-Sharif, Afghanistan → works
  - Any city mentioned in a news article → geocoded on first encounter, cached forever

Cache is persisted to a simple JSON file so it survives app restarts.
"""

import os
import json
import time
import requests
import threading
from functools import lru_cache
from pathlib import Path

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS       = {"User-Agent": "SupplierRiskDashboard/2.0 (supply-chain-monitor)"}
CACHE_FILE    = "city_coords_cache.json"

# Thread lock for safe concurrent writes
_cache_lock = threading.Lock()

# ─── Persistent cache ─────────────────────────────────────────────────────────

def _load_cache() -> dict:
    """Load persisted city cache from disk."""
    try:
        if Path(CACHE_FILE).exists():
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_cache(cache: dict):
    """Save city cache to disk."""
    try:
        with _cache_lock:
            with open(CACHE_FILE, "w") as f:
                json.dump(cache, f)
    except Exception:
        pass

# In-memory cache — loaded once at startup
_CITY_CACHE: dict = _load_cache()

# Nominatim rate limiter — max 1 req/sec
_last_nominatim_call = 0.0
_nominatim_lock = threading.Lock()

def _rate_limited_get(url: str, params: dict) -> dict | None:
    """Make a Nominatim request respecting the 1 req/sec rate limit."""
    global _last_nominatim_call
    with _nominatim_lock:
        elapsed = time.time() - _last_nominatim_call
        if elapsed < 1.1:
            time.sleep(1.1 - elapsed)
        _last_nominatim_call = time.time()

    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def geocode_city(city: str, country: str = "") -> tuple[float, float] | None:
    """
    Get (lat, lon) for any city in any country.
    
    Lookup order:
    1. In-memory + disk cache (instant)
    2. Nominatim with city + country (precise)
    3. Nominatim with city only (broader)
    
    Returns None if city cannot be geocoded.
    """
    if not city or not city.strip():
        return None

    # Normalize cache key
    cache_key = f"{city.lower().strip()}|{country.lower().strip()}"
    
    # Check cache first
    if cache_key in _CITY_CACHE:
        cached = _CITY_CACHE[cache_key]
        return (cached[0], cached[1]) if cached else None

    # Try Nominatim
    queries = []
    if country:
        queries.append({"q": f"{city}, {country}", "format": "json", "limit": 1})
    queries.append({"q": city, "format": "json", "limit": 1,
                    "featuretype": "city"})

    for params in queries:
        results = _rate_limited_get(NOMINATIM_URL, params)
        if results:
            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
            # Cache the result
            _CITY_CACHE[cache_key] = [lat, lon]
            _save_cache(_CITY_CACHE)
            return (lat, lon)

    # Cache the failure so we don't retry on every article
    _CITY_CACHE[cache_key] = None
    _save_cache(_CITY_CACHE)
    return None


def geocode_city_fast(city: str, country: str = "") -> tuple[float, float] | None:
    """
    Cache-only lookup — returns instantly, never makes a network call.
    Used during scoring (called hundreds of times) to avoid rate limits.
    Only returns a result if the city was previously geocoded.
    """
    cache_key = f"{city.lower().strip()}|{country.lower().strip()}"
    cached = _CITY_CACHE.get(cache_key)
    if cached:
        return (cached[0], cached[1])
    # Also try without country
    cache_key_bare = f"{city.lower().strip()}|"
    cached_bare = _CITY_CACHE.get(cache_key_bare)
    if cached_bare:
        return (cached_bare[0], cached_bare[1])
    return None


def warm_cache_for_suppliers(suppliers: list[dict]):
    """
    Pre-geocode all supplier cities at fetch time (not scoring time).
    Call this when suppliers are uploaded so their cities are cached
    before any news articles need to be matched against them.
    
    suppliers: list of dicts with 'city' and 'country' keys
    """
    for s in suppliers:
        city    = str(s.get("city", "") or "").strip()
        country = str(s.get("country", "") or "").strip()
        if city:
            geocode_city(city, country)  # result gets cached automatically


def warm_cache_for_article_cities(cities_found: list[tuple[str, str]]):
    """
    Geocode cities extracted from news articles.
    cities_found: list of (city_name, country_hint) tuples
    Called in background after article parsing.
    """
    for city, country in cities_found:
        if city and len(city) > 2:
            geocode_city(city, country)


def get_cache_stats() -> dict:
    """Return stats about the geocoding cache."""
    total   = len(_CITY_CACHE)
    hits    = sum(1 for v in _CITY_CACHE.values() if v is not None)
    misses  = total - hits
    return {"total_cached": total, "successful": hits, "failed_lookups": misses}
