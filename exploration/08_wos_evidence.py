"""Evidence for the WOS method (ASSUMPTIONS.md 5.2): how many months to average, how to turn
months into weeks, and how the GDC's lumpy restock months affect the answer.

Uses the master table from src/master_table.py, so demand per site already follows ASSUMPTIONS.md 5.1
and the cleaning rules in section 4 are already applied. The yardstick is the run-out time: walk
the forecast forward from the snapshot one month at a time until today's stock is used up. The
10 days before November have no forecast, so customers buy at November's daily rate, and the
GDC's planned restock to the PDCs starts in November with the plan (the walk in src/weeks_of_supply.py).

Read-only check. Run from the repo root: .venv\\Scripts\\python.exe exploration\\08_wos_evidence.py
"""
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `src` imports work when run as a plain script

from src.clean import clean_all
from src.config import FORECAST_MONTHS, SNAPSHOT_DATE
from src.load_raw import read_all_raw
from src.master_table import build_master
from src.standardize import standardize_all
from src.weeks_of_supply import weeks_until_run_out

pd.set_option("display.width", 200)
WINDOWS = [3, 6, 12, 24]
MONTH_DAYS = pd.Series([pd.Period(month).days_in_month for month in FORECAST_MONTHS], index=FORECAST_MONTHS)
GAP_DAYS = (pd.Timestamp(FORECAST_MONTHS[0]) - SNAPSHOT_DATE).days  # snapshot to first forecast month

clean_tables, flag_list = clean_all(standardize_all(read_all_raw()))
master, set_aside_flags = build_master(clean_tables)
with_demand = master[master["demand_24m"] > 0].copy()
print("\npairs with demand:", len(with_demand), "| of those with stock above 0:", (with_demand["on_hand_qty"] > 0).sum())


def run_out_weeks(pairs: pd.DataFrame) -> list[float]:
    """Weeks from the snapshot until today's stock is used up, walking forward one month at a time."""
    gap_demand = pairs["direct_first_month"] * GAP_DAYS / MONTH_DAYS.iloc[0]
    return [weeks_until_run_out(on_hand, gap, list(months))
            for on_hand, gap, months in zip(pairs["on_hand_qty"], gap_demand, pairs[FORECAST_MONTHS].to_numpy())]


def average_wos(pairs: pd.DataFrame, months: int) -> pd.Series:
    """On-hand divided by the average weekly demand over the next `months` months, in calendar days."""
    window = FORECAST_MONTHS[:months]
    weekly_demand = pairs[window].sum(axis=1) / (MONTH_DAYS[window].sum() / 7)
    return pairs["on_hand_qty"].clip(lower=0) / weekly_demand


print("\n1. Does demand change over the 24 months? Demand per 3-month block, as a share of Nov to Jan")
blocks = {}
for start in range(0, 24, 3):
    block_months = FORECAST_MONTHS[start:start + 3]
    blocks[block_months[0]] = with_demand.groupby("location")[block_months].sum().sum(axis=1)
blocks = pd.DataFrame(blocks)
print(blocks.div(blocks.iloc[:, 0], axis=0).round(2).to_string())
first_3m = with_demand[FORECAST_MONTHS[:3]].sum(axis=1) / 3
all_24m = with_demand["demand_24m"] / 24
print("per pair, 24-month monthly average / Nov-Jan monthly average, quantiles:",
      (all_24m / first_3m).quantile([0.1, 0.25, 0.5, 0.75, 0.9]).round(2).to_dict())
print("pairs with no demand in the first N months but demand later:",
      {months: int((with_demand[FORECAST_MONTHS[:months]].sum(axis=1) == 0).sum()) for months in WINDOWS})
filled = flag_list[flag_list["rule"].isin(["4.7", "4.8"])]
filled_month = filled["record"].str.extract(r"(\d{4}-\d{2})$")[0]
print("filled forecast cells (4.7, 4.8) inside the first N months:",
      {months: int(filled_month.isin(FORECAST_MONTHS[:months]).sum()) for months in WINDOWS})

print("\n2. How far ahead does today's stock reach? Run-out weeks from the snapshot")
with_demand["run_out_weeks"] = run_out_weeks(with_demand)
bands = pd.cut(with_demand["run_out_weeks"], [-0.1, 0, 4, 13, 26, 52, 104, math.inf],
               labels=["0 (no stock)", "up to 4", "4 to 13", "13 to 26", "26 to 52", "52 to 104", "beyond 24 months"])
print(pd.crosstab(bands, with_demand["location"], margins=True).to_string())

print("\n3. Average WOS for each window next to the run-out weeks (pairs with stock above 0)")
stocked = with_demand[with_demand["on_hand_qty"] > 0].copy()
summary = {"run-out weeks": stocked.groupby("location")["run_out_weeks"].median()}
for months in WINDOWS:
    stocked[f"wos_{months}m"] = average_wos(stocked, months)
    summary[f"median WOS, {months}m"] = stocked.groupby("location")[f"wos_{months}m"].median()
print(pd.DataFrame(summary).round(1).to_string())
print("pairs whose stock runs out inside 24 months:", (stocked["run_out_weeks"] < math.inf).sum(), "of", len(stocked))
for months in WINDOWS:
    gap = (stocked[f"wos_{months}m"] - stocked["run_out_weeks"]).abs()
    close = (gap <= 2) | (gap <= 0.2 * stocked["run_out_weeks"])
    print(f"  {months}m window: within 2 weeks or 20% of run-out weeks for {close.sum()} of {len(stocked)}, "
          f"by location {close.groupby(stocked['location']).mean().round(2).to_dict()}")

print("\n4. Months to weeks")
nov_to_jan_days = MONTH_DAYS[FORECAST_MONTHS[:3]].sum()
print(f"Nov to Jan: {nov_to_jan_days} days = {nov_to_jan_days / 7:.3f} weeks | 3 x 52/12 = 13.000 | 3 x 4 = 12"
      f" | calendar / 52-12 = {nov_to_jan_days / 7 / 13:.4f} | 4-week / calendar = {12 / (nov_to_jan_days / 7):.3f}")
print(f"snapshot {SNAPSHOT_DATE.date()} to first forecast month: {GAP_DAYS} days, no forecast covers them")
nov_daily = stocked["direct_first_month"] / MONTH_DAYS.iloc[0]
print("pairs with stock above 0 that run out inside those days at November's customer rate:",
      (stocked["on_hand_qty"] < nov_daily * GAP_DAYS).sum())
supply = clean_tables["supply"]
print("supply ETAs:", supply["eta_date"].min().date(), "to", supply["eta_date"].max().date(),
      "| lines landing before the first forecast month:", (supply["eta_date"] < FORECAST_MONTHS[0]).sum(), "of", len(supply))

print("\n5. How lumpy is the GDC? (restock months come in whole pallets)")
demand = clean_tables["demand"]
restock = demand[demand["demand_type"] == "Replenishment"]
print("restock rows:", len(restock), "| zero cells:", f"{(restock[FORECAST_MONTHS] == 0).mean().mean():.1%}",
      "| zero cells in Nov to Jan:", f"{(restock[FORECAST_MONTHS[:3]] == 0).mean().mean():.1%}",
      "| rows with no restock in Nov to Jan:", int((restock[FORECAST_MONTHS[:3]].sum(axis=1) == 0).sum()))
gdc = with_demand[with_demand["location"] == "GDC"]
# GDC demand is its own Direct plus all restock rows (5.1), which smooths out most of the empty restock months
print("GDC pairs:", len(gdc), "| zero months once combined:", f"{(gdc[FORECAST_MONTHS] == 0).mean().mean():.1%}",
      "| restock share of GDC demand, quantiles:",
      (gdc["restock_out_24m"] / gdc["demand_24m"]).quantile([0.1, 0.25, 0.5, 0.75, 0.9]).round(2).to_dict())
# month-to-month swing: standard deviation of the 24 months as a share of the average month
swing = with_demand[FORECAST_MONTHS].std(axis=1) / with_demand[FORECAST_MONTHS].mean(axis=1)
print("month-to-month swing (std / mean of the 24 months), median by location:",
      swing.groupby(with_demand["location"]).median().round(2).to_dict())
gdc_stocked = stocked[stocked["location"] == "GDC"]
print("GDC, 3-month WOS / 12-month WOS, quantiles:",
      (gdc_stocked["wos_3m"] / gdc_stocked["wos_12m"]).quantile([0.1, 0.25, 0.5, 0.75, 0.9]).round(2).to_dict())
example_columns = ["part_number", "location", "on_hand_qty", "direct_24m", "restock_out_24m",
                   "wos_3m", "wos_6m", "wos_12m", "run_out_weeks"]
for part in ["SKU6", "SKU61"]:
    example = stocked[(stocked["part_number"] == part) & (stocked["location"] == "GDC")].iloc[0]
    run_out_date = SNAPSHOT_DATE + pd.Timedelta(weeks=example["run_out_weeks"])
    print(f"\n{part} at the GDC, run-out date {run_out_date.date()}:")
    print(example[example_columns].to_string())
    print("first 6 months (Direct + restock):", example[FORECAST_MONTHS[:6]].astype(float).tolist())
    print(demand.loc[(demand["part_number"] == part) & (demand["location"] == "GDC"),
                     ["demand_type", "ship_to"] + FORECAST_MONTHS[:6]].to_string(index=False))

print("\n6. Would comparing the 3-month and 6-month demand rates catch the pairs where WOS and run-out disagree?")
rate_change = (stocked["wos_3m"] / stocked["wos_6m"] - 1).abs()  # same as the change in weekly demand
flag_6m = rate_change > 0.25
gap = (stocked["wos_3m"] - stocked["run_out_weeks"]).abs()
far_off = (gap > 2) & (gap > 0.2 * stocked["run_out_weeks"])
print(pd.crosstab(flag_6m.rename("3m vs 6m rate differs > 25%"), far_off.rename("3m WOS far from run-out"),
                  margins=True).to_string())
