"""Evidence for the incoming supply review (task 4): a dated projection of stock from the snapshot
to the end of January, with each delivery landing on its ETA.

Projected stock on a day = on-hand + deliveries so far - demand so far, all in whole pieces. Customers
buy every day: each month spread evenly over its days, the running total rounded to whole pieces. In the
10 days before November they buy at November's daily rate (5.2). Planned restock is one shipment on the
1st of each month, and the plan only starts in November, so no restock moves before then. Stock may go
below zero, as a backorder (4.6). A day that ends below zero is a day without stock.

The GDC's planned restock to the PDCs is part of the GDC's demand (5.1). The GDC fills all its orders
in date order, its own customers and the PDCs alike. So a PDC gets its restock only while the GDC has
stock, and restock the GDC cannot fill waits until supply lands there. Restock counts at the PDC on the
day it leaves the GDC, so every piece that leaves the GDC lands at a PDC. For comparison, the PDCs are
also shown with no restock and with the full plan.

Read-only check. Run from the repo root: .venv\\Scripts\\python.exe exploration\\11_supply_review_evidence.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `src` imports work when run as a plain script

from src.clean import clean_all
from src.config import FORECAST_MONTHS, LOCATIONS, SNAPSHOT_DATE
from src.load_raw import read_all_raw
from src.master import build_master
from src.standardize import standardize_all
from src.status import EXCESS_WEEKS, SHORT_WEEKS, STATUS_ORDER, WANTED_ELSEWHERE, add_status
from src.wos import add_wos

pd.set_option("display.width", 220)
pd.set_option("display.max_colwidth", 60)
END_DATE = pd.Timestamp("2027-01-31")
DAYS = pd.date_range(SNAPSHOT_DATE, END_DATE)
PROJECTION_MONTHS = FORECAST_MONTHS[:3]
# each day reads its own month's forecast; the days before November read November
DAY_MONTH = DAYS.strftime("%Y-%m").where(DAYS >= PROJECTION_MONTHS[0], PROJECTION_MONTHS[0])
MONTH_DAYS = pd.Series([pd.Period(month).days_in_month for month in PROJECTION_MONTHS], index=PROJECTION_MONTHS)
BEFORE_PLAN = DAYS < PROJECTION_MONTHS[0]
FIRST_DAYS = [DAYS.get_loc(pd.Timestamp(month)) for month in PROJECTION_MONTHS]

clean_tables, flag_list = clean_all(standardize_all(read_all_raw()))
master, set_aside_flags = build_master(clean_tables)
master, timing_flags = add_status(add_wos(master))
pairs = master.reset_index(drop=True)
pairs["stock"] = pairs["on_hand_qty"].clip(lower=0)
pair_row = {key: i for i, key in enumerate(zip(pairs["part_number"], pairs["location"]))}
supply = clean_tables["supply"]
demand = clean_tables["demand"]
is_pdc = (pairs["location"] != "GDC").to_numpy()
has_demand = (pairs["demand_24m"] > 0).to_numpy()


def customer_flow(monthly: pd.DataFrame) -> np.ndarray:
    """Customers buy every day: each month spread evenly over its days, the running total rounded to whole pieces."""
    even = (monthly[PROJECTION_MONTHS] / MONTH_DAYS)[DAY_MONTH].to_numpy()
    daily = np.zeros_like(even)
    for days in [BEFORE_PLAN, ~BEFORE_PLAN]:  # late October and Nov to Jan each add up to whole pieces
        running = np.floor(even[:, days].cumsum(axis=1) + 0.5)
        daily[:, days] = np.diff(running, axis=1, prepend=0)
    return daily


def restock_shipments(monthly: pd.DataFrame) -> np.ndarray:
    """Planned restock as one shipment on the 1st of each month, from November."""
    shipments = np.zeros((len(monthly), len(DAYS)))
    shipments[:, FIRST_DAYS] = monthly[PROJECTION_MONTHS].to_numpy()
    return shipments


def demand_by_day() -> np.ndarray:
    """Pieces leaving each site per day: customers from the snapshot, restock to the PDCs from November."""
    daily = np.zeros((len(pairs), len(DAYS)))
    for demand_type, timing in [("Direct", customer_flow), ("Replenishment", restock_shipments)]:
        rows = demand[demand["demand_type"] == demand_type]
        monthly = rows.groupby(["part_number", "location"])[PROJECTION_MONTHS].sum()
        monthly = monthly[monthly.sum(axis=1) > 0]  # skips all-zero rows, including the 6 pairs set aside in 4.3
        daily[[pair_row[key] for key in monthly.index]] += timing(monthly)
    return daily


def receipts_by_day() -> np.ndarray:
    """Incoming supply landing on its ETA, one row per pair."""
    receipts = np.zeros((len(pairs), len(DAYS)))
    by_eta = supply.groupby(["part_number", "destination", "eta_date"])["qty"].sum()
    for (part, location, eta), qty in by_eta.items():
        receipts[pair_row[(part, location)], DAYS.get_loc(eta)] += qty
    return receipts


def restock_by_day() -> np.ndarray:
    """The GDC's planned restock to each PDC, one shipment on the 1st, the same days as the GDC sends it."""
    restock = demand[demand["demand_type"] == "Replenishment"]
    monthly = restock.groupby(["part_number", "ship_to"])[PROJECTION_MONTHS].sum()
    rows = [pair_row[key] for key in monthly.index]
    daily = np.zeros((len(pairs), len(DAYS)))
    daily[rows] = restock_shipments(monthly)
    return daily


def restock_shipped_by_day() -> np.ndarray:
    """Planned restock the GDC can actually ship, filling all its orders in date order."""
    shipped = np.zeros_like(restock_in)
    for i in np.flatnonzero(restock_in.sum(axis=1)):
        gdc = pair_row[(pairs.loc[i, "part_number"], "GDC")]
        # orders filled so far: no more than the GDC has had (old backorders go first), no more than was ordered
        filled = np.clip(on_hand[gdc, 0] + cum_receipts[gdc], 0, cum_demand[gdc])
        # orders go out in date order, so how far down the GDC's order list it got says how much restock has left
        shipped_so_far = np.interp(filled, np.r_[0, cum_demand[gdc]], np.r_[0, cum_restock[i]])
        shipped[i] = np.diff(np.floor(shipped_so_far + 1e-9), prepend=0)  # whole pieces only
    return shipped


def out_of_stock(stock_path: np.ndarray) -> np.ndarray:
    """True on days that end with demand unmet (stock below zero, so at least one whole piece), for pairs with demand."""
    return (stock_path < 0) & has_demand[:, None]


def first_day_without_stock(stock_path: np.ndarray) -> pd.Series:
    """First day with demand unmet, NaT if never."""
    is_out = out_of_stock(stock_path)
    first = np.where(is_out.any(axis=1), is_out.argmax(axis=1), -1)
    return pd.Series([DAYS[i] if i >= 0 else pd.NaT for i in first])


def status_from_stock(stock: pd.Series) -> pd.Series:
    """Status with the 5.3 lines, using a projected stock instead of today's."""
    wos = (stock.clip(lower=0) / pairs["weekly_demand"]).where(pairs["demand_24m"] > 0)
    status = pd.Series("Healthy", index=pairs.index)
    status.loc[wos > EXCESS_WEEKS] = "Excess"
    status.loc[wos < SHORT_WEEKS] = "Short"
    status.loc[wos == 0] = "Stockout"
    status.loc[wos.isna()] = "No demand"
    return status


daily_demand = demand_by_day()
receipts = receipts_by_day()
restock_in = restock_by_day()
cum_demand = daily_demand.cumsum(axis=1)
cum_receipts = receipts.cumsum(axis=1)
cum_restock = restock_in.cumsum(axis=1)
on_hand = pairs["on_hand_qty"].to_numpy()[:, None]
restock_shipped = restock_shipped_by_day()
path_on_hand = on_hand - cum_demand
path_supply = on_hand + cum_receipts - cum_demand
path_full = path_supply + cum_restock
# the main view: supply plus the restock the GDC can ship (the GDC's own path is path_supply)
path_shipped = path_supply + restock_shipped.cumsum(axis=1)
pairs["out_on_hand"] = first_day_without_stock(path_on_hand)
pairs["out_with_supply"] = first_day_without_stock(path_supply)
pairs["out_with_full_plan"] = first_day_without_stock(path_full)
pairs["out_with_shipped"] = first_day_without_stock(path_shipped)
pairs["days_without_stock"] = out_of_stock(path_shipped).sum(axis=1)
pairs["worst_backorder"] = (-path_shipped.min(axis=1)).clip(min=0)
pairs["stock_jan31"] = path_shipped[:, -1]

print("\nchecks: receipts", receipts.sum(), "=", supply["qty"].sum())
after_gap = DAYS >= PROJECTION_MONTHS[0]
print("demand Nov to Jan:", round(daily_demand[:, after_gap].sum()), "=", pairs[PROJECTION_MONTHS].sum().sum(),
      "| customer demand before November:", round(daily_demand[:, ~after_gap].sum()),
      "| restock before November:", restock_in[:, ~after_gap].sum())
gdc_direct = demand[(demand["demand_type"] == "Direct") & (demand["location"] == "GDC")]
gdc_customers = customer_flow(gdc_direct).sum()
print("GDC orders: its own customers", round(gdc_customers), "+ restock to PDCs", round(restock_in.sum()),
      "=", round(gdc_customers + restock_in.sum()), "| GDC demand in the projection", round(cum_demand[~is_pdc, -1].sum()))
print("restock Nov to Jan: planned", round(restock_in.sum()), "| the GDC can ship", round(restock_shipped.sum()))

print("\n1. Where incoming supply goes, by today's status")
lines = supply.merge(pairs[["part_number", "location", "status", "no_demand_reason", "qpc"]],
                     left_on=["part_number", "destination"], right_on=["part_number", "location"], how="left")
lines["pallets"] = lines["qty"] / lines["qpc"]
print(lines.groupby("status").agg(lines=("qty", "size"), pieces=("qty", "sum"), pallets=("pallets", "sum"))
      .reindex(STATUS_ORDER).round(0).to_string())
print(pd.crosstab(lines["status"], lines["source"]).reindex(STATUS_ORDER).to_string())

print("\n2. Running out before the first delivery (on-hand only against the first ETA, pairs with demand and supply)")
pairs["first_eta"] = pd.to_datetime(pairs["first_eta"])
waiting = pairs[has_demand & (pairs["supply_qty"] > 0)].copy()
late = waiting["out_on_hand"] < waiting["first_eta"]
waiting["days_waiting"] = (waiting["first_eta"] - waiting["out_on_hand"]).dt.days.where(late)
print("pairs:", len(waiting), "| delivery lands in time:", (~late).sum(), "| stock runs out first:", late.sum(),
      waiting.loc[late, "location"].value_counts().reindex(LOCATIONS, fill_value=0).to_dict())
print("days without stock while waiting, quantiles:",
      waiting["days_waiting"].quantile([0, 0.25, 0.5, 0.75, 1]).to_dict())
columns = ["part_number", "location", "status", "on_hand_qty", "out_on_hand", "first_eta", "days_waiting",
           "supply_qty", "days_without_stock", "worst_backorder", "stock_jan31"]
print(waiting[late].sort_values("days_waiting", ascending=False)[columns].head(10).to_string(index=False, float_format="%.0f"))
ever_out = pairs["days_without_stock"] > 0
print(f"\ndays without stock, Oct 22 to Jan 31 ({len(DAYS)} days), with supply and the restock the GDC can ship:")
print("pairs out at least one day:", pairs.loc[ever_out, "location"].value_counts().reindex(LOCATIONS, fill_value=0).to_dict(),
      "| out every day:", (pairs["days_without_stock"] == len(DAYS)).sum())
print("days out, quantiles (pairs out at least one day):",
      pairs.loc[ever_out, "days_without_stock"].quantile([0, 0.25, 0.5, 0.75, 1]).to_dict())

print("\n3. Status today against status on Jan 31 (supply and the restock the GDC can ship)")
pairs["status_jan31"] = status_from_stock(pairs["stock_jan31"])
print(pd.crosstab(pairs["status"], pairs["status_jan31"]).reindex(index=STATUS_ORDER, columns=STATUS_ORDER, fill_value=0).to_string())
print("pairs with demand out of stock at some point before Jan 31, by view:")
for label, column in [("on-hand only", "out_on_hand"), ("+ supply", "out_with_supply"),
                      ("+ supply + full restock plan", "out_with_full_plan"),
                      ("+ supply + restock GDC can ship", "out_with_shipped")]:
    out = pairs[column].notna()
    print(f"  {label:32s} GDC {(out & ~is_pdc).sum():3d}, PDCs {(out & is_pdc).sum():3d}")

print("\n4. Can the GDC make its planned restock? (its demand already includes the restock, 5.1)")
gdc_out = pairs[~is_pdc & has_demand].set_index("part_number")["out_with_supply"].dropna()
print("GDC parts out of stock at some point before Jan 31, even with supply:", len(gdc_out))
# restock that left on its planned day: what has gone by the end of the day, less what was due before that day
on_time = np.clip(restock_shipped.cumsum(axis=1) - (cum_restock - restock_in), 0, restock_in).sum(axis=1)
restock = pairs.loc[is_pdc, ["part_number", "location", "status"]].copy()
restock["planned"] = restock_in.sum(axis=1)[is_pdc]
restock["on_time"] = on_time[is_pdc]
restock["shipped"] = restock_shipped.sum(axis=1)[is_pdc]
restock["late"] = restock["shipped"] - restock["on_time"]
restock["not_shipped"] = restock["planned"] - restock["shipped"]
print("restock pieces Oct 22 to Jan 31:", restock[["planned", "on_time", "late", "not_shipped"]].sum().round(0).to_dict())
hit = restock[restock["planned"] - restock["on_time"] > 0.5].copy()
hit["gdc_out_from"] = hit["part_number"].map(gdc_out).dt.date
print("PDC pairs with restock late or not shipped by Jan 31:", len(hit), "| parts:", hit["part_number"].nunique())
print(hit.sort_values("not_shipped", ascending=False).head(12).round(0).to_string(index=False))

print("\n5. Three views of the PDCs: no restock, full planned restock, the restock the GDC can ship")
pdc_demand = is_pdc & has_demand
for label, (path, out_column) in {"supply only": (path_supply, "out_with_supply"),
                                  "+ full planned restock": (path_full, "out_with_full_plan"),
                                  "+ restock the GDC can ship": (path_shipped, "out_with_shipped")}.items():
    status = status_from_stock(pd.Series(path[:, -1]))
    out = pairs[out_column].notna() & pdc_demand
    print(f"\n{label}: PDC pairs out at some point before Jan 31 {out.sum()} of {pdc_demand.sum()} "
          f"(no stock today {(out & (pairs['on_hand_qty'] <= 0)).sum()}), "
          f"out on Jan 31 {(out_of_stock(path)[:, -1] & is_pdc).sum()}")
    print("  PDC status on Jan 31:", status[is_pdc].value_counts().reindex(STATUS_ORDER, fill_value=0).to_dict())
late_pdc = pdc_demand & (pairs["supply_qty"] > 0) & (pairs["out_on_hand"] < pairs["first_eta"])
print("\nPDC pairs that run out before their first delivery (section 2):", late_pdc.sum(),
      "| still out before it with full restock:", (late_pdc & (pairs["out_with_full_plan"] < pairs["first_eta"])).sum(),
      "| with the restock the GDC can ship:", (late_pdc & (pairs["out_with_shipped"] < pairs["first_eta"])).sum())

print("\n6. Moving stock between sites: spare at one site, still short at another (supply and restock the GDC can ship)")
spare = (pairs["stock"] - EXCESS_WEEKS * pairs["weekly_demand"]).clip(lower=0).where(pairs["status"] == "Excess", 0)
spare = spare + pairs["stock"].where(pairs["no_demand_reason"] == WANTED_ELSEWHERE, 0)
need = pairs["worst_backorder"].where(has_demand, 0)
by_part = pd.DataFrame({"part_number": pairs["part_number"], "spare": spare, "need": need}).groupby("part_number").sum()
both = by_part[(by_part["spare"] > 0) & (by_part["need"] > 0)].copy()
both["movable"] = both[["spare", "need"]].min(axis=1)
print("parts with spare stock somewhere and a shortage somewhere else:", len(both),
      "| pieces that could move:", round(both["movable"].sum()))
for part in both.sort_values("movable", ascending=False).index[:6]:
    rows = pairs[(pairs["part_number"] == part) & ((spare > 0) | (need > 0))]
    print(f"  {part}: " + "; ".join(f"{loc} spare {s:.0f} need {n:.0f} ({st})" for loc, s, n, st in
                                    zip(rows["location"], spare[rows.index], need[rows.index], rows["status"])))

print("\n7. Supply that adds to excess or goes where there is no demand (Jan 31 with restock the GDC can ship)")
lines = lines.merge(pairs[["part_number", "location", "status_jan31", "stock_jan31", "weekly_demand"]],
                    on=["part_number", "location"], how="left")
to_excess = lines["status"] == "Excess"
to_no_demand = lines["status"] == "No demand"
turns_excess = (lines["status"] != "Excess") & (lines["status_jan31"] == "Excess")
for label, mask in [("to pairs already Excess", to_excess), ("to No demand pairs", to_no_demand),
                    ("tipping a pair into Excess by Jan 31", turns_excess)]:
    print(f"  {label:38s} lines {mask.sum():3d}, pieces {lines.loc[mask, 'qty'].sum():7,.0f}, "
          f"pallets {lines.loc[mask, 'pallets'].sum():6,.0f}")
excess_pairs = pairs[(pairs["status_jan31"] == "Excess") & (pairs["supply_qty"] > 0)]
above_line = (excess_pairs["stock_jan31"] - EXCESS_WEEKS * excess_pairs["weekly_demand"]).clip(lower=0)
print("pieces of supply landing above the 26-week line by Jan 31 (pairs Excess on Jan 31):",
      round(np.minimum(excess_pairs["supply_qty"], above_line).sum()))
look_alike = flag_list[flag_list["rule"] == "4.5"]
print("look-alike supply lines (4.5):", len(look_alike), "| their pairs by today's status:",
      look_alike.merge(pairs, on=["part_number", "location"])["status"].value_counts().to_dict())

print("\n8. Hand check: SKU98 at every site")
sku98 = pairs[pairs["part_number"] == "SKU98"]
print(sku98[["location", "status", "on_hand_qty", "supply_qty", "first_eta", "restock_in_24m", "out_with_supply",
             "out_with_shipped", "days_without_stock", "stock_jan31"]].to_string(index=False, float_format="%.0f"))
