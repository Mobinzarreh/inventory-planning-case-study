"""Worked example: SKU98 followed from the raw rows to the suggested move, then any other part.

The story is fixed, but every number in it is read from output/, so it stays true if the pipeline changes.
"""
import pandas as pd
import streamlit as st

from dashboard.charts import NO_TOOLBAR
from dashboard.data import load_clean_demand, load_tables, number_table, part_label
from dashboard.part_charts import daily_stock_chart, demand_chart, part_order, part_sites, site_table
from src.config import FORECAST_MONTHS
from src.status import EXCESS_WEEKS
from src.weeks_of_supply import WOS_MONTHS, WOS_WEEKS

PART = "SKU98"
FIRST_PICK = "SKU75"  # the GDC's biggest shortfall, a different story from SKU98
LATER_MONTHS = FORECAST_MONTHS[3:6]  # Feb to Apr, when PDC2's restock starts
LINE_COLUMNS = {"po_number": "PO", "po_line": "Line", "destination": "Destination", "source": "Source",
                "qty": "Pieces", "eta_date": "Lands", "days_without_stock_before": "Days out before it lands",
                "short_when_landing": "Pieces waiting when it lands", "review": "Verdict", "action": "Action"}
MOVE_COLUMNS = {"from_site": "From", "to_site": "To", "pieces": "Pieces", "pallets": "Pallets",
                "needed_from": "Needed from", "route": "Route"}
FLAG_COLUMNS = {"rule": "Rule", "issue": "Issue", "location": "Site", "record": "Record",
                "original_value": "Was", "value_used": "Now", "note": "What it might mean"}


def demand_months(demand: pd.DataFrame, site: str, demand_type: str, months: list[str]) -> pd.Series:
    """Pieces per month for one kind of demand row of this part at one site (restock rows summed over PDCs)."""
    is_row = (demand["part_number"] == PART) & (demand["location"] == site) & (demand["demand_type"] == demand_type)
    return demand.loc[is_row, months].sum()


def pieces(values: pd.Series) -> str:
    """Monthly pieces as words: '746, 814 and 759'."""
    numbers = [f"{value:,.0f}" for value in values]
    return ", ".join(numbers[:-1]) + " and " + numbers[-1]


def readable(table: pd.DataFrame, columns: dict[str, str]) -> pd.DataFrame:
    """Only the listed columns, renamed, with whole pieces and blank cells instead of None."""
    shown = table[list(columns)].rename(columns=columns)
    for column in shown.select_dtypes("float").columns:
        if column != "Pallets":
            shown[column] = shown[column].round().astype("Int64")
    for column in shown.select_dtypes("str").columns:
        shown[column] = shown[column].fillna("")
    return shown


def step_rows(rows: pd.DataFrame, demand: pd.DataFrame, line: pd.Series, flags: pd.DataFrame) -> None:
    """Step 1: the stock, demand and supply rows everything else comes from."""
    st.subheader("1. The rows behind it")
    pdc2_customers = demand_months(demand, "PDC2", "Direct", WOS_MONTHS)
    pdc2_restock = demand[(demand["part_number"] == PART) & (demand["ship_to"] == "PDC2")]
    st.markdown(f"On Oct 22 the GDC holds **{rows.at['GDC', 'on_hand_qty']:,.0f}** pieces, PDC2 "
                f"**{rows.at['PDC2', 'on_hand_qty']:,.0f}** and PDC3 **{rows.at['PDC3', 'on_hand_qty']:,.0f}**. "
                f"PDC2's customers want {pieces(pdc2_customers)} pieces in November to January. The GDC's restock plan "
                f"for PDC2 is zero until February, then {pieces(pdc2_restock[LATER_MONTHS].sum())} a month. One PO is "
                f"open: **{line['po_number']} brings {line['qty']:,.0f} pieces to PDC2 on {line['eta_date']}**.")
    rows_shown = demand[demand["part_number"] == PART][["location", "demand_type", "ship_to"] + FORECAST_MONTHS[:6]]
    number_table(rows_shown.rename(columns={"location": "Site", "demand_type": "Demand type", "ship_to": "Ships to"})
                 .fillna(""), hide_index=True)
    part_flags = flags[flags["part_number"] == PART]
    with st.expander(f"Data notes on this part ({len(part_flags)})"):
        number_table(part_flags[list(FLAG_COLUMNS)].rename(columns=FLAG_COLUMNS).fillna(""), hide_index=True)


def step_today(pairs: pd.DataFrame, rows: pd.DataFrame, demand: pd.DataFrame) -> None:
    """Step 2: weeks of supply and status today, with the GDC's sum written out."""
    st.subheader("2. Today: weeks of supply and status")
    gdc, pdc2, pdc3 = rows.loc["GDC"], rows.loc["PDC2"], rows.loc["PDC3"]
    own = demand_months(demand, "GDC", "Direct", WOS_MONTHS).sum()
    restock = demand_months(demand, "GDC", "Replenishment", WOS_MONTHS).sum()
    st.markdown(f"WOS divides today's stock by the average weekly demand over November to January "
                f"({WOS_WEEKS:.2f} weeks). At the GDC that demand is its own customers ({own:,.0f}) plus the restock "
                f"it ships to PDC3 ({restock:,.0f}): {gdc['demand_3m']:,.0f} pieces, or {gdc['weekly_demand']:.1f} a "
                f"week. So {gdc['on_hand_qty']:,.0f} ÷ {gdc['weekly_demand']:.1f} = **{gdc['wos']:.0f} weeks: "
                f"{gdc['status']}**. PDC2 has nothing on hand: **{pdc2['status']}**. PDC3 has "
                f"{pdc3['on_hand_qty']:,.0f} ÷ {pdc3['weekly_demand']:.1f} = **{pdc3['wos']:.1f} weeks: "
                f"{pdc3['status']}**.")
    columns = ["On hand", "Nov to Jan demand", "Weekly demand", "WOS", "Status today"]
    number_table(site_table(pairs, PART)[columns])


def step_day_by_day(tables: dict[str, pd.DataFrame], rows: pd.DataFrame, line: pd.Series) -> None:
    """Step 3: the projection, day by day to Jan 31."""
    st.subheader("3. Day by day to Jan 31")
    day_before = (pd.Timestamp(line["eta_date"]) - pd.Timedelta(days=1)).strftime("%b %d")
    pdc3 = rows.loc["PDC3"]
    st.markdown(f"PDC2 is out from the first day, and its unmet demand keeps growing. By {day_before}, "
                f"{line['short_when_landing']:,.0f} pieces are waiting, so the {line['qty']:,.0f} pieces of "
                f"{line['po_number']} don't catch up. PDC2 is out all {rows.at['PDC2', 'days_without_stock']} days, "
                f"and on Jan 31 it is still **{-rows.at['PDC2', 'stock_jan31']:,.0f} pieces short**. "
                f"PDC3 doesn't run out, but it ends at {pdc3['stock_jan31']:,.0f} pieces, which is {pdc3['status_jan31']} on Jan 31. "
                f"The GDC ships little before February and still holds {rows.at['GDC', 'stock_jan31']:,.0f} on Jan 31.")
    chart = daily_stock_chart(tables["daily_stock"], tables["supply_review_lines"], tables["inventory_health"], PART)
    st.plotly_chart(chart, config=NO_TOOLBAR)
    st.caption("Shaded red: demand not met (stock below zero). Triangles: open PO lines and transfers landing. "
               "Each site has its own scale.")


def gdc_runs_out_in(rows: pd.DataFrame) -> str:
    """The month the GDC's Jan 31 stock runs out at its planned outflow, with no new order."""
    outflow = rows.loc["GDC", FORECAST_MONTHS[3:]].astype(float).cumsum()
    past_stock = outflow[outflow > rows.at["GDC", "stock_jan31"]]
    if past_stock.empty:
        return "after October 2028"
    return pd.Period(past_stock.index[0]).strftime("%B %Y")


def step_verdict(rows: pd.DataFrame, demand: pd.DataFrame, line: pd.Series) -> None:
    """Step 4: the verdict on the open PO, and why the GDC's Excess isn't spare."""
    st.subheader("4. The verdict")
    st.markdown(f"**{line['po_number']} {line['review']}.** When it arrives, PDC2 has been out for "
                f"{line['days_without_stock_before']} days. The action: {line['action']}.")
    number_table(readable(line.to_frame().T.infer_objects(), LINE_COLUMNS), hide_index=True)  # back to number types
    gdc = rows.loc["GDC"]
    to_pdc2 = demand[(demand["part_number"] == PART) & (demand["ship_to"] == "PDC2")][LATER_MONTHS].sum()
    st.markdown(f"**The GDC's {gdc['status']} isn't spare.** Its November to January demand is small "
                f"({gdc['demand_3m']:,.0f}) because the restock plan for PDC2 only starts in February. From then "
                f"the GDC ships {pieces(gdc[LATER_MONTHS].astype(float))} pieces a month ({pieces(to_pdc2)} of them "
                f"to PDC2), so the {gdc['stock_jan31']:,.0f} it holds on Jan 31 runs out in {gdc_runs_out_in(rows)}. "
                f"Its {gdc['wos']:.0f} weeks of supply are really PDC2's restock, held early.")


def step_action(rows: pd.DataFrame, line: pd.Series, moves: pd.DataFrame, gdc_lines: pd.DataFrame) -> None:
    """Step 5: what the planner should do."""
    st.subheader("5. What to do")
    move = moves[(moves["part_number"] == PART) & (moves["to_site"] == "PDC2")].iloc[0]
    open_po = "It has no open PO" if gdc_lines.empty else f"Its {len(gdc_lines)} open PO lines are not enough"
    st.markdown(f"1. **Move {move['pieces']:,} pieces ({move['pallets']:.0f} pallets) from the {move['from_site']} "
                f"to PDC2 now.** That's the GDC's stock above {EXCESS_WEEKS} weeks of its November to January demand. "
                f"It covers {move['pieces']:,} of the {-rows.at['PDC2', 'stock_jan31']:,.0f} pieces PDC2 is still "
                f"short on Jan 31, and it's PDC2's own February restock, sent early.\n"
                f"2. **Expedite {line['po_number']}.** Every day earlier is one day less without stock at PDC2.\n"
                f"3. **Order for the GDC.** {open_po}, and after the move it can't cover PDC2's restock "
                f"from February.")
    number_table(readable(moves[moves["part_number"] == PART], MOVE_COLUMNS), hide_index=True)


def part_details(pairs: pd.DataFrame, part: str) -> str:
    """A few words about the part from the part master (its name is in the label), or a note that it isn't in it."""
    info = pairs[pairs["part_number"] == part].iloc[0]
    if not info["in_part_master"]:
        return "Not in the part master, so no part type, planner or qpc"
    return f"Part type {info['part_type']}, planner {info['planner']}, {info['qpc']:,.0f} pieces per pallet"


def part_picker(tables: dict[str, pd.DataFrame], demand: pd.DataFrame) -> None:
    """The same views for any part: sites, day-by-day stock, demand, PO lines, moves and data notes."""
    pairs = tables["inventory_health"]
    parts = sorted(pairs["part_number"].unique(), key=part_order)
    part = st.selectbox("Part", parts, index=parts.index(FIRST_PICK), format_func=part_label,
                        help="Type a name or a code, like compressor or SKU98.")
    st.caption(f"{part_details(pairs, part)}. Rows at {', '.join(part_sites(pairs, part))}.")
    number_table(site_table(pairs, part))
    st.markdown("**Stock day by day to Jan 31**")
    st.plotly_chart(daily_stock_chart(tables["daily_stock"], tables["supply_review_lines"], pairs, part),
                    config=NO_TOOLBAR, key="picker_daily")
    st.markdown("**Demand, November 2026 to October 2028**")
    st.plotly_chart(demand_chart(demand, pairs, part), config=NO_TOOLBAR, key="picker_demand")
    lists = [("Open PO lines", tables["supply_review_lines"], LINE_COLUMNS),
             ("Suggested moves", tables["stock_moves"], MOVE_COLUMNS), ("Data notes", tables["flags"], FLAG_COLUMNS)]
    for title, table, columns in lists:
        rows = table[table["part_number"] == part]
        with st.expander(f"{title} ({len(rows)})"):
            number_table(readable(rows, columns), hide_index=True)


st.title(f"Worked example: {part_label(PART)}")
tables = load_tables()
demand = load_clean_demand()
pairs = tables["inventory_health"]
rows = pairs[pairs["part_number"] == PART].set_index("location")
lines = tables["supply_review_lines"]
line = lines[lines["part_number"] == PART].iloc[0]
st.caption(f"{part_details(pairs, PART)}. The sidebar filters don't apply on this page.")
st.write("This one part shows most of the method: a site that is out from day one, a PO that lands too late, a "
         "site that looks Excess but is holding another site's restock, and a move that helps. Every number below "
         "is read from the output files.")

step_rows(rows, demand, line, tables["flags"])
step_today(pairs, rows, demand)
step_day_by_day(tables, rows, line)
step_verdict(rows, demand, line)
step_action(rows, line, tables["stock_moves"],
            lines[(lines["part_number"] == PART) & (lines["destination"] == "GDC")])

st.divider()
st.header("Any other part")
st.write(f"The same views for any part. It starts on {part_label(FIRST_PICK)}, the GDC's biggest shortfall.")
part_picker(tables, demand)
