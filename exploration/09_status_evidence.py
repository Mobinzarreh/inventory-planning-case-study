"""Evidence for the inventory status thresholds: how WOS is spread, how fast stock can be
replenished, and how many pairs, pieces and pallets land in each status under a few candidate
thresholds.

Uses the master table with WOS (src/weeks_of_supply.py, ASSUMPTIONS.md 5.2). Pallets are stock / qpc and are
blank for the parts not in the part master (4.10). Negative on-hand counts as 0 stock (4.6).

Read-only check. Run from the repo root: .venv\\Scripts\\python.exe exploration\\09_status_evidence.py
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `src` imports work when run as a plain script

from src.clean import clean_all
from src.config import FORECAST_MONTHS, LOCATIONS, SNAPSHOT_DATE
from src.load_raw import read_all_raw
from src.master_table import build_master
from src.standardize import standardize_all
from src.weeks_of_supply import add_wos, weeks_until_run_out

pd.set_option("display.width", 200)
QUANTILES = [0.1, 0.25, 0.5, 0.75, 0.9]

clean_tables, flag_list = clean_all(standardize_all(read_all_raw()))
master, set_aside_flags = build_master(clean_tables)
master = add_wos(master)
master["stock"] = master["on_hand_qty"].clip(lower=0)
master["pallets"] = master["stock"] / master["qpc"]
with_demand = master[master["demand_24m"] > 0].copy()
stocked = with_demand[with_demand["stock"] > 0].copy()




def months_between_shipments(row: pd.Series) -> float:
    """Median number of months between planned shipments in one restock row."""
    ship_positions = np.flatnonzero(row.to_numpy() > 0)
    if len(ship_positions) < 2:
        return math.nan
    return float(np.median(np.diff(ship_positions)))


def status_by_wos(pairs: pd.DataFrame, short_line: dict, excess_line: dict) -> pd.Series:
    """Status from WOS alone, with the short and excess lines given per location."""
    short = pairs["location"].map(short_line)
    excess = pairs["location"].map(excess_line)
    status = pd.Series("Healthy", index=pairs.index)
    status.loc[pairs["wos"] > excess] = "Excess"
    status.loc[pairs["wos"] < short] = "Short"
    status.loc[pairs["wos"] == 0] = "Stockout"
    status.loc[pairs["wos"].isna()] = "No demand"
    return status


print("\n1. How WOS is spread (pairs with demand)")
wos_bands = pd.cut(with_demand["wos"], [-0.1, 0, 2, 4, 8, 13, 26, 52, math.inf],
                   labels=["0", "0 to 2", "2 to 4", "4 to 8", "8 to 13", "13 to 26", "26 to 52", "over 52"])
band_table = pd.crosstab(wos_bands, with_demand["location"])
band_table["pieces"] = with_demand.groupby(wos_bands, observed=False)["stock"].sum()
band_table["pallets"] = with_demand.groupby(wos_bands, observed=False)["pallets"].sum().round(0)
print(band_table.to_string())
print("WOS quantiles by location, pairs with stock:")
print(stocked.groupby("location")["wos"].quantile(QUANTILES).unstack().round(1).to_string())

print("\n2. How fast can stock be replenished?")
supply = clean_tables["supply"].copy()
supply["weeks_to_eta"] = (supply["eta_date"] - SNAPSHOT_DATE).dt.days / 7
print("weeks from the snapshot to ETA, open supply:")
print(supply.groupby(["source", "destination"])["weeks_to_eta"].describe()[["count", "min", "50%", "max"]].round(1).to_string())
demand = clean_tables["demand"]
restock = demand[demand["demand_type"] == "Replenishment"]
shipments = restock[["part_number", "ship_to"]].rename(columns={"ship_to": "location"})
shipments["months_between"] = restock[FORECAST_MONTHS].apply(months_between_shipments, axis=1)
shipments["typical_shipment"] = restock[FORECAST_MONTHS].where(restock[FORECAST_MONTHS] > 0).median(axis=1)
shipments = shipments.merge(with_demand[["part_number", "location", "weekly_demand", "qpc"]],
                            on=["part_number", "location"], how="left")
shipments["shipment_weeks"] = shipments["typical_shipment"] / shipments["weekly_demand"]
print("\nrestock rows:", len(shipments), "| months between planned shipments (median per row):",
      shipments["months_between"].value_counts().sort_index().to_dict())
print("one typical restock shipment, in weeks of the PDC's demand, quantiles by PDC:")
print(shipments.groupby("location")["shipment_weeks"].quantile(QUANTILES).unstack().round(1).to_string())
with_demand["pallet_weeks"] = with_demand["qpc"] / with_demand["weekly_demand"]
print("one pallet, in weeks of the site's demand, quantiles by location:")
print(with_demand.groupby("location")["pallet_weeks"].quantile(QUANTILES).unstack().round(1).to_string())

print("\n3. Pairs with stock below each short line and above each excess line")
short_counts = {f"WOS under {line}": (stocked["wos"] < line).groupby(stocked["location"]).sum() for line in [1, 2, 4, 6, 8]}
print(pd.DataFrame(short_counts).T.to_string())
for line in [13, 26, 39, 52]:
    above = stocked[stocked["wos"] > line]
    beyond = above["stock"] - line * above["weekly_demand"]
    print(f"WOS over {line}: pairs {above.groupby('location').size().to_dict()}, stock {above['stock'].sum():,.0f} pieces, "
          f"of which beyond {line} weeks {beyond.sum():,.0f} pieces ({(beyond / above['qpc']).sum():,.0f} pallets)")

print("\n4. Status counts under candidate thresholds (all 340 pairs)")
candidates = {
    "short 2, excess 13": ({site: 2 for site in LOCATIONS}, {site: 13 for site in LOCATIONS}),
    "short 4, excess 13": ({site: 4 for site in LOCATIONS}, {site: 13 for site in LOCATIONS}),
    "short 4, excess 26": ({site: 4 for site in LOCATIONS}, {site: 26 for site in LOCATIONS}),
    "GDC 8/26, PDCs 4/13": ({"GDC": 8, "PDC1": 4, "PDC2": 4, "PDC3": 4}, {"GDC": 26, "PDC1": 13, "PDC2": 13, "PDC3": 13}),
}
for name, (short_line, excess_line) in candidates.items():
    status = status_by_wos(master, short_line, excess_line)
    counts = pd.crosstab(master["location"], status, margins=True)
    pallets = master.groupby(status)["pallets"].sum().round(0).to_dict()
    print(f"\n{name} | pallets by status: {pallets}")
    print(counts.to_string())

print("\n5. No demand: why (all pairs with no demand)")
no_demand = master[master["wos"].isna()].copy()
reason = pd.Series("no demand anywhere (dead stock)", index=no_demand.index)
reason.loc[no_demand["direct_elsewhere_24m"] > 0] = "wanted at another site"
reason.loc[~no_demand["in_part_master"]] = "not in the part master (4.10)"
print(no_demand.groupby(reason).agg(pairs=("part_number", "size"), parts=("part_number", "nunique"),
                                    pieces=("stock", "sum"), pallets=("pallets", "sum"),
                                    incoming=("supply_qty", "sum")).round(0).to_string())
print(pd.crosstab(reason, no_demand["location"]).to_string())

print("\n6. Stockout: demand but no stock today")
out = with_demand[with_demand["stock"] == 0]
kind = pd.Series("on-hand 0", index=out.index)
kind.loc[~out["has_inventory_row"]] = "no inventory row"
kind.loc[out["on_hand_qty"] < 0] = "negative (backorder)"
print(pd.crosstab(kind, out["location"], margins=True).to_string())
print("with supply on the way:", (out["supply_qty"] > 0).sum(), "of", len(out))

print("\n7. Timing: WOS against the run-out weeks, for pairs with stock")
stocked["run_out_weeks"] = [
    weeks_until_run_out(on_hand, gap_demand, list(months))
    for on_hand, gap_demand, months in zip(stocked["on_hand_qty"], stocked["gap_demand"], stocked[FORECAST_MONTHS].to_numpy())
]
for line in [2, 4]:
    hidden = (stocked["wos"] >= line) & (stocked["run_out_weeks"] < line)
    overstated = (stocked["wos"] < line) & (stocked["run_out_weeks"] >= line)
    print(f"short line {line}: WOS says fine but runs out sooner {hidden.sum()} "
          f"{stocked.loc[hidden, 'location'].value_counts().to_dict()}, WOS says short but lasts longer {overstated.sum()}")
hidden = stocked[(stocked["wos"] >= 4) & (stocked["run_out_weeks"] < 4)]
print(hidden[["part_number", "location", "on_hand_qty", "wos", "run_out_weeks"]].round(1).to_string(index=False))
