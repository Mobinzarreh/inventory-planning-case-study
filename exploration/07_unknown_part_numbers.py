"""Part numbers outside the master (SKU1 to SKU100): how many, and which real part could each be a typo of?

Each guess is tested against the other files. The main test uses the restock plan: step 2 showed the
GDC plans a PDC's first restock for when that PDC's own stock runs out.

Read-only check. Run from the repo root: .venv\\Scripts\\python.exe exploration\\07_unknown_part_numbers.py
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `src` imports work when run as a plain script

from src.config import FORECAST_MONTHS
from src.load_raw import read_all_raw
from src.standardize import standardize_all

raw_tables = read_all_raw()
tables = standardize_all(raw_tables)
parts = tables["parts"].drop_duplicates()
inventory = tables["inventory"].drop_duplicates()
supply = tables["supply"].drop_duplicates()
demand = tables["demand"].drop_duplicates()
master = set(parts["part_number"])

print("1. Odd part number spellings in the raw files")
all_odd_spellings = set()
for name in ["parts", "inventory", "supply", "demand"]:
    raw_values = raw_tables[name]["part_number"]
    clean_values = tables[name]["part_number"]
    changed = raw_values != clean_values
    all_odd_spellings.update(raw_values[changed])
    outside = sorted(set(clean_values) - master)
    print(f"{name}: {changed.sum()} rows spelled oddly ({raw_values[changed].nunique()} spellings), "
          f"outside the master after standardizing: {outside}")
print("distinct odd spellings across all files:", len(all_odd_spellings))
padded = sorted(spelling for spelling in all_odd_spellings if "SKU0" in spelling.upper().replace("-", "").replace(" ", ""))
print("zero-padded spellings:", padded)

# monthly series per pair, missing months as 0 for this check only
direct = demand[demand["demand_type"] == "Direct"].set_index(["part_number", "location"])[FORECAST_MONTHS].fillna(0)
restock = demand[demand["demand_type"] == "Replenishment"].set_index(["part_number", "ship_to"])[FORECAST_MONTHS].fillna(0)
on_hand = inventory.set_index(["part_number", "location"])["on_hand_qty"]
incoming = supply.groupby(["part_number", "destination"])["qty"].sum()


def first_month_above(monthly: pd.Series, level: float) -> str:
    """First month where the running total goes above a level, or 'none' within the 24 months."""
    running_total = monthly.cumsum()
    above = running_total[running_total > level]
    if len(above) == 0:
        return "none"
    return above.index[0]


def predicted_first_restock(part: str, pdc: str, extra_stock: float = 0, extra_supply: float = 0) -> str:
    """Month the PDC runs out: when demand so far passes stock + incoming supply."""
    stock = on_hand.get((part, pdc), 0) + extra_stock
    supply_qty = incoming.get((part, pdc), 0) + extra_supply
    return first_month_above(direct.loc[(part, pdc)], stock + supply_qty)


def actual_first_restock(part: str, pdc: str) -> str:
    """First month the GDC plans to ship anything to this PDC."""
    if (part, pdc) not in restock.index:
        return "no restock row"
    return first_month_above(restock.loc[(part, pdc)], 0)


print("\n2. Does 'first restock = month the PDC runs out' hold for the known parts?")
month_number = {month: i for i, month in enumerate(FORECAST_MONTHS)}
month_number["none"] = len(FORECAST_MONTHS)  # never runs out / never restocked within 24 months
months_off = []
for part, pdc in restock.index:
    predicted = month_number[predicted_first_restock(part, pdc)]
    actual = month_number[actual_first_restock(part, pdc)]
    months_off.append(abs(predicted - actual))
months_off = pd.Series(months_off)
print(f"exact month predicted for {(months_off == 0).sum()} of {len(months_off)} restock rows")
print("months off, count of rows:", months_off.clip(upper=5).value_counts().sort_index().to_dict(), "(5 means 5 or more)")

print("\n3. Candidates for each unknown code (drop one digit, which also covers a 0 hit as 9 in 'SKU00x')")
unknown_rows = [
    ("SKU901", "PDC2", "stock", 35),
    ("SKU902", "GDC", "stock", 120),
    ("SKU903", "PDC1", "supply", 60),
]
for unknown, location, kind, qty in unknown_rows:
    print(f"\n{unknown}: {qty} pieces of {kind} at {location}")
    digits = unknown.removeprefix("SKU")
    for i in range(len(digits)):
        candidate = "SKU" + str(int(digits[:i] + digits[i + 1:]))
        has_row = (candidate, location) in on_hand.index
        stock = on_hand.get((candidate, location), 0)
        demand_3m = direct.loc[(candidate, location), FORECAST_MONTHS[:3]].sum() if (candidate, location) in direct.index else 0
        line = (f"  {candidate}: stock row {has_row} ({stock}), direct demand first 3 months {demand_3m}, "
                f"supply coming {incoming.get((candidate, location), 0)}")
        if location != "GDC" and (candidate, location) in restock.index:
            extra_stock = qty if kind == "stock" else 0
            extra_supply = qty if kind == "supply" else 0
            line += (f"\n      first restock: actual {actual_first_restock(candidate, location)}, "
                     f"predicted as recorded {predicted_first_restock(candidate, location)}, "
                     f"predicted with the {qty} added {predicted_first_restock(candidate, location, extra_stock, extra_supply)}")
        print(line)
