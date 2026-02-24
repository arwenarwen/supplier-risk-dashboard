"""
upload.py - Supplier CSV upload, validation, and database storage
"""

import pandas as pd
import streamlit as st
from database import upsert_suppliers

REQUIRED_COLUMNS = ["Supplier Name", "Category", "City", "Country", "Tier"]


def validate_csv(df: pd.DataFrame) -> tuple[bool, str]:
    """
    Validate that the uploaded DataFrame has required columns
    and no critical fields are empty.
    Returns (is_valid: bool, message: str)
    """
    # Check required columns
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        return False, f"Missing required columns: {', '.join(missing)}"

    # Check for empty critical fields
    critical = ["Supplier Name", "Country"]
    for col in critical:
        if df[col].isnull().any() or (df[col].astype(str).str.strip() == "").any():
            return False, f"Column '{col}' contains empty values. Please fill all rows."

    return True, f"✅ {len(df)} suppliers validated successfully."


def process_upload(uploaded_file) -> pd.DataFrame | None:
    """
    Read, validate, and store supplier CSV data.
    Returns the DataFrame if successful, None otherwise.
    """
    try:
        df = pd.read_csv(uploaded_file)
    except Exception as e:
        st.error(f"Failed to read CSV: {e}")
        return None

    # Strip whitespace from column names
    df.columns = df.columns.str.strip()

    is_valid, message = validate_csv(df)
    if not is_valid:
        st.error(message)
        return None

    st.success(message)

    # Initialize extra columns
    df["latitude"] = None
    df["longitude"] = None
    df["risk_score"] = 0.0
    df["risk_level"] = "Low"
    df["event_summary"] = ""

    # Save to database
    upsert_suppliers(df)
    return df


def get_sample_csv() -> str:
    """Return sample CSV content for user download."""
    sample = """Supplier Name,Category,City,Country,Tier
Acme Electronics,Electronics,Shenzhen,China,1
Global Textiles,Apparel,Dhaka,Bangladesh,2
Pacific Metals,Raw Materials,Tokyo,Japan,1
Euro Components,Electronics,Munich,Germany,2
Sunrise Chemicals,Chemicals,Mumbai,India,3
NordSupply,Logistics,Rotterdam,Netherlands,1
AfriParts,Manufacturing,Lagos,Nigeria,3
"""
    return sample
