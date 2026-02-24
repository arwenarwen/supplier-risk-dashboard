"""
database.py - SQLite database initialization and operations
Handles all database creation, reads, and writes for the Supplier Risk Dashboard.
"""

import sqlite3
import pandas as pd
from datetime import datetime

DB_PATH = "supplier_risk.db"


def get_connection():
    """Return a connection to the SQLite database."""
    return sqlite3.connect(DB_PATH)


def init_db():
    """
    Initialize the database with required tables:
    - suppliers: stores supplier info, geocoding, and risk scores
    - events: stores news and weather/disaster events
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Suppliers table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_name TEXT NOT NULL,
            category TEXT,
            city TEXT,
            country TEXT,
            tier TEXT,
            latitude REAL,
            longitude REAL,
            risk_score REAL DEFAULT 0,
            risk_level TEXT DEFAULT 'Low',
            event_summary TEXT,
            last_updated TEXT
        )
    """)

    # Events table (news + weather/disaster)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            description TEXT,
            source TEXT,
            published_date TEXT,
            detected_country TEXT,
            event_type TEXT,
            severity TEXT DEFAULT 'medium',
            disruption_likely TEXT DEFAULT 'Unknown',
            url TEXT DEFAULT '',
            created_at TEXT
        )
    """)
    # Add url column to existing databases (safe migration)
    try:
        cursor.execute("ALTER TABLE events ADD COLUMN url TEXT DEFAULT ''")
    except Exception:
        pass  # Column already exists

    conn.commit()
    conn.close()


def upsert_suppliers(df: pd.DataFrame):
    """
    Insert or replace supplier records from a DataFrame.
    Clears existing suppliers and re-inserts (simple MVP approach).
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Drop and re-insert for clean state on new uploads
    cursor.execute("DELETE FROM suppliers")

    now = datetime.utcnow().isoformat()
    for _, row in df.iterrows():
        cursor.execute("""
            INSERT INTO suppliers (supplier_name, category, city, country, tier,
                                   latitude, longitude, risk_score, risk_level,
                                   event_summary, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row.get("Supplier Name"),
            row.get("Category"),
            row.get("City"),
            row.get("Country"),
            row.get("Tier"),
            row.get("latitude"),
            row.get("longitude"),
            row.get("risk_score", 0),
            row.get("risk_level", "Low"),
            row.get("event_summary", ""),
            now
        ))

    conn.commit()
    conn.close()


def update_supplier_geocoding(supplier_name: str, lat: float, lon: float):
    """Update latitude/longitude for a specific supplier."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE suppliers SET latitude=?, longitude=? WHERE supplier_name=?
    """, (lat, lon, supplier_name))
    conn.commit()
    conn.close()


def update_supplier_risk(supplier_name: str, risk_score: float, risk_level: str, summary: str):
    """Update risk score, level, and event summary for a supplier."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE suppliers
        SET risk_score=?, risk_level=?, event_summary=?, last_updated=?
        WHERE supplier_name=?
    """, (risk_score, risk_level, summary, datetime.utcnow().isoformat(), supplier_name))
    conn.commit()
    conn.close()


def get_all_suppliers() -> pd.DataFrame:
    """Return all suppliers as a DataFrame."""
    conn = get_connection()
    df = pd.read_sql_query("SELECT * FROM suppliers ORDER BY risk_score DESC", conn)
    conn.close()
    return df


def insert_event(title, description, source, published_date, country, event_type,
                 severity="medium", disruption_likely="Unknown", url=""):
    """Insert a single event (news or weather) into the events table."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO events (title, description, source, published_date,
                            detected_country, event_type, severity, disruption_likely, url, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (title, description, source, published_date, country, event_type,
          severity, disruption_likely, url or "", datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def get_all_events() -> pd.DataFrame:
    """Return all events as a DataFrame."""
    conn = get_connection()
    df = pd.read_sql_query("SELECT * FROM events ORDER BY published_date DESC", conn)
    conn.close()
    return df


def clear_events():
    """Clear all events from the database (before re-fetching)."""
    conn = get_connection()
    conn.execute("DELETE FROM events")
    conn.commit()
    conn.close()
