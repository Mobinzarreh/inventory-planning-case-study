"""Read the raw CSVs exactly as received."""
import pandas as pd

from src.config import DATA_DIR, RAW_FILES


def read_raw(name: str) -> pd.DataFrame:
    """Read one raw file with every column as text, so nothing is converted or dropped on the way in."""
    path = DATA_DIR / RAW_FILES[name]
    # keep_default_na=False keeps 'N/A' and blank cells as text, so profiling can count them
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def read_all_raw() -> dict[str, pd.DataFrame]:
    """All four raw files, keyed by short name (parts, inventory, supply, demand)."""
    tables = {}
    for name in RAW_FILES:
        tables[name] = read_raw(name)
    return tables
