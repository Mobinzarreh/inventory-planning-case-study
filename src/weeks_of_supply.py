"""Weeks of supply (WOS) for today's on-hand stock, as defined in ASSUMPTIONS.md 5.2."""
import math

import pandas as pd

from src.clean import clean_all
from src.config import FORECAST_MONTHS, SNAPSHOT_DATE
from src.load_raw import read_all_raw
from src.master_table import build_master
from src.standardize import standardize_all

MONTH_DAYS = [pd.Period(month).days_in_month for month in FORECAST_MONTHS]
GAP_DAYS = (pd.Timestamp(FORECAST_MONTHS[0]) - SNAPSHOT_DATE).days  # the 10 days before the forecast starts
WOS_MONTHS = FORECAST_MONTHS[:3]  # Nov to Jan: the demand today's stock will actually meet
WOS_WEEKS = sum(MONTH_DAYS[:3]) / 7  # 92 calendar days = 13.14 weeks


def weeks_until_run_out(on_hand: float, gap_demand: float, monthly_demand: list[float]) -> float:
    """Weeks from the snapshot until a whole piece of demand goes unmet: the days before November, then month by month."""
    if on_hand <= 0:
        return 0.0
    periods = zip([GAP_DAYS] + MONTH_DAYS, [gap_demand] + list(monthly_demand))
    # pieces are whole, as in the 5.5 projection: the stock runs out once demand so far, rounded, passes it
    stock_left = on_hand + 0.5
    days_so_far = 0
    for days, demand in periods:
        if demand >= stock_left:  # runs out part way through this period
            return (days_so_far + days * stock_left / demand) / 7
        stock_left -= math.floor(demand + 0.5)  # each period uses whole pieces, like the projection
        days_so_far += days
    return math.inf  # lasts beyond the 24-month forecast


def add_wos(master: pd.DataFrame) -> pd.DataFrame:
    """WOS = on-hand / average weekly demand over Nov to Jan. 0 with no stock, blank with no demand."""
    master = master.copy()
    master["demand_3m"] = master[WOS_MONTHS].sum(axis=1)
    master["weekly_demand"] = master["demand_3m"] / WOS_WEEKS
    # no forecast covers the days before November: customers buy at November's rate, but the restock plan
    # only starts in November, so the GDC's planned shipments to the PDCs are left out of these days (5.2)
    master["gap_demand"] = master["direct_first_month"] * GAP_DAYS / MONTH_DAYS[0]
    has_demand = master["demand_24m"] > 0
    late_start = has_demand & (master["demand_3m"] == 0)
    if late_start.any():
        raise ValueError(f"{late_start.sum()} pairs have demand later but none in Nov to Jan, so WOS needs a rule for them")
    # 4.6: negative on-hand is a backorder, so there is no stock left to cover demand
    stock = master["on_hand_qty"].clip(lower=0)
    master["wos"] = (stock / master["weekly_demand"]).where(has_demand)
    no_stock = has_demand & (stock == 0)
    print(f"  WOS: {has_demand.sum()} pairs with demand ({no_stock.sum()} of them at 0 for no stock), "
          f"{(~has_demand).sum()} with no demand left blank")
    return master


if __name__ == "__main__":
    clean_tables, flag_list = clean_all(standardize_all(read_all_raw()))
    master, set_aside_flags = build_master(clean_tables)
    master = add_wos(master)
    print("\nWOS by location (pairs with demand):")
    print(master.groupby("location")["wos"].describe(percentiles=[0.25, 0.5, 0.75]).round(1).to_string())
    check_columns = ["part_number", "location", "on_hand_qty", "demand_3m", "weekly_demand", "wos"]
    hand_checks = [("SKU82", None), ("SKU6", "GDC"), ("SKU92", "PDC2"), ("SKU1", "PDC2"), ("SKU16", "PDC3")]
    print("\nhand checks:")
    for part, location in hand_checks:
        rows = master[(master["part_number"] == part) & ((master["location"] == location) | (location is None))]
        print(rows[check_columns].round(2).to_string(index=False, header=part == "SKU82"))
    print("\nrows:", len(master), "| on-hand total:", master["on_hand_qty"].sum())
