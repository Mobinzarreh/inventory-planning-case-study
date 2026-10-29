"""Evidence for the remaining cleaning rules: duplicates, negatives, blank forecast months,
missing qpc, and parts not in parts.csv.

Read-only check. Run from the repo root: .venv\\Scripts\\python.exe exploration\\06_cleaning_evidence.py
"""
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `src` imports work when run as a plain script

from src.config import FORECAST_MONTHS, GRAIN
from src.load_raw import read_all_raw
from src.standardize import standardize_all

raw_tables = read_all_raw()
tables = standardize_all(raw_tables)
DEMAND_KEYS = ["part_number", "location", "demand_type", "ship_to"]

print("1. Duplicate rows")
for name, table in tables.items():
    raw_copies = raw_tables[name].duplicated().sum()
    copies = table.duplicated().sum()
    key_repeats = table.drop_duplicates().duplicated(GRAIN[name]).sum()
    print(f"\n{name}: exact copies in raw {raw_copies}, after standardizing {copies}, "
          f"key repeats left once copies are dropped: {key_repeats}")
    copied_rows = table[table.duplicated(keep=False)]
    print(copied_rows.iloc[:, :6].to_string(index=False))

supply = tables["supply"].drop_duplicates()
same_shipment = supply.duplicated(["part_number", "destination", "qty"], keep=False)
print("\nsupply lines with the same part, destination and qty but a different PO line:")
print(supply.loc[same_shipment].sort_values(["part_number", "eta_date"]).to_string(index=False))

# demand in long form (one row per month) makes neighbour months easy to read
demand = tables["demand"].drop_duplicates()
demand_long = demand.melt(id_vars=DEMAND_KEYS, value_vars=FORECAST_MONTHS, var_name="month", value_name="qty")
demand_long = demand_long.sort_values(DEMAND_KEYS + ["month"])
demand_long["prev_month"] = demand_long.groupby(DEMAND_KEYS)["qty"].shift(1)
demand_long["next_month"] = demand_long.groupby(DEMAND_KEYS)["qty"].shift(-1)
demand_long["row_median"] = demand_long.groupby(DEMAND_KEYS)["qty"].transform("median")
parts = tables["parts"].drop_duplicates()
demand_long = demand_long.merge(parts[["part_number", "qpc"]], on="part_number", how="left")
first_3_months = FORECAST_MONTHS[:3]

print("\n2a. Negative on-hand")
inventory = tables["inventory"].drop_duplicates()
pair_demand = demand_long[demand_long["month"].isin(first_3_months)]
pair_demand = pair_demand.groupby(["part_number", "location"], as_index=False)["qty"].sum()
pair_demand["avg_month_first_3m"] = pair_demand["qty"] / 3
pair_supply = supply.groupby(["part_number", "destination"], as_index=False).agg(
    supply_qty=("qty", "sum"), first_eta=("eta_date", "min"))
pair_supply = pair_supply.rename(columns={"destination": "location"})
negative_stock = inventory[inventory["on_hand_qty"] < 0]
negative_stock = negative_stock.merge(pair_demand[["part_number", "location", "avg_month_first_3m"]], how="left")
negative_stock = negative_stock.merge(pair_supply, how="left")
print(negative_stock[["part_number", "location", "on_hand_qty", "avg_month_first_3m", "supply_qty", "first_eta"]].to_string(index=False))

print("\n2b. Negative forecast months")
cell_columns = DEMAND_KEYS + ["month", "qty", "prev_month", "next_month", "row_median", "qpc"]
print(demand_long.loc[demand_long["qty"] < 0, cell_columns].to_string(index=False))

print("\n3. Blank forecast months")
blanks = demand_long[demand_long["qty"].isna()].copy()
print("blank cells:", len(blanks), "| rows with a blank:", blanks[DEMAND_KEYS].drop_duplicates().shape[0])
print("by demand type:", blanks["demand_type"].value_counts().to_dict())
print("in the first 3 months:", blanks["month"].isin(first_3_months).sum())
print("by month:", blanks["month"].value_counts().sort_index().to_dict())
blanks["neighbour_avg"] = blanks[["prev_month", "next_month"]].mean(axis=1)
print("blank cells whose neighbours are both 0:", ((blanks["prev_month"] == 0) & (blanks["next_month"] == 0)).sum())
total_demand = demand_long["qty"].sum()
print("pieces at stake if filled with the neighbour average:", blanks["neighbour_avg"].sum(),
      "| share of all forecast pieces:", round(blanks["neighbour_avg"].sum() / total_demand, 4))
replen_months = demand_long[demand_long["demand_type"] == "Replenishment"]
print("share of Replenishment months that are 0:", round((replen_months["qty"] == 0).mean(), 2))
print("\nDirect blanks:")
print(blanks.loc[blanks["demand_type"] == "Direct", cell_columns].head(12).to_string(index=False))
print("\nReplenishment blanks:")
print(blanks.loc[blanks["demand_type"] == "Replenishment", cell_columns].to_string(index=False))


def pallet_gcd(values: pd.Series) -> int:
    """Largest number that divides every positive monthly quantity."""
    result = 0
    for value in values[values > 0]:
        result = math.gcd(result, int(value))
    return result


print("\n4. Missing or zero qpc")
replen_by_part = replen_months.groupby("part_number")["qty"]
gcd_by_part = replen_by_part.apply(pallet_gcd).rename("replen_gcd").reset_index()
known = parts[parts["qpc"] > 0].merge(gcd_by_part, on="part_number")
known = known[known["replen_gcd"] > 0]
print("parts with a known qpc and restock rows:", len(known), "| restock GCD equals qpc:",
      (known["replen_gcd"] == known["qpc"]).sum(), "| GCD is a multiple of qpc:",
      (known["replen_gcd"] % known["qpc"] == 0).sum())
print("where the GCD is not the qpc:", known.loc[known["replen_gcd"] != known["qpc"], ["part_number", "qpc", "replen_gcd"]].values.tolist())
for part in ["SKU57", "SKU34", "SKU5"]:
    part_row = parts[parts["part_number"] == part]
    description = part_row["part_description"].iloc[0]
    part_type = part_row["part_type"].iloc[0]
    print(f"\n{part}: {description} | {part_type} | qpc {part_row['qpc'].iloc[0]}")
    restock_values = replen_months.loc[(replen_months["part_number"] == part) & (replen_months["qty"] > 0), "qty"]
    print("  positive restock months:", sorted(restock_values.unique().tolist()), "| GCD:", pallet_gcd(restock_values))
    same_description = parts[(parts["part_description"] == description) & (parts["part_number"] != part)]
    print("  other parts with the same description:", same_description[["part_number", "qpc"]].values.tolist())
    same_type = parts.loc[parts["part_type"] == part_type, "qpc"]
    print("  qpc of other", part_type, "parts:", same_type.value_counts().to_dict())

print("\n5. Parts not in parts.csv")
for part in ["SKU901", "SKU902", "SKU903"]:
    print(f"\n{part}")
    print("  inventory:", inventory.loc[inventory["part_number"] == part, ["location", "on_hand_qty"]].values.tolist())
    print("  supply:", supply.loc[supply["part_number"] == part, ["po_number", "destination", "qty", "eta_date"]].values.tolist())
    print("  demand rows:", (demand["part_number"] == part).sum())
    # a mistyped number usually has one extra digit, so drop each digit in turn and look at the result
    digits = part.removeprefix("SKU")
    for i in range(len(digits)):
        look_alike = "SKU" + str(int(digits[:i] + digits[i + 1:]))
        stock = inventory.loc[inventory["part_number"] == look_alike, ["location", "on_hand_qty"]].values.tolist()
        demand_3m = pair_demand.loc[pair_demand["part_number"] == look_alike, ["location", "qty"]].values.tolist()
        in_master = look_alike in set(parts["part_number"])
        print(f"  look-alike {look_alike}: in master {in_master} | stock {stock} | demand first 3 months {demand_3m}")

print("\n6. Rules 4 and 5 together: negative months become blanks, then each blank gets the neighbour average")
months = demand.set_index(DEMAND_KEYS)[FORECAST_MONTHS]
negative_cells = months < 0
to_fill = months.isna() | negative_cells
# for a single gap, linear interpolation is the average of the two neighbours;
# a gap in the last month takes the month before it
filled = months.mask(negative_cells).interpolate(axis=1)
print("cells filled:", to_fill.sum().sum(), "| still blank after filling:", filled.isna().sum().sum())
next_to_another = to_fill & to_fill.shift(1, axis=1, fill_value=False)
print("filled cells right after another filled cell:", next_to_another.sum().sum())
print("filled in the last month (one neighbour only):", to_fill[FORECAST_MONTHS[-1]].sum())
pieces_added = filled[to_fill].sum().sum()
print("pieces added:", pieces_added, "| share of all forecast pieces after filling:", round(pieces_added / filled.sum().sum(), 4))
print("pieces added in blank cells only:", filled[months.isna()].sum().sum())
print("values used for the negative months:")
print(filled.where(negative_cells).stack().dropna().to_string())
print("SKU59 at PDC1, 24-month total after filling:", filled.loc[("SKU59", "PDC1", "Direct", "")].sum())
