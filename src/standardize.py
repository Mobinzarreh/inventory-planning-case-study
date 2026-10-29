"""Put messy raw values into one standard form.

This layer only fixes formats (spelling, case, spaces, number and date text).
It never drops a row or changes what a value means. Those calls (duplicates,
negatives, missing values) belong to the cleaning step and go in ASSUMPTIONS.md.
"""
import re

import pandas as pd

from src.config import FORECAST_MONTHS, RAW_FILES

MISSING_TEXT = ["", "N/A"]  # the only two "no value" tokens in the raw files
DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y"]  # 2026-11-13, 11/15/2026, 04-Dec-2026
# What standardizing does to each text column, in words for the log
TEXT_FIXES = {
    "part_number": "part number written one way: SKU and the number",
    "location": "site name: GDC, PDC1, PDC2 or PDC3",
    "destination": "site name: GDC, PDC1, PDC2 or PDC3",
    "ship_to": "site name: GDC, PDC1, PDC2 or PDC3",
    "demand_type": "demand type: Direct or Replenishment",
    "part_type": "label: one spelling, no stray case or spaces",
    "planner": "label: one spelling, no stray case or spaces",
}
NUMBER_COLUMNS = {"parts": ["qpc"], "inventory": ["on_hand_qty"], "supply": ["qty"], "demand": FORECAST_MONTHS}
DATE_COLUMNS = {"parts": [], "inventory": ["snapshot_date"], "supply": ["eta_date"], "demand": []}
LOG_COLUMNS = ["file", "column", "what_was_fixed", "values_changed", "raw_spellings", "examples"]


def standardize_part_number(value: str) -> str:
    """'sku16', 'SKU-53', ' SKU0022' -> 'SKU16', 'SKU53', 'SKU22'."""
    text = value.strip().upper().replace("-", "").replace(" ", "")
    match = re.fullmatch(r"SKU0*(\d+)", text)
    if match is None:
        return text  # unexpected pattern: leave it so the checks flag it
    return "SKU" + match.group(1)


def standardize_location(value: str) -> str:
    """'gdc', 'GDC ', 'Global DC' -> 'GDC' and 'PDC-3', 'PDC 3', 'pdc3' -> 'PDC3'. Blank stays blank."""
    text = value.strip().upper().replace("-", "").replace(" ", "")
    if text == "GLOBALDC":
        return "GDC"
    return text


def standardize_demand_type(value: str) -> str:
    """'DIRECT', 'Direct ' -> 'Direct' and 'Replen', 'REPLENISHMENT' -> 'Replenishment'."""
    text = value.strip().lower()
    if text == "direct":
        return "Direct"
    if text.startswith("replen"):
        return "Replenishment"
    return value.strip()  # unexpected value: leave it so the checks flag it


def standardize_label(values: pd.Series) -> pd.Series:
    """Fix stray case and spaces ('glass ', 'DANIEL OKAFOR') by using each label's most common spelling."""
    stripped = values.str.strip()
    lowered = stripped.str.lower()
    spelling_counts = pd.DataFrame({"key": lowered, "spelling": stripped}).value_counts().reset_index()
    # value_counts sorts by count, so the first spelling kept per key is the most common one
    most_common = spelling_counts.drop_duplicates("key").set_index("key")["spelling"]
    return lowered.map(most_common)


def parse_quantity(values: pd.Series) -> pd.Series:
    """Number text like '38.0' or '1,041' -> float. Blank and 'N/A' -> NaN."""
    text = values.str.strip().str.replace(",", "", regex=False)
    text = text.mask(text.isin(MISSING_TEXT))
    return pd.to_numeric(text)  # no errors='coerce': any other odd token should fail loudly


def parse_date(values: pd.Series) -> pd.Series:
    """The raw dates come in three formats. Try each one explicitly instead of letting pandas guess."""
    text = values.str.strip()
    parsed = pd.to_datetime(text, format=DATE_FORMATS[0], errors="coerce")
    for date_format in DATE_FORMATS[1:]:
        attempt = pd.to_datetime(text, format=date_format, errors="coerce")
        parsed = parsed.fillna(attempt)
    unparsed = text[parsed.isna() & ~text.isin(MISSING_TEXT)]
    if len(unparsed) > 0:
        raise ValueError(f"Dates in an unknown format: {list(unparsed.unique()[:5])}")
    return parsed


def standardize_parts(raw: pd.DataFrame) -> pd.DataFrame:
    """Part master with standard part numbers, labels and numeric qpc."""
    parts = raw.copy()
    parts["part_number"] = parts["part_number"].map(standardize_part_number)
    parts["part_description"] = parts["part_description"].str.strip()
    parts["part_type"] = standardize_label(parts["part_type"])
    parts["planner"] = standardize_label(parts["planner"])
    parts["qpc"] = parse_quantity(parts["qpc"])
    return parts


def standardize_inventory(raw: pd.DataFrame) -> pd.DataFrame:
    """On-hand snapshot with standard keys, numeric quantity and a real date."""
    inventory = raw.copy()
    inventory["part_number"] = inventory["part_number"].map(standardize_part_number)
    inventory["location"] = inventory["location"].map(standardize_location)
    inventory["on_hand_qty"] = parse_quantity(inventory["on_hand_qty"])
    inventory["snapshot_date"] = parse_date(inventory["snapshot_date"])
    return inventory


def standardize_supply(raw: pd.DataFrame) -> pd.DataFrame:
    """Open supply lines with standard keys, numeric quantity and a real ETA date."""
    supply = raw.copy()
    supply["po_number"] = supply["po_number"].str.strip()
    supply["po_line"] = supply["po_line"].str.strip()
    supply["part_number"] = supply["part_number"].map(standardize_part_number)
    supply["destination"] = supply["destination"].map(standardize_location)
    supply["source"] = supply["source"].str.strip()
    supply["qty"] = parse_quantity(supply["qty"])
    supply["eta_date"] = parse_date(supply["eta_date"])
    return supply


def standardize_demand(raw: pd.DataFrame) -> pd.DataFrame:
    """Monthly forecast (still wide, one column per month) with standard keys and numeric months."""
    demand = raw.copy()
    demand["part_number"] = demand["part_number"].map(standardize_part_number)
    demand["location"] = demand["location"].map(standardize_location)
    demand["demand_type"] = demand["demand_type"].map(standardize_demand_type)
    demand["ship_to"] = demand["ship_to"].map(standardize_location)
    for month in FORECAST_MONTHS:
        demand[month] = parse_quantity(demand[month])
    return demand


def log_row(file: str, column: str, fix: str, before: pd.Series, after: pd.Series) -> dict:
    """One log row: how many values changed, how many raw spellings they had, and up to 3 examples."""
    pairs = pd.DataFrame({"raw": before, "clean": after}).drop_duplicates()
    examples = "; ".join(f"'{raw}' -> '{clean}'" for raw, clean in pairs.head(3).itertuples(index=False))
    return {"file": RAW_FILES[file], "column": column, "what_was_fixed": fix, "values_changed": len(before),
            "raw_spellings": pairs["raw"].nunique(), "examples": examples}


def text_fixes(raw: pd.DataFrame, clean: pd.DataFrame, file: str) -> list[dict]:
    """Text columns whose values changed. Rows keep their order, so raw and standardized values line up."""
    rows = []
    for column, fix in TEXT_FIXES.items():
        if column not in raw:
            continue
        before = raw[column].fillna("")
        after = clean[column].fillna("")
        changed = before != after
        if changed.any():
            rows.append(log_row(file, column, fix, before[changed], after[changed]))
    return rows


def number_fixes(raw: pd.DataFrame, file: str) -> list[dict]:
    """Numbers written in an odd way ('38.0', '1,041'). Plain whole numbers and blanks (rule 4.8) are not counted."""
    columns = NUMBER_COLUMNS[file]
    text = raw[columns].stack(future_stack=True).dropna().str.strip()
    odd = text[text.str.contains(r"[.,]")]
    if odd.empty:
        return []
    label = columns[0] if len(columns) == 1 else "the 24 forecast months"
    parsed = parse_quantity(odd).map("{:g}".format)
    return [log_row(file, label, "number written as text, like '38.0' or '1,041'", odd, parsed)]


def date_fixes(raw: pd.DataFrame, clean: pd.DataFrame, file: str) -> list[dict]:
    """Dates not written as YYYY-MM-DD, read into that one format."""
    rows = []
    for column in DATE_COLUMNS[file]:
        odd = ~raw[column].str.strip().str.fullmatch(r"\d{4}-\d{2}-\d{2}")
        if odd.any():
            after = clean.loc[odd, column].dt.strftime("%Y-%m-%d")
            rows.append(log_row(file, column, "date in another format: MM/DD/YYYY or DD-Mon-YYYY", raw.loc[odd, column], after))
    return rows


def standardizing_log(raw_tables: dict[str, pd.DataFrame], tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Every value standardizing changed, by file and column, with examples, so the fixes are on record too."""
    rows = []
    for file in raw_tables:
        rows += text_fixes(raw_tables[file], tables[file], file)
        rows += number_fixes(raw_tables[file], file)
        rows += date_fixes(raw_tables[file], tables[file], file)
    log = pd.DataFrame(rows, columns=LOG_COLUMNS)
    print(f"  standardizing log: {log['values_changed'].sum():,} values fixed, in {len(log)} file and column groups")
    return log


def standardize_all(raw_tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Standardize all four tables. Row counts must not change in this step."""
    steps = {
        "parts": standardize_parts,
        "inventory": standardize_inventory,
        "supply": standardize_supply,
        "demand": standardize_demand,
    }
    tables = {}
    for name, step in steps.items():
        tables[name] = step(raw_tables[name])
        if len(tables[name]) != len(raw_tables[name]):
            raise ValueError(f"{name}: row count changed during standardizing")
    return tables
