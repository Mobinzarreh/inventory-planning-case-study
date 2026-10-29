"""Does the GDC restock plan already count the PDC's own stock? Plus a closer look at the odd transfers.

Read-only check. Run from the repo root: .venv\\Scripts\\python.exe exploration\\03_restock_and_on_hand.py
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `src` imports work when run as a plain script

from src.config import FORECAST_MONTHS, PDCS
from src.load_raw import read_all_raw
from src.standardize import standardize_all

tables = standardize_all(read_all_raw())
# exact copies dropped only for this check
demand = tables["demand"].drop_duplicates()
supply = tables["supply"].drop_duplicates()
inventory = tables["inventory"].drop_duplicates()
parts = tables["parts"].drop_duplicates()

pdc_direct = demand[(demand["demand_type"] == "Direct") & demand["location"].isin(PDCS)].copy()
pdc_direct["pdc"] = pdc_direct["location"]
replen = demand[demand["demand_type"] == "Replenishment"].copy()
replen["pdc"] = replen["ship_to"]
pairs = pdc_direct.merge(replen, on=["part_number", "pdc"], suffixes=("_dir", "_rep"))
print("part/PDC pairs with both a Direct and a restock row:", len(pairs))

pdc_on_hand = inventory.loc[inventory["location"].isin(PDCS), ["part_number", "location", "on_hand_qty"]]
pdc_on_hand = pdc_on_hand.rename(columns={"location": "pdc"})
pairs = pairs.merge(pdc_on_hand, on=["part_number", "pdc"], how="left")
print("rows after adding PDC on-hand:", len(pairs), "| with a PDC inventory row:", pairs["on_hand_qty"].notna().sum())

print("\n1. Running totals: by each month, has supply so far caught up with PDC demand so far?")
# for this check only: missing months count as 0, no inventory row or negative on-hand counts as 0
direct_so_far = pairs[[month + "_dir" for month in FORECAST_MONTHS]].fillna(0).cumsum(axis=1).to_numpy()
restock_so_far = pairs[[month + "_rep" for month in FORECAST_MONTHS]].fillna(0).cumsum(axis=1).to_numpy()
on_hand = pairs["on_hand_qty"].fillna(0).clip(lower=0).to_numpy().reshape(-1, 1)

covered_restock_only = pd.Series((restock_so_far >= direct_so_far).mean(axis=1))
covered_with_on_hand = pd.Series((on_hand + restock_so_far >= direct_so_far).mean(axis=1))
quartiles = [0, 0.25, 0.5, 0.75, 1]
print("share of months covered, restock only:", covered_restock_only.quantile(quartiles).round(2).to_dict())
print("share of months covered, on-hand + restock:", covered_with_on_hand.quantile(quartiles).round(2).to_dict())
print("pairs covered in every month with on-hand + restock:", (covered_with_on_hand == 1).sum(), "of", len(pairs))

print("\n2. Worked example: SKU82 at PDC2")
example = pairs[(pairs["part_number"] == "SKU82") & (pairs["pdc"] == "PDC2")]
first_months = FORECAST_MONTHS[:12]
print("qpc:", parts.loc[parts["part_number"] == "SKU82", "qpc"].tolist(), "| PDC2 on-hand:", example["on_hand_qty"].tolist())
print("months:      ", first_months)
print("PDC2 direct: ", example[[month + "_dir" for month in first_months]].to_numpy().tolist())
print("GDC restock: ", example[[month + "_rep" for month in first_months]].to_numpy().tolist())
print(supply[supply["part_number"] == "SKU82"].to_string(index=False))

print("\n3. The parts behind the transfers with no restock row")
for part in ["SKU96", "SKU17"]:
    print(f"\n--- {part}")
    print(parts[parts["part_number"] == part].to_string(index=False))
    print(inventory.loc[inventory["part_number"] == part, ["location", "on_hand_qty"]].to_string(index=False))
    part_demand = demand[demand["part_number"] == part].copy()
    part_demand["total_24m"] = part_demand[FORECAST_MONTHS].sum(axis=1)
    print("demand rows:", len(part_demand))
    print(part_demand[["location", "demand_type", "ship_to", "total_24m"]].to_string(index=False))
    print(supply[supply["part_number"] == part].to_string(index=False))
