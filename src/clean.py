"""Apply the cleaning rules in ASSUMPTIONS.md section 4 (4.4 to 4.10).

Nothing is dropped or changed silently: every row that is dropped, changed or looks odd
gets one row in the flag list, with the rule, the original value, the value used and a note.
"""
import math

import pandas as pd

from src.config import FORECAST_MONTHS, GRAIN, OUTPUT_DIR, RAW_FILES, ROOT
from src.load_raw import read_all_raw
from src.standardize import standardize_all

FLAG_COLUMNS = ["rule", "issue", "file", "part_number", "location", "record", "original_value", "value_used", "note"]
QUANTITY_COLUMN = {"inventory": "on_hand_qty", "supply": "qty"}

UNKNOWN_PART_NOTE = "not in the part master: possibly a new part not yet set up, a mistyped part number, or a test record"
# best guesses come from exploration/07_unknown_part_numbers.py (ASSUMPTIONS.md 4.10)
UNKNOWN_PART_GUESSES = {
    "SKU901": "Best guess SKU1 (SKU001 with a 9 typed for the first 0). SKU91 unlikely, it does not fit the restock plan",
    "SKU902": "Could be SKU2, SKU92 or SKU90. Cannot be tested at the GDC",
    "SKU903": "Best guess SKU3. SKU93 unlikely, it does not fit the restock plan. PO9999 is the only PO outside PO1001 to PO1054",
}


def describe_rows(table: pd.DataFrame, name: str) -> pd.DataFrame:
    """Part, location and a short record label, so each flag says which row it is about."""
    described = pd.DataFrame({"file": name, "part_number": table["part_number"]})
    if name == "parts":
        described["location"] = ""
        described["record"] = table["part_description"]
    elif name == "inventory":
        described["location"] = table["location"]
        described["record"] = "on-hand"
    elif name == "supply":
        described["location"] = table["destination"]
        described["record"] = table["po_number"] + " line " + table["po_line"] + ", " + table["source"]
    else:
        described["location"] = table["location"]
        described["record"] = table["demand_type"]
        is_replen = table["demand_type"] == "Replenishment"
        described.loc[is_replen, "record"] = "Replenishment to " + table.loc[is_replen, "ship_to"]
    return described


def drop_exact_copies(table: pd.DataFrame, name: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """4.4: keep one copy of each exact duplicate row and flag the copies that were dropped."""
    is_copy = table.duplicated()
    flags = describe_rows(table[is_copy], name)
    flags["rule"] = "4.4"
    flags["issue"] = "exact duplicate row dropped"
    flags["note"] = "exact duplicate, likely exported twice"
    kept = table[~is_copy].reset_index(drop=True)
    if kept.duplicated(GRAIN[name]).any():
        raise ValueError(f"{name}: same key with different values, this needs its own rule")

    message = f"  {name}: {len(table)} rows -> {len(kept)} ({is_copy.sum()} exact copies dropped)"
    if name in QUANTITY_COLUMN:
        pieces_dropped = table.loc[is_copy, QUANTITY_COLUMN[name]].sum()
        message += f", {pieces_dropped:,.0f} duplicate pieces removed"
    print(message)
    return kept, flags


def flag_look_alike_supply(supply: pd.DataFrame) -> pd.DataFrame:
    """4.5: supply lines with the same part, site and qty on different PO lines are kept but flagged."""
    same_shipment = ["part_number", "destination", "qty"]
    look_alikes = supply[supply.duplicated(same_shipment, keep=False)].copy()
    look_alikes["po_label"] = look_alikes["po_number"] + " line " + look_alikes["po_line"]
    lines_in_group = look_alikes.groupby(same_shipment)["po_label"].agg(", ".join).rename("lines_in_group")
    look_alikes = look_alikes.join(lines_in_group, on=same_shipment)

    flags = describe_rows(look_alikes, "supply")
    flags["rule"] = "4.5"
    flags["issue"] = "possible double entry"
    flags["original_value"] = look_alikes["qty"]
    flags["value_used"] = look_alikes["qty"]
    flags["note"] = ("possible double entry, check with the buyer (same part, site and qty: "
                     + look_alikes["lines_in_group"] + ")")
    group_count = look_alikes.groupby(same_shipment).ngroups
    print(f"  supply: {len(look_alikes)} look-alike lines in {group_count} groups, all kept and flagged")
    return flags


def flag_negative_on_hand(inventory: pd.DataFrame) -> pd.DataFrame:
    """4.6: negative on-hand is kept as a backorder (WOS 0, Stockout) and flagged."""
    negative = inventory[inventory["on_hand_qty"] < 0]
    flags = describe_rows(negative, "inventory")
    flags["rule"] = "4.6"
    flags["issue"] = "negative on-hand kept as backorder"
    flags["original_value"] = negative["on_hand_qty"]
    flags["value_used"] = negative["on_hand_qty"]
    flags["note"] = "likely backorder, or a booking or count error"
    print(f"  inventory: {len(negative)} negative on-hand rows kept as backorders "
          f"({negative['on_hand_qty'].sum():,.0f} pieces owed)")
    return flags


def pallet_size_from_restock(restock_months: pd.Series) -> int:
    """Largest number that divides every positive restock month (0 if there are none)."""
    result = 0
    for value in restock_months[restock_months > 0]:
        result = math.gcd(result, int(value))
    return result


def estimate_missing_qpc(parts: pd.DataFrame, demand: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """4.9: fill a missing or zero qpc from the restock pallet pattern and mark it as estimated."""
    parts = parts.copy()
    needs_estimate = parts["qpc"].isna() | (parts["qpc"] <= 0)
    original_qpc = parts["qpc"].copy()
    restock = demand[demand["demand_type"] == "Replenishment"]
    for i in parts.index[needs_estimate]:
        part = parts.loc[i, "part_number"]
        restock_months = restock.loc[restock["part_number"] == part, FORECAST_MONTHS].stack()
        estimate = pallet_size_from_restock(restock_months)
        if estimate == 0:
            print(f"  WARNING {part}: no restock months to estimate qpc from, left blank")
            parts.loc[i, "qpc"] = float("nan")
            continue
        parts.loc[i, "qpc"] = estimate
    parts["qpc_is_estimate"] = needs_estimate & parts["qpc"].notna()

    flags = describe_rows(parts[needs_estimate], "parts")
    flags["rule"] = "4.9"
    flags["issue"] = "qpc estimated"
    flags["original_value"] = original_qpc[needs_estimate]
    flags["value_used"] = parts.loc[needs_estimate, "qpc"]
    flags["note"] = "qpc missing or zero in the part master, estimated from the restock pallet pattern, confirm with the part master owner"
    print(f"  parts: qpc estimated for {parts['qpc_is_estimate'].sum()} parts")
    return parts, flags


def flag_filled_cells(demand: pd.DataFrame, original: pd.DataFrame, filled: pd.DataFrame,
                      cells: pd.DataFrame, rule: str, issue: str, note: str) -> pd.DataFrame:
    """One flag per filled forecast cell, with the original value and the value used."""
    month_flags = []
    for month in FORECAST_MONTHS:
        rows = cells[month]
        if not rows.any():
            continue
        flags = describe_rows(demand[rows], "demand")
        flags["record"] = flags["record"] + ", " + month
        flags["original_value"] = original.loc[rows, month]
        flags["value_used"] = filled.loc[rows, month]
        month_flags.append(flags)
    flags = pd.concat(month_flags)
    flags["rule"] = rule
    flags["issue"] = issue
    flags["note"] = note
    return flags


def fill_forecast_gaps(demand: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """4.7 and 4.8: negative months become blanks, then each blank gets the average of its neighbours."""
    original = demand[FORECAST_MONTHS]
    is_negative = original < 0
    is_blank = original.isna()
    # 4.7: a negative month is not a valid forecast, so it is filled like a blank
    # 4.8: for a single gap, linear interpolation is the average of the two neighbours,
    # and a gap in the last month takes the month before it
    filled = original.mask(is_negative).interpolate(axis=1)
    if filled.isna().any().any():
        raise ValueError("Some forecast months could not be filled (a blank in the first month?)")
    filled_demand = demand.copy()
    filled_demand[FORECAST_MONTHS] = filled
    print(f"  demand: {is_negative.sum().sum()} negative and {is_blank.sum().sum()} blank months filled, "
          f"forecast pieces {original.sum().sum():,.1f} -> {filled.sum().sum():,.1f}")

    negative_flags = flag_filled_cells(demand, original, filled, is_negative, "4.7", "negative forecast month filled",
                                       "negative forecast: possible sign error, returns netted in, or typo")
    blank_flags = flag_filled_cells(demand, original, filled, is_blank, "4.8", "blank forecast month filled",
                                    "forecast unknown, filled with the average of the months before and after")
    return filled_demand, pd.concat([negative_flags, blank_flags])


def flag_unknown_parts(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """4.10: rows for parts missing from the part master are kept and flagged for investigation."""
    master = set(tables["parts"]["part_number"])
    file_flags = []
    for name in ["inventory", "supply", "demand"]:
        table = tables[name]
        unknown = table[~table["part_number"].isin(master)]
        flags = describe_rows(unknown, name)
        if name in QUANTITY_COLUMN:
            flags["original_value"] = unknown[QUANTITY_COLUMN[name]]
            flags["value_used"] = unknown[QUANTITY_COLUMN[name]]
        file_flags.append(flags)
    flags = pd.concat(file_flags)
    guesses = flags["part_number"].map(UNKNOWN_PART_GUESSES).fillna("No guess yet")
    flags["rule"] = "4.10"
    flags["issue"] = "part not in part master"
    flags["note"] = UNKNOWN_PART_NOTE + ". " + guesses
    print(f"  {len(flags)} rows for {flags['part_number'].nunique()} parts not in the part master, kept and flagged")

    # the one row where a confirmed guess could change a status (ASSUMPTIONS.md 4.10)
    inventory = tables["inventory"]
    sku1_row = inventory[(inventory["part_number"] == "SKU1") & (inventory["location"] == "PDC2")]
    status_flag = describe_rows(sku1_row, "inventory")
    status_flag["rule"] = "4.10"
    status_flag["issue"] = "could be affected by an unknown part"
    status_flag["original_value"] = sku1_row["on_hand_qty"]
    status_flag["value_used"] = sku1_row["on_hand_qty"]
    status_flag["note"] = ("if SKU901 is confirmed as SKU1, stock here rises from 17 to 52 pieces "
                           "(WOS about 5 to 15.5 weeks). Status stays Healthy under the 5.3 lines")
    return pd.concat([flags, status_flag])


def clean_all(tables: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Apply rules 4.4 to 4.10 to the standardized tables. Returns the clean tables and the flag list."""
    print("Cleaning (ASSUMPTIONS.md section 4)")
    clean = {}
    flags = []
    # 4.4 exact duplicate rows: keep one copy
    for name, table in tables.items():
        clean[name], copy_flags = drop_exact_copies(table, name)
        flags.append(copy_flags)
    # 4.5 look-alike supply lines: keep all, flag
    flags.append(flag_look_alike_supply(clean["supply"]))
    # 4.6 negative on-hand: keep as backorder, flag
    flags.append(flag_negative_on_hand(clean["inventory"]))
    # 4.9 missing qpc: estimated from restock months as received, before 4.7/4.8 fill any of them
    clean["parts"], qpc_flags = estimate_missing_qpc(clean["parts"], clean["demand"])
    flags.append(qpc_flags)
    # 4.7 and 4.8 negative and blank forecast months: fill from the neighbouring months
    clean["demand"], gap_flags = fill_forecast_gaps(clean["demand"])
    flags.append(gap_flags)
    # 4.10 parts not in the part master: keep, flag
    flags.append(flag_unknown_parts(clean))

    flag_list = pd.concat(flags, ignore_index=True).reindex(columns=FLAG_COLUMNS)
    print(f"  flag list: {len(flag_list)} rows")
    return clean, flag_list


def save_clean_tables(clean: dict[str, pd.DataFrame]) -> None:
    """Write each clean table to output/clean/ under its raw file name, so it can be opened and checked."""
    clean_dir = OUTPUT_DIR / "clean"
    clean_dir.mkdir(parents=True, exist_ok=True)
    for name, table in clean.items():
        path = clean_dir / RAW_FILES[name]
        table.to_csv(path, index=False)
        print(f"  wrote {path.relative_to(ROOT)} ({len(table)} rows)")


if __name__ == "__main__":
    clean_tables, flag_list = clean_all(standardize_all(read_all_raw()))
    save_clean_tables(clean_tables)
    print("\nflags by rule:")
    print(flag_list.groupby(["rule", "issue"]).size().to_string())
    print("\nsample flags (4.7, 4.9, 4.10):")
    sample = flag_list[flag_list["rule"].isin(["4.7", "4.9", "4.10"])]
    print(sample.drop(columns=["note"]).to_string(index=False))
    print("\nrows after cleaning:", {name: len(table) for name, table in clean_tables.items()})
