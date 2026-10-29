"""Connect incoming supply to the part/location pairs from 04_inventory_vs_demand.py.

Supply lands at (part_number, destination). GDC Transfers count at the PDC they are heading to
(ASSUMPTIONS.md 5.1). A pair with no stock row has 0 on hand, and an all-zero demand row counts
as no demand (ASSUMPTIONS.md 4.1 and 4.2).

Read-only check. Run from the repo root: .venv\\Scripts\\python.exe exploration\\05_supply_vs_pairs.py
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
# exact copies dropped only for this check
inventory = tables["inventory"].drop_duplicates()
demand = tables["demand"].drop_duplicates()
supply = tables["supply"].drop_duplicates()
print("supply lines:", len(supply), "| repeated po_number + po_line:",
      supply.duplicated(["po_number", "po_line"]).sum())

# demand per site, same as 04 (a missing month counts as 0 for this check)
demand["total_24m"] = demand[FORECAST_MONTHS].sum(axis=1)
demand["first_3m"] = demand[FORECAST_MONTHS[:3]].sum(axis=1)
site_demand = demand.groupby(["part_number", "location"], as_index=False).agg(
    total_24m=("total_24m", "sum"),
    first_3m=("first_3m", "sum"),
)
network_demand = site_demand.groupby("part_number", as_index=False)["total_24m"].sum()
network_demand = network_demand.rename(columns={"total_24m": "network_total_24m"})

# supply per pair, split by source
supply["supplier_qty"] = supply["qty"].where(supply["source"] == "Supplier", 0)
supply["transfer_qty"] = supply["qty"].where(supply["source"] == "GDC Transfer", 0)
supply_by_pair = supply.groupby(["part_number", "destination"], as_index=False).agg(
    supply_lines=("qty", "size"),
    supply_qty=("qty", "sum"),
    supplier_qty=("supplier_qty", "sum"),
    transfer_qty=("transfer_qty", "sum"),
    first_eta=("eta_date", "min"),
)
supply_by_pair = supply_by_pair.rename(columns={"destination": "location"})
print("pairs with incoming supply:", len(supply_by_pair), "| total pieces:", supply_by_pair["supply_qty"].sum())

stock = inventory[["part_number", "location", "on_hand_qty"]]
pairs = stock.merge(site_demand, on=["part_number", "location"], how="outer")
print("\npairs from stock + demand:", len(pairs))
pairs = pairs.merge(supply_by_pair, on=["part_number", "location"], how="outer", indicator=True)
print("pairs after adding supply:", len(pairs), "| supply pairs not in stock or demand files:",
      (pairs["_merge"] == "right_only").sum())
pairs = pairs.merge(network_demand, on="part_number", how="left")
pairs["network_total_24m"] = pairs["network_total_24m"].fillna(0)
pairs["supply_qty"] = pairs["supply_qty"].fillna(0)
pairs["has_supply"] = pairs["supply_qty"] > 0


def label_pair(row: pd.Series) -> str:
    """Group a pair by whether it has stock and real demand (same groups as 04)."""
    has_stock = pd.notna(row["on_hand_qty"])
    has_demand = row["total_24m"] > 0  # NaN (no demand row) is not > 0
    if has_stock and has_demand:
        return "1 stock + demand"
    if has_demand:
        return "2 demand, no stock"
    if has_stock:
        return "3 stock, no demand"
    return "4 no stock, no demand"


pairs["group"] = pairs.apply(label_pair, axis=1)

print("\n1. Pairs by group, with and without incoming supply")
print(pd.crosstab(pairs["group"], pairs["has_supply"], margins=True))
print("\nincoming pieces by group and source:")
print(pairs.groupby("group")[["supply_qty", "supplier_qty", "transfer_qty"]].sum())

print("\n2. Demand but no stock: is supply coming, and is it enough for the first 3 months?")
no_stock = pairs[pairs["group"] == "2 demand, no stock"].copy()
no_stock["covers_3m"] = no_stock["supply_qty"] >= no_stock["first_3m"]
print("pairs:", len(no_stock), "| with supply:", no_stock["has_supply"].sum(),
      "| supply covers first 3 months:", no_stock["covers_3m"].sum())
print("supply lands by 2026-11-30:", (no_stock["first_eta"] <= "2026-11-30").sum())
cols = ["part_number", "location", "first_3m", "total_24m", "supply_qty", "first_eta"]
print(no_stock.sort_values(["has_supply", "location", "part_number"])[cols].to_string(index=False))

print("\n3. Supply heading to pairs with no demand there")
no_demand_groups = ["3 stock, no demand", "4 no stock, no demand"]
no_demand_supply = pairs[pairs["group"].isin(no_demand_groups) & pairs["has_supply"]].copy()
no_demand_supply["part_has_demand_elsewhere"] = no_demand_supply["network_total_24m"] > 0
cols = ["part_number", "location", "group", "on_hand_qty", "supplier_qty", "transfer_qty",
        "first_eta", "part_has_demand_elsewhere"]
print(no_demand_supply.sort_values(["location", "part_number"])[cols].to_string(index=False))
print("lines behind these pairs:")
no_demand_lines = supply.merge(
    no_demand_supply[["part_number", "location"]],
    left_on=["part_number", "destination"], right_on=["part_number", "location"],
)
print(no_demand_lines[["po_number", "po_line", "part_number", "destination", "source", "qty", "eta_date"]].to_string(index=False))

print("\n4. The 6 no stock, no demand pairs from 04")
nothing = pairs[pairs["group"] == "4 no stock, no demand"]
print(nothing[["part_number", "location", "total_24m", "supply_qty", "network_total_24m"]].to_string(index=False))

print("\n5. Stock + demand pairs with supply: how big is the supply next to 3 months of demand?")
normal = pairs[(pairs["group"] == "1 stock + demand") & pairs["has_supply"]].copy()
normal["supply_vs_3m"] = normal["supply_qty"] / normal["first_3m"]
print("pairs:", len(normal), "| supply / first-3-month demand, quartiles:",
      normal["supply_vs_3m"].quantile([0, 0.25, 0.5, 0.75, 1]).round(2).to_dict())

print("\n6. Supply for parts not in parts.csv")
print(supply[supply["part_number"] == "SKU903"].to_string(index=False))
print(pairs.loc[pairs["part_number"] == "SKU903", ["part_number", "location", "group", "network_total_24m"]].to_string(index=False))
print("\ncheck: pairs per group add up to", pairs["group"].value_counts().sum(), "| supply pieces in pairs:",
      pairs["supply_qty"].sum())

print("\n7. PDC pairs with demand but no stock: does the GDC's planned restock cover them?")
# incoming_supply only lists what is already moving; the Replenishment rows are what the GDC plans to send next
replen = demand[demand["demand_type"] == "Replenishment"].copy()
replen["restock_nov"] = replen[FORECAST_MONTHS[0]]
replen = replen.rename(columns={"ship_to": "pdc", "first_3m": "restock_3m"})
pdc_direct = demand[(demand["demand_type"] == "Direct") & (demand["location"] != "GDC")].copy()
pdc_direct["demand_nov"] = pdc_direct[FORECAST_MONTHS[0]]
pdc_direct = pdc_direct.rename(columns={"location": "pdc"})

pdc_no_stock = no_stock[no_stock["location"] != "GDC"].rename(columns={"location": "pdc"})
pdc_no_stock = pdc_no_stock.merge(pdc_direct[["part_number", "pdc", "demand_nov"]], on=["part_number", "pdc"], how="left")
pdc_no_stock = pdc_no_stock.merge(replen[["part_number", "pdc", "restock_nov", "restock_3m"]], on=["part_number", "pdc"], how="left")
print("PDC pairs:", len(pdc_no_stock), "| with a restock row:", pdc_no_stock["restock_3m"].notna().sum())
print("Nov restock >= Nov demand:", (pdc_no_stock["restock_nov"] >= pdc_no_stock["demand_nov"]).sum())
print("restock + supply >= first-3-month demand:",
      (pdc_no_stock["restock_3m"] + pdc_no_stock["supply_qty"] >= pdc_no_stock["first_3m"]).sum())
cols = ["part_number", "pdc", "demand_nov", "restock_nov", "first_3m", "restock_3m", "supply_qty"]
print(pdc_no_stock.sort_values(["pdc", "part_number"])[cols].to_string(index=False))
