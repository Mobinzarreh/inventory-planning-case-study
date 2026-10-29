"""Match inventory against demand, one part at one location at a time.

Demand at each site follows ASSUMPTIONS.md 5.1: a PDC has its Direct row, the GDC has its
Direct row plus all Replenishment rows.

Read-only check. Run from the repo root: .venv\\Scripts\\python.exe exploration\\04_inventory_vs_demand.py
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `src` imports work when run as a plain script

from src.config import FORECAST_MONTHS
from src.load_raw import read_all_raw
from src.standardize import standardize_all

tables = standardize_all(read_all_raw())
# exact copies (5 inventory rows, 2 demand rows) dropped only for this check
inventory = tables["inventory"].drop_duplicates()
demand = tables["demand"].drop_duplicates()
print("inventory rows:", len(inventory), "| repeated part/location keys:",
      inventory.duplicated(["part_number", "location"]).sum())

# for this check only: a missing month counts as 0
demand["total_24m"] = demand[FORECAST_MONTHS].sum(axis=1)
demand["first_3m"] = demand[FORECAST_MONTHS[:3]].sum(axis=1)

# Replenishment rows already sit at location GDC, so one groupby gives demand per site
site_demand = demand.groupby(["part_number", "location"], as_index=False).agg(
    demand_rows=("total_24m", "size"),
    total_24m=("total_24m", "sum"),
    first_3m=("first_3m", "sum"),
)
print("part/location pairs with at least one demand row:", len(site_demand))

pairs = inventory[["part_number", "location", "on_hand_qty"]].merge(
    site_demand, on=["part_number", "location"], how="outer", indicator=True
)
print("pairs in either file:", len(pairs))


def label_pair(row: pd.Series) -> str:
    """Which group a part/location pair falls in."""
    has_stock_row = row["_merge"] != "right_only"
    if row["_merge"] == "left_only":
        return "stock row, no demand row"
    if row["total_24m"] > 0:
        return "stock row, demand" if has_stock_row else "no stock row, demand"
    return "stock row, demand row all zero" if has_stock_row else "no stock row, demand row all zero"


pairs["group"] = pairs.apply(label_pair, axis=1)

print("\n1. Pairs by group and location")
print(pd.crosstab(pairs["group"], pairs["location"], margins=True))

print("\n2. On-hand pieces by group")
on_hand_by_group = pairs.groupby("group")["on_hand_qty"].agg(["count", "sum", "min", "median", "max"])
print(on_hand_by_group)

print("\n3. Demand but no stock row (these start at zero stock)")
no_stock = pairs[pairs["group"] == "no stock row, demand"].sort_values(["location", "part_number"])
print("pairs:", len(no_stock), "| parts:", no_stock["part_number"].nunique())
print("demand in the first 3 months, quartiles:", no_stock["first_3m"].quantile([0, 0.25, 0.5, 0.75, 1]).to_dict())
print("with zero demand in the first 3 months:", (no_stock["first_3m"] == 0).sum())

print("\n4. Stock but no demand (no row, or a row that is zero for all 24 months)")
no_demand_groups = ["stock row, no demand row", "stock row, demand row all zero"]
no_demand = pairs[pairs["group"].isin(no_demand_groups)].sort_values(["location", "part_number"])
print(no_demand[["part_number", "location", "on_hand_qty", "group"]].to_string(index=False))

print("\n5. The 4 negative on-hand rows: which group?")
print(pairs.loc[pairs["on_hand_qty"] < 0, ["part_number", "location", "on_hand_qty", "group", "total_24m"]].to_string(index=False))
print("zero on-hand rows:", (pairs["on_hand_qty"] == 0).sum())

print("\n6. Demand that only starts later: has demand in 24 months but none in the first 3")
late_start = pairs[(pairs["total_24m"] > 0) & (pairs["first_3m"] == 0)]
print("pairs:", len(late_start))
print(late_start[["part_number", "location", "on_hand_qty", "total_24m"]].to_string(index=False))

print("\n7. Stock with no demand: is the part dead everywhere, or wanted at another site?")
network_demand = site_demand.groupby("part_number", as_index=False)["total_24m"].sum()
network_demand = network_demand.rename(columns={"total_24m": "network_total_24m"})
no_demand = no_demand.merge(network_demand, on="part_number", how="left")
no_demand["network_total_24m"] = no_demand["network_total_24m"].fillna(0)
dead_everywhere = no_demand[no_demand["network_total_24m"] == 0]
wanted_elsewhere = no_demand[no_demand["network_total_24m"] > 0]
print("dead everywhere:", len(dead_everywhere), "pairs,", dead_everywhere["part_number"].nunique(), "parts,",
      dead_everywhere["on_hand_qty"].sum(), "pieces")
print(sorted(dead_everywhere["part_number"].unique()))
print("wanted at another site:", len(wanted_elsewhere), "pairs,", wanted_elsewhere["on_hand_qty"].sum(), "pieces")
print(wanted_elsewhere[["part_number", "location", "on_hand_qty"]].to_string(index=False))

# where the demand for those parts actually sits
elsewhere_sites = site_demand[site_demand["part_number"].isin(wanted_elsewhere["part_number"]) & (site_demand["total_24m"] > 0)]
print(elsewhere_sites[["part_number", "location", "total_24m", "first_3m"]].sort_values("part_number").to_string(index=False))
