"""Incoming supply review: a dated projection of stock from the snapshot to Jan 31, and a verdict on
every open PO line, as defined in ASSUMPTIONS.md 5.5.

Projected stock on a day = on-hand + deliveries so far - demand so far, all in whole pieces. Customers
buy every day, planned restock goes out as one shipment on the 1st of each month, and supply lands on
its ETA. The arrays below have one row per part and location (master table order) and one column per
day, Oct 22 to Jan 31.
"""
import math

import numpy as np
import pandas as pd

from src.clean import FLAG_COLUMNS, clean_all
from src.config import FORECAST_MONTHS, SNAPSHOT_DATE
from src.load_raw import read_all_raw
from src.master_table import build_master
from src.standardize import standardize_all
from src.status import DEAD_STOCK, EXCESS_WEEKS, UNKNOWN_PART, WANTED_ELSEWHERE, add_status, label_status
from src.weeks_of_supply import add_wos

END_DATE = pd.Timestamp("2027-01-31")  # all open supply lands by Jan 12, and it is the same three months as WOS (5.2)
DAYS = pd.date_range(SNAPSHOT_DATE, END_DATE)
PROJECTION_MONTHS = FORECAST_MONTHS[:3]
BEFORE_PLAN = DAYS < PROJECTION_MONTHS[0]  # Oct 22 to 31: no forecast and no restock plan cover these days
DAY_MONTH = DAYS.strftime("%Y-%m").where(~BEFORE_PLAN, PROJECTION_MONTHS[0])  # those days read November
MONTH_DAYS = pd.Series([pd.Period(month).days_in_month for month in PROJECTION_MONTHS], index=PROJECTION_MONTHS)
FIRST_DAYS = [DAYS.get_loc(pd.Timestamp(month)) for month in PROJECTION_MONTHS]  # Nov 1, Dec 1, Jan 1
ACTIONS = {
    "lands too late": "expedite the PO",
    "adds to excess": "push out or cut the pieces above the 26-week line",
    "no demand anywhere": "cancel or return: no demand anywhere in the network",
    "no demand here": "redirect to a site with demand",
    "not in the part master": "check the part number before it lands (4.10)",
    "on time": "keep",
}
MOVE_COLUMNS = ["part_number", "from_site", "to_site", "pieces", "pallets", "needed_from", "route"]


def customer_flow(monthly: pd.DataFrame) -> np.ndarray:
    """Customer demand as a daily flow in whole pieces: each month spread evenly over its days, the running
    total rounded to whole pieces (half a piece rounds up). The days before November buy at November's rate."""
    even = (monthly[PROJECTION_MONTHS] / MONTH_DAYS)[DAY_MONTH].to_numpy()
    daily = np.zeros_like(even)
    # late October and Nov to Jan each add up to whole pieces, so Nov to Jan matches the forecast exactly
    for days in [BEFORE_PLAN, ~BEFORE_PLAN]:
        running = np.floor(even[:, days].cumsum(axis=1) + 0.5)
        daily[:, days] = np.diff(running, axis=1, prepend=0)
    return daily


def restock_shipments(monthly: pd.DataFrame) -> np.ndarray:
    """Planned restock as one shipment on the 1st of each month. The plan has no rows before November."""
    shipments = np.zeros((len(monthly), len(DAYS)))
    shipments[:, FIRST_DAYS] = monthly[PROJECTION_MONTHS].to_numpy()  # whole pallets in this data (section 1)
    return shipments


def flows_by_day(master: pd.DataFrame, demand: pd.DataFrame, supply: pd.DataFrame) -> dict[str, np.ndarray]:
    """Pieces per pair and day: sold to customers, restock sent (GDC), restock planned in (PDCs), supply landing."""
    pair_row = {pair: i for i, pair in enumerate(zip(master["part_number"], master["location"]))}
    flows = {name: np.zeros((len(master), len(DAYS))) for name in ["customers", "restock_out", "restock_in", "receipts"]}
    is_direct = demand["demand_type"] == "Direct"
    # customers buy every day, from the snapshot at November's rate (5.2); restock is a monthly shipment from November
    plan = [("customers", demand[is_direct], "location", customer_flow),
            ("restock_out", demand[~is_direct], "location", restock_shipments),
            ("restock_in", demand[~is_direct], "ship_to", restock_shipments)]
    for name, rows, site_column, timing in plan:
        monthly = rows.groupby(["part_number", site_column])[PROJECTION_MONTHS].sum()
        monthly = monthly[monthly.sum(axis=1) > 0]  # skips all-zero rows, including the 6 pairs set aside in 4.3
        flows[name][[pair_row[pair] for pair in monthly.index]] = timing(monthly)
    by_eta = supply.groupby(["part_number", "destination", "eta_date"])["qty"].sum()
    for (part, location, eta), qty in by_eta.items():
        flows["receipts"][pair_row[(part, location)], DAYS.get_loc(eta)] += qty
    return flows


def check_flows(master: pd.DataFrame, flows: dict[str, np.ndarray], supply: pd.DataFrame) -> None:
    """Stop if a delivery or a forecast month went missing when spreading over days."""
    sent_out = flows["customers"] + flows["restock_out"]
    checks = {
        "supply landing": (flows["receipts"].sum(), supply["qty"].sum()),
        "demand Nov to Jan": (sent_out[:, ~BEFORE_PLAN].sum(), master[PROJECTION_MONTHS].sum().sum()),
        "restock sent = restock planned in": (flows["restock_out"].sum(), flows["restock_in"].sum()),
    }
    for name, (got, expected) in checks.items():
        if not math.isclose(got, expected):
            raise ValueError(f"projection: {name} adds up to {got}, expected {expected}")
    print(f"  projection: {len(master)} pairs over {len(DAYS)} days, supply, demand and restock totals match")


def restock_received_so_far(master: pd.DataFrame, flows: dict[str, np.ndarray]) -> np.ndarray:
    """Planned restock each PDC has received so far. The GDC fills all its orders in date order, its own
    customers and the PDCs alike, so restock goes out only while the GDC has stock and the rest waits."""
    gdc = master[master["location"] == "GDC"]
    gdc_row = dict(zip(gdc["part_number"], gdc.index))
    gdc_orders = (flows["customers"] + flows["restock_out"]).cumsum(axis=1)
    gdc_supply = flows["receipts"].cumsum(axis=1)
    planned = flows["restock_in"].cumsum(axis=1)
    received = np.zeros_like(planned)
    for i in np.flatnonzero(planned[:, -1]):
        gdc_index = gdc_row[master.loc[i, "part_number"]]
        # orders filled so far: no more than the GDC has had (old backorders first, 4.6), no more than was ordered
        filled = np.clip(master.loc[gdc_index, "on_hand_qty"] + gdc_supply[gdc_index], 0, gdc_orders[gdc_index])
        # orders go out in date order, so how far down its order list the GDC has got says how much restock has gone.
        # Orders due the same day share what the GDC has, and it sends whole pieces only
        share = np.interp(filled, np.r_[0, gdc_orders[gdc_index]], np.r_[0, planned[i]])
        received[i] = np.floor(share + 1e-9)
    return received


def project_stock(master: pd.DataFrame, flows: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Projected stock per pair and day (below zero is a backorder, 4.6), plus restock planned and received so far."""
    restock_planned = flows["restock_in"].cumsum(axis=1)
    restock_received = restock_received_so_far(master, flows)
    pieces_in = flows["receipts"].cumsum(axis=1) + restock_received
    # at the GDC this counts every order, so stock below zero there is orders waiting, restock included
    pieces_out = (flows["customers"] + flows["restock_out"]).cumsum(axis=1)
    stock = master["on_hand_qty"].to_numpy()[:, None] + pieces_in - pieces_out
    return stock, restock_planned, restock_received


def daily_stock_table(master: pd.DataFrame, stock: np.ndarray) -> pd.DataFrame:
    """The projection itself, one row per part, location and day, so the dashboard can draw it."""
    pairs = pd.MultiIndex.from_frame(master[["part_number", "location"]])
    by_day = pd.DataFrame(stock, index=pairs, columns=pd.Index(DAYS, name="date"))
    daily = by_day.stack().rename("projected_stock").reset_index()  # pair by pair, each from Oct 22 to Jan 31
    if len(daily) != len(master) * len(DAYS):
        raise ValueError("daily stock: expected one row per pair and day")
    return daily


def add_projection_columns(master: pd.DataFrame, stock: np.ndarray, restock_planned: np.ndarray,
                           restock_received: np.ndarray) -> pd.DataFrame:
    """Run-out date, days without stock, worst backorder, restock not received, and stock, WOS and status on Jan 31."""
    master = master.copy()
    has_demand = (master["demand_24m"] > 0).to_numpy()
    # stock is in whole pieces, so a day that ends below zero left at least one piece of demand unmet
    out = (stock < 0) & has_demand[:, None]
    master["run_out_date"] = pd.Series(DAYS[out.argmax(axis=1)]).where(out.any(axis=1))
    master["days_without_stock"] = out.sum(axis=1)
    master["worst_backorder"] = (-stock.min(axis=1)).clip(min=0)
    master["restock_planned_3m"] = restock_planned[:, -1]
    master["restock_not_received"] = (restock_planned[:, -1] - restock_received[:, -1]).clip(min=0)
    master["stock_jan31"] = stock[:, -1]
    master["wos_jan31"] = (master["stock_jan31"].clip(lower=0) / master["weekly_demand"]).where(master["demand_24m"] > 0)
    master["status_jan31"] = label_status(master[["wos_jan31"]].rename(columns={"wos_jan31": "wos"}))
    return master


def pieces_above_line(lines: pd.DataFrame) -> pd.Series:
    """Pieces of each line that sit above the 26-week line on Jan 31. The latest arrivals count as the extra ones."""
    is_excess = lines["status_jan31"] == "Excess"
    room_over = (lines["stock_jan31"] - EXCESS_WEEKS * lines["weekly_demand"]).clip(lower=0).where(is_excess, 0)
    latest_first = lines.sort_values("eta_date", ascending=False)
    landing_after = latest_first.groupby(["part_number", "destination"])["qty"].cumsum() - latest_first["qty"]
    above = (room_over - landing_after).clip(lower=0).clip(upper=lines["qty"])
    return np.floor(above)  # whole pieces: part of a piece above the line can't be pushed out


def label_lines(lines: pd.DataFrame) -> pd.Series:
    """One verdict per PO line. Later rules win, so a line to a part nobody wants is never called on time."""
    review = pd.Series("on time", index=lines.index)
    review.loc[lines["pieces_above_26w"] > 0] = "adds to excess"
    review.loc[lines["short_when_landing"] > 0] = "lands too late"
    review.loc[lines["no_demand_reason"] == WANTED_ELSEWHERE] = "no demand here"
    review.loc[lines["no_demand_reason"] == DEAD_STOCK] = "no demand anywhere"
    review.loc[lines["no_demand_reason"] == UNKNOWN_PART] = "not in the part master"
    return review


def review_supply_lines(master: pd.DataFrame, supply: pd.DataFrame, stock: np.ndarray) -> pd.DataFrame:
    """One row per open PO line: how it fits its part and site, and what the planner should do (5.5)."""
    columns = ["part_number", "location", "status", "no_demand_reason", "run_out_date", "status_jan31",
               "stock_jan31", "weekly_demand", "qpc"]
    lines = supply.merge(master[columns].assign(row=master.index), left_on=["part_number", "destination"],
                         right_on=["part_number", "location"], how="left", validate="many_to_one").drop(columns="location")
    lines["pieces_above_26w"] = pieces_above_line(lines)
    # stock at the start of each day, so a line landing on a day sees the shortage built up before it
    start_of_day = np.c_[master["on_hand_qty"].to_numpy(), stock[:, :-1]]
    eta_day = [DAYS.get_loc(eta) for eta in lines["eta_date"]]
    lines["short_when_landing"] = [max(0.0, -start_of_day[row, day]) for row, day in zip(lines["row"], eta_day)]
    lines["days_without_stock_before"] = [int((stock[row, :day] < 0).sum()) for row, day in zip(lines["row"], eta_day)]
    lines["review"] = label_lines(lines)
    lines["action"] = lines["review"].map(ACTIONS)
    # a GDC transfer is already on the road: a late one is bridged from another site, an extra one is
    # taken off the next planned restock instead
    is_transfer = lines["source"] == "GDC Transfer"
    lines.loc[is_transfer & (lines["review"] == "lands too late"), "action"] = "already on the road: bridge from another site"
    lines.loc[is_transfer & (lines["review"] == "adds to excess"), "action"] = "already on the road: cut the next planned restock"
    lines["pallets"] = lines["qty"] / lines["qpc"]
    return lines.drop(columns=["weekly_demand", "row"])


def gdc_shortfalls(master: pd.DataFrame, lines: pd.DataFrame) -> pd.DataFrame:
    """GDC parts that run out before Jan 31 even with open supply, and the PDC restock they cannot send (5.5)."""
    gdc = master[(master["location"] == "GDC") & master["run_out_date"].notna()].copy()
    not_sent = master.groupby("part_number")["restock_not_received"].sum()
    gdc["pdc_restock_not_sent"] = gdc["part_number"].map(not_sent)
    late_po = lines[(lines["destination"] == "GDC") & (lines["eta_date"] >= lines["run_out_date"])]
    gdc["next_po_eta"] = gdc["part_number"].map(late_po.groupby("part_number")["eta_date"].min())
    gdc["action"] = np.where(gdc["next_po_eta"].notna(), "expedite the open PO and check it covers the gap",
                             "no PO lands after the run-out: order now")
    gdc["pallets_short"] = gdc["worst_backorder"] / gdc["qpc"]
    columns = ["part_number", "status", "on_hand_qty", "run_out_date", "next_po_eta", "worst_backorder",
               "pallets_short", "pdc_restock_not_sent", "stock_jan31", "action"]
    return gdc.sort_values("worst_backorder", ascending=False)[columns].reset_index(drop=True)


def need_by_pair(master: pd.DataFrame, stock: np.ndarray, restock_owed: np.ndarray) -> pd.Series:
    """Most pieces each site is short at one time. At the GDC only its own customers count, because the
    restock it cannot send already shows up as a shortage at the PDCs."""
    owed_by_part = pd.DataFrame(restock_owed).groupby(master["part_number"]).sum()
    waiting = (-stock).clip(min=0)
    is_gdc = (master["location"] == "GDC").to_numpy()
    waiting[is_gdc] -= owed_by_part.loc[master.loc[is_gdc, "part_number"]].to_numpy()
    need = pd.Series(waiting.max(axis=1), index=master.index).clip(lower=0)
    return need.where(master["demand_24m"] > 0, 0)


def spare_by_pair(master: pd.DataFrame) -> pd.Series:
    """Stock a site could give away: above 26 weeks of its demand, or all of it where only other sites have demand."""
    stock = master["on_hand_qty"].clip(lower=0)
    above_line = (stock - EXCESS_WEEKS * master["weekly_demand"]).clip(lower=0).where(master["status"] == "Excess", 0)
    return above_line + stock.where(master["no_demand_reason"] == WANTED_ELSEWHERE, 0)


def suggest_moves(master: pd.DataFrame, need: pd.Series) -> pd.DataFrame:
    """Match spare stock to shortages of the same part, biggest first. GDC to PDC is the usual route."""
    sites = master[["part_number", "location", "qpc", "run_out_date"]].assign(spare=spare_by_pair(master), need=need)
    moves = []
    for part, rows in sites.groupby("part_number"):
        spare_left = rows[rows["spare"] >= 1].sort_values("spare", ascending=False).set_index("location")["spare"].to_dict()
        for _, taker in rows[rows["need"] >= 1].sort_values("need", ascending=False).iterrows():
            need_left = taker["need"]
            for giver in spare_left:
                pieces = math.floor(min(need_left, spare_left[giver]))
                if pieces < 1:
                    continue
                spare_left[giver] -= pieces
                need_left -= pieces
                route = "usual route" if giver == "GDC" else "check if a transfer is possible"
                moves.append({"part_number": part, "from_site": giver, "to_site": taker["location"], "pieces": pieces,
                              "pallets": pieces / taker["qpc"], "needed_from": taker["run_out_date"], "route": route})
    # the columns are named up front, so a run with no moves still gives an empty table, not a crash
    return pd.DataFrame(moves, columns=MOVE_COLUMNS).sort_values("pieces", ascending=False).reset_index(drop=True)


def flag_before_restock_plan(master: pd.DataFrame, stock: np.ndarray, restock_planned: np.ndarray) -> pd.DataFrame:
    """5.5: PDC pairs that run out before their November restock starts while the GDC still holds the part."""
    gdc = master[master["location"] == "GDC"]
    gdc_row = dict(zip(gdc["part_number"], gdc.index))
    # pairs with no November restock are short for another reason, and the supply line review shows them
    november_end = DAYS.get_loc(pd.Period(PROJECTION_MONTHS[0]).end_time.normalize())
    has_november_restock = pd.Series(restock_planned[:, november_end] > 0, index=master.index)
    early = master[has_november_restock & (master["run_out_date"] < PROJECTION_MONTHS[0])]
    early = early[early["part_number"].isin(gdc_row)].copy()
    run_out_day = [DAYS.get_loc(date) for date in early["run_out_date"]]
    early["gdc_stock"] = [stock[gdc_row[part], day] for part, day in zip(early["part_number"], run_out_day)]
    early["short_before_nov"] = -stock[early.index.to_numpy()][:, BEFORE_PLAN].min(axis=1)
    early = early[(early["gdc_stock"] > 0) & (early["short_before_nov"] >= 1)]  # at least one whole piece short
    note = ("runs out on " + early["run_out_date"].dt.strftime("%Y-%m-%d") + " and is up to "
            + early["short_before_nov"].round(1).astype(str) + " pieces short before November. Its planned restock starts "
            + "in November and the plan has no October rows, so no restock is counted before then. The GDC holds "
            + early["gdc_stock"].round(0).astype(int).astype(str)
            + " pieces that day: if it ships before November, this gap closes. Check with the planner")
    flags = pd.DataFrame({"rule": "5.5", "issue": "runs out before the restock plan starts", "file": "demand",
                          "part_number": early["part_number"], "location": early["location"],
                          "record": "Replenishment rows start in 2026-11", "note": note}).reindex(columns=FLAG_COLUMNS)
    print(f"  supply review: {len(flags)} PDC pairs run out before the restock plan starts while the GDC has stock, flagged")
    return flags


def review_supply(master: pd.DataFrame, clean: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], pd.DataFrame]:
    """Project every pair to Jan 31 and review the open supply. Returns the table, the review tables and the 5.5 flags."""
    master = master.reset_index(drop=True)
    flows = flows_by_day(master, clean["demand"], clean["supply"])
    check_flows(master, flows, clean["supply"])
    stock, restock_planned, restock_received = project_stock(master, flows)
    master = add_projection_columns(master, stock, restock_planned, restock_received)
    lines = review_supply_lines(master, clean["supply"], stock)
    need = need_by_pair(master, stock, restock_planned - restock_received)
    tables = {"supply_lines": lines, "gdc_shortfalls": gdc_shortfalls(master, lines), "moves": suggest_moves(master, need),
              "daily_stock": daily_stock_table(master, stock)}
    flags = flag_before_restock_plan(master, stock, restock_planned)
    print(f"  supply review: restock Nov to Jan planned {restock_planned[:, -1].sum():,.0f}, "
          f"the GDC can send {restock_received[:, -1].sum():,.0f}; {master['run_out_date'].notna().sum()} pairs run out by Jan 31")
    print(f"  daily stock: {len(tables['daily_stock']):,} rows ({len(master)} pairs x {len(DAYS)} days)")
    return master, tables, flags


if __name__ == "__main__":
    clean_tables, flag_list = clean_all(standardize_all(read_all_raw()))
    master, set_aside_flags = build_master(clean_tables)
    master, timing_flags = add_status(add_wos(master))
    master, review_tables, review_flags = review_supply(master, clean_tables)
    lines = review_tables["supply_lines"]
    print("\nPO lines by verdict (pallets blank where qpc is unknown, 4.10):")
    by_verdict = lines.groupby("review")[["qty", "pallets", "pieces_above_26w"]].sum(min_count=1)
    by_verdict.insert(0, "lines", lines.groupby("review").size())
    print(by_verdict.round(0).to_string())
    print("\nstatus today against status on Jan 31:")
    print(pd.crosstab(master["status"], master["status_jan31"], margins=True).to_string())
    print("\nGDC shortfalls:", len(review_tables["gdc_shortfalls"]), "parts | top 5:")
    print(review_tables["gdc_shortfalls"].head(5).to_string(index=False, float_format="%.1f"))
    moves = review_tables["moves"]
    print(f"\nmoves: {len(moves)} for {moves['part_number'].nunique()} parts, {moves['pieces'].sum():,} pieces, by route:",
          moves.groupby("route")["pieces"].sum().to_dict())
    print(moves.head(8).to_string(index=False, float_format="%.1f"))
    check_columns = ["part_number", "location", "on_hand_qty", "run_out_date", "days_without_stock", "worst_backorder",
                     "restock_planned_3m", "restock_not_received", "stock_jan31", "wos_jan31", "status_jan31"]
    print("\nhand checks:")
    hand_checks = master["part_number"].isin(["SKU98", "SKU9", "SKU26"])
    print(master.loc[hand_checks, check_columns].to_string(index=False, float_format="%.1f"))
