"""Paths and fixed facts about the case study data."""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"  # raw files exactly as received, never written to
OUTPUT_DIR = ROOT / "output"

RAW_FILES = {
    "parts": "parts.csv",
    "inventory": "inventory_on_hand.csv",
    "supply": "incoming_supply.csv",
    "demand": "demand_forecast_monthly.csv",
}

# The columns that should identify one row in each file
GRAIN = {
    "parts": ["part_number"],
    "inventory": ["part_number", "location"],
    "supply": ["po_number", "po_line"],
    "demand": ["part_number", "location", "demand_type", "ship_to"],
}

SNAPSHOT_DATE = pd.Timestamp("2026-10-22")
FORECAST_MONTHS = [str(month) for month in pd.period_range("2026-11", "2028-10", freq="M")]
LOCATIONS = ["GDC", "PDC1", "PDC2", "PDC3"]
PDCS = ["PDC1", "PDC2", "PDC3"]
