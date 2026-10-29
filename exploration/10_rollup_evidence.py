"""Evidence for the DC-level roll-up (task 3): how different one-number summaries of WOS are for
each location, what drives them, and what a status view shows that one number hides.

Uses the master table with WOS and status (src/wos.py and src/status.py, ASSUMPTIONS.md 5.2 and 5.3).
Pallets are stock / qpc, so parts not in the part master are left out of pallet figures (4.10).

Read-only check. Run from the repo root: .venv\\Scripts\\python.exe exploration\\10_rollup_evidence.py
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `src` imports work when run as a plain script

from src.clean import clean_all
from src.config import LOCATIONS
from src.load_raw import read_all_raw
from src.master import build_master
from src.standardize import standardize_all
from src.status import EXCESS_WEEKS, SHORT_WEEKS, STATUS_ORDER, add_status
from src.wos import add_wos

pd.set_option("display.width", 200)

clean_tables, flag_list = clean_all(standardize_all(read_all_raw()))
master, set_aside_flags = build_master(clean_tables)
master, timing_flags = add_status(add_wos(master))
master["stock"] = master["on_hand_qty"].clip(lower=0)
master["pallets"] = master["stock"] / master["qpc"]
master["weekly_pallets"] = master["weekly_demand"] / master["qpc"]
with_demand = master[master["demand_24m"] > 0].copy()


def one_number_wos(pairs: pd.DataFrame) -> pd.Series:
    """Five ways to squash the WOS of many parts into one number."""
    capped_stock = pairs["stock"].clip(upper=EXCESS_WEEKS * pairs["weekly_demand"])
    return pd.Series({
        "pairs": len(pairs),
        "mean WOS": pairs["wos"].mean(),
        "median WOS": pairs["wos"].median(),
        "pieces-weighted WOS": pairs["stock"].sum() / pairs["weekly_demand"].sum(),
        "pallet-weighted WOS": pairs["pallets"].sum() / pairs["weekly_pallets"].sum(),
        "pieces-weighted, stock capped at 26 wks": capped_stock.sum() / pairs["weekly_demand"].sum(),
    })


print("\n1. One number per location, five ways (pairs with demand)")
summaries = {location: one_number_wos(with_demand[with_demand["location"] == location]) for location in LOCATIONS}
print(pd.DataFrame(summaries).T.round(1).to_string())

print("\n2. What drives the weighted numbers: the 5 pairs with the most stock and the most demand at each site")
for location in LOCATIONS:
    site = with_demand[with_demand["location"] == location]
    top_stock = site.nlargest(5, "stock")
    top_demand = site.nlargest(5, "weekly_demand")
    print(f"\n{location}: top 5 hold {top_stock['stock'].sum() / site['stock'].sum():.0%} of stock pieces, "
          f"top 5 make {top_demand['weekly_demand'].sum() / site['weekly_demand'].sum():.0%} of weekly demand")
    columns = ["part_number", "part_description", "stock", "weekly_demand", "wos", "status", "qpc"]
    print(top_stock[columns].round(1).to_string(index=False))
    without_top = site.drop(top_stock.index[:1])
    print(f"pieces-weighted WOS {site['stock'].sum() / site['weekly_demand'].sum():.1f}, "
          f"without the single biggest stock pair {without_top['stock'].sum() / without_top['weekly_demand'].sum():.1f}")

print("\n3. Status mix per location")
print("pairs:")
print(pd.crosstab(master["location"], master["status"], margins=True)[STATUS_ORDER + ["All"]].to_string())
print("share of pairs:")
print(pd.crosstab(master["location"], master["status"], normalize="index")[STATUS_ORDER].round(2).to_string())
print("pallets on hand:")
pallet_table = master.pivot_table(values="pallets", index="location", columns="status", aggfunc="sum", fill_value=0)
print(pallet_table.reindex(columns=STATUS_ORDER, fill_value=0).round(0).to_string())

print("\n4. What it would take to fix: pieces and pallets missing (to reach 4 weeks) and extra (above 26 weeks)")
needs = master["status"].isin(["Stockout", "Short"])
# on-hand is not clipped here, so backorders (4.6) count as missing pieces too
missing = (SHORT_WEEKS * master["weekly_demand"] - master["on_hand_qty"]).clip(lower=0).where(needs, 0)
extra = (master["stock"] - EXCESS_WEEKS * master["weekly_demand"]).clip(lower=0).where(master["status"] == "Excess", 0)
fix = pd.DataFrame({
    "missing pieces": missing.groupby(master["location"]).sum(),
    "missing pallets": (missing / master["qpc"]).groupby(master["location"]).sum(),
    "extra pieces": extra.groupby(master["location"]).sum(),
    "extra pallets": (extra / master["qpc"]).groupby(master["location"]).sum(),
    "no-demand pallets": master["pallets"].where(master["status"] == "No demand", 0).groupby(master["location"]).sum(),
})
print(fix.round(0).to_string())

print("\n5. Watch list (Healthy, WOS over 13 weeks) and timing flags per location")
watch = master[(master["status"] == "Healthy") & (master["wos"] > 13)]
print("watch list:", watch["location"].value_counts().reindex(LOCATIONS, fill_value=0).to_dict(),
      "| timing flags:", timing_flags["location"].value_counts().reindex(LOCATIONS, fill_value=0).to_dict())

print("\n6. Part mix per location: pallets per piece differ a lot, so pieces and pallets tell different stories")
mix = with_demand.groupby("location").agg(pieces=("stock", "sum"), pallets=("pallets", "sum"),
                                         median_qpc=("qpc", "median"), max_qpc=("qpc", "max"))
print(mix.round(0).to_string())
