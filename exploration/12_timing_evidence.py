"""Evidence for the timing rule in the supply review (5.5): customers buy every day in whole pieces, and the
planned restock arrives as one shipment on the 1st of each month.

The supply review is rerun with other ways to time the same monthly numbers, and the main results are compared:
- restock spread evenly over the days instead, like daily deliveries
- customer demand as one batch per month, on the 1st, the 15th or the last day
- the earlier version, with fractions of a piece and both flows spread evenly

Read-only check. Run from the repo root: .venv\\Scripts\\python.exe exploration\\12_timing_evidence.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `src` imports work when run as a plain script

import src.supply_review as review
from src.clean import clean_all
from src.load_raw import read_all_raw
from src.master import build_master
from src.standardize import standardize_all
from src.status import add_status
from src.wos import add_wos

pd.set_option("display.width", 220)
clean_tables, _ = clean_all(standardize_all(read_all_raw()))
master, _ = build_master(clean_tables)
master, _ = add_status(add_wos(master))
CHOSEN = {"customers": review.customer_flow, "restock": review.restock_shipments,
          "received": review.restock_received_so_far}
LATE_OCTOBER = int(review.BEFORE_PLAN.sum())  # the 10 days before November


def even_over_days(monthly: pd.DataFrame) -> np.ndarray:
    """Each month spread evenly over its days, in fractions of a piece. The days before November read November."""
    return (monthly[review.PROJECTION_MONTHS] / review.MONTH_DAYS)[review.DAY_MONTH].to_numpy()


def restock_even_whole(monthly: pd.DataFrame) -> np.ndarray:
    """Restock spread evenly over the days in whole pieces, nothing before November (like daily deliveries)."""
    even = even_over_days(monthly)
    even[:, review.BEFORE_PLAN] = 0
    running = np.floor(even.cumsum(axis=1) + 0.5)
    return np.diff(running, axis=1, prepend=0)


def customer_batches(where: str):
    """Customer demand as one batch per month on the 1st, the 15th or the last day. Late October gets its own batch."""
    months = [pd.Period(month) for month in review.PROJECTION_MONTHS]
    days = {"1st": [m.start_time for m in months], "15th": [m.start_time + pd.Timedelta(days=14) for m in months],
            "last day": [m.end_time.normalize() for m in months]}[where]
    late_october_day = {"1st": 0, "15th": LATE_OCTOBER // 2, "last day": LATE_OCTOBER - 1}[where]

    def timing(monthly: pd.DataFrame) -> np.ndarray:
        batches = np.zeros((len(monthly), len(review.DAYS)))
        batches[:, [review.DAYS.get_loc(day) for day in days]] = monthly[review.PROJECTION_MONTHS].to_numpy()
        first_month = monthly[review.PROJECTION_MONTHS[0]].to_numpy()
        batches[:, late_october_day] = np.floor(first_month * LATE_OCTOBER / review.MONTH_DAYS.iloc[0] + 0.5)
        return batches
    return timing


def restock_even_fractional(monthly: pd.DataFrame) -> np.ndarray:
    """The earlier restock timing: spread evenly in fractions of a piece, nothing before November."""
    even = even_over_days(monthly)
    even[:, review.BEFORE_PLAN] = 0
    return even


def received_fractional(master: pd.DataFrame, flows: dict[str, np.ndarray]) -> np.ndarray:
    """The same as restock_received_so_far, but without rounding down to whole pieces (the earlier version)."""
    gdc = master[master["location"] == "GDC"]
    gdc_row = dict(zip(gdc["part_number"], gdc.index))
    gdc_orders = (flows["customers"] + flows["restock_out"]).cumsum(axis=1)
    gdc_supply = flows["receipts"].cumsum(axis=1)
    planned = flows["restock_in"].cumsum(axis=1)
    received = np.zeros_like(planned)
    for i in np.flatnonzero(planned[:, -1]):
        gdc_index = gdc_row[master.loc[i, "part_number"]]
        filled = np.clip(master.loc[gdc_index, "on_hand_qty"] + gdc_supply[gdc_index], 0, gdc_orders[gdc_index])
        received[i] = np.interp(filled, np.r_[0, gdc_orders[gdc_index]], np.r_[0, planned[i]])
    return received


def run_out(result: pd.DataFrame, part: str, site: str) -> str:
    """The run-out date of one pair, or 'never'."""
    date = result.loc[(result["part_number"] == part) & (result["location"] == site), "run_out_date"].iloc[0]
    return str(date.date()) if pd.notna(date) else "never"


def results_with(customers, restock, received) -> dict:
    """Rerun the supply review with these timing functions and collect the main results."""
    review.customer_flow, review.restock_shipments, review.restock_received_so_far = customers, restock, received
    result, tables, flags = review.review_supply(master, clean_tables)
    lines, out = tables["supply_lines"], result["run_out_date"].notna()
    return {
        "pairs that run out by Jan 31": int(out.sum()),
        "  of them on the 1st of a month": int((result.loc[out, "run_out_date"].dt.day == 1).sum()),
        "pair-days without stock": int(result["days_without_stock"].sum()),
        "PO lines that land too late": int((lines["review"] == "lands too late").sum()),
        "Stockout on Jan 31": int((result["status_jan31"] == "Stockout").sum()),
        "restock the GDC can send": round(float((result["restock_planned_3m"] - result["restock_not_received"]).sum())),
        "SKU75 at the GDC runs out": run_out(result, "SKU75", "GDC"),
        "SKU29 at PDC2 runs out": run_out(result, "SKU29", "PDC2"),
        "stock values that are not whole": int((tables["daily_stock"]["projected_stock"] % 1 != 0).sum()),
    }


variants = {
    "chosen": (CHOSEN["customers"], CHOSEN["restock"], CHOSEN["received"]),
    "restock spread evenly": (CHOSEN["customers"], restock_even_whole, CHOSEN["received"]),
    "customers on the 1st": (customer_batches("1st"), CHOSEN["restock"], CHOSEN["received"]),
    "customers on the 15th": (customer_batches("15th"), CHOSEN["restock"], CHOSEN["received"]),
    "customers on the last day": (customer_batches("last day"), CHOSEN["restock"], CHOSEN["received"]),
    "earlier: fractions, both even": (even_over_days, restock_even_fractional, received_fractional),
}
table = pd.DataFrame({name: results_with(*functions) for name, functions in variants.items()})
review.customer_flow, review.restock_shipments, review.restock_received_so_far = CHOSEN.values()
print("\nThe same monthly numbers, timed in different ways (supply review rerun for each):")
print(table.to_string())
