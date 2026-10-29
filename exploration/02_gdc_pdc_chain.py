"""Does the GDC restock plan line up with PDC demand, and do GDC Transfers line up with the restock plan?

Read-only check. Run from the repo root: .venv\\Scripts\\python.exe exploration\\02_gdc_pdc_chain.py
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
# exact copies (2 demand rows, 4 supply lines) dropped only for this check
demand = tables["demand"].drop_duplicates()
supply = tables["supply"].drop_duplicates()
print("demand rows:", len(demand), "| supply lines:", len(supply))

pdc_direct = demand[(demand["demand_type"] == "Direct") & demand["location"].isin(PDCS)].copy()
pdc_direct["pdc"] = pdc_direct["location"]
replen = demand[demand["demand_type"] == "Replenishment"].copy()
replen["pdc"] = replen["ship_to"]
direct_keys = pdc_direct[["part_number", "pdc"]]
replen_keys = replen[["part_number", "pdc"]]
print("repeated keys, direct:", direct_keys.duplicated().sum(), "| restock:", replen_keys.duplicated().sum())

print("\n1. PDC Direct rows vs GDC restock rows (same part and PDC)")
matched = direct_keys.merge(replen_keys, how="outer", indicator=True)
print(pd.crosstab(matched["pdc"], matched["_merge"]))
print("restock rows with no PDC Direct row:", (matched["_merge"] == "right_only").sum())

# Direct rows the GDC never restocks: do they carry any demand?
no_restock = matched.loc[matched["_merge"] == "left_only", ["part_number", "pdc"]]
no_restock = no_restock.merge(pdc_direct, on=["part_number", "pdc"])
no_restock["total_24m"] = no_restock[FORECAST_MONTHS].sum(axis=1)
no_restock["missing_months"] = no_restock[FORECAST_MONTHS].isna().sum(axis=1)
no_restock = no_restock.sort_values(["pdc", "part_number"])
print("\nPDC Direct rows with no restock row:")
print(no_restock[["part_number", "pdc", "total_24m", "missing_months"]].to_string(index=False))

direct = demand[demand["demand_type"] == "Direct"]
direct_totals = direct[FORECAST_MONTHS].sum(axis=1)
print("\nDirect rows that are zero for all 24 months, any site:", (direct_totals == 0).sum())
print(direct.loc[direct_totals == 0, ["part_number", "location"]].to_string(index=False))

print("\n2. GDC Transfers vs restock rows")
transfers = supply[supply["source"] == "GDC Transfer"].copy()
transfers["pdc"] = transfers["destination"]
print("transfer lines:", len(transfers), "| ETAs from", transfers["eta_date"].min().date(),
      "to", transfers["eta_date"].max().date())

transfer_check = transfers.merge(replen_keys, on=["part_number", "pdc"], how="left", indicator=True)
print("rows before / after merge:", len(transfers), "/", len(transfer_check))
print("with a matching restock row:", (transfer_check["_merge"] == "both").sum())
transfer_direct = transfers.merge(direct_keys, on=["part_number", "pdc"], how="left", indicator=True)
print("with Direct demand at that PDC:", (transfer_direct["_merge"] == "both").sum())

print("\nTransfers with no restock row:")
unmatched = transfer_check[transfer_check["_merge"] == "left_only"]
print(unmatched[["po_number", "po_line", "part_number", "pdc", "qty", "eta_date"]].to_string(index=False))
