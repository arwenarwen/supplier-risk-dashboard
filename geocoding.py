"""
geocoding.py - Geocode supplier locations using OpenStreetMap Nominatim (free, no API key)
"""

import time
import requests
import pandas as pd
import streamlit as st
from database import update_supplier_geocoding

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "SupplierRiskDashboard/1.0 (contact@yourdomain.com)"}


def geocode_location(city: str, country: str) -> tuple[float | None, float | None]:
    """
    Use Nominatim to get (lat, lon) for a city+country.
    Falls back to country-only if city+country fails.
    Rate-limited to 1 req/sec per Nominatim policy.
    """
    queries = [f"{city}, {country}", country]

    for query in queries:
        try:
            params = {"q": query, "format": "json", "limit": 1}
            response = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=10)
            response.raise_for_status()
            results = response.json()
            if results:
                return float(results[0]["lat"]), float(results[0]["lon"])
        except Exception:
            pass
        time.sleep(1)  # Respect Nominatim rate limit

    return None, None


def geocode_suppliers(df: pd.DataFrame, progress_bar=None) -> pd.DataFrame:
    """
    Geocode all suppliers in the DataFrame.
    Updates database with lat/lon for each supplier.
    Returns updated DataFrame.
    """
    total = len(df)
    geocoded = 0

    for idx, row in df.iterrows():
        city = str(row.get("City", ""))
        country = str(row.get("Country", ""))
        supplier_name = str(row.get("Supplier Name", ""))

        # Skip if already geocoded
        if pd.notna(row.get("latitude")) and row.get("latitude") is not None:
            geocoded += 1
            continue

        lat, lon = geocode_location(city, country)
        df.at[idx, "latitude"] = lat
        df.at[idx, "longitude"] = lon

        # Persist to database
        if lat and lon:
            update_supplier_geocoding(supplier_name, lat, lon)

        geocoded += 1
        if progress_bar:
            progress_bar.progress(geocoded / total, text=f"Geocoding: {supplier_name}...")

        time.sleep(1)  # Nominatim rate limit: 1 request/second

    return df
