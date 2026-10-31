"""Actions: what the planner should do about the top issues, from the supply review in src/supply_review.py."""
import pandas as pd
import streamlit as st

from dashboard.charts import NO_TOOLBAR, run_out_chart, run_out_takeaway, run_out_weeks
from dashboard.data import add_part_details, filter_pairs, filter_rows, load_tables, number_table

VERDICT_ORDER = ["lands too late", "adds to excess", "no demand anywhere", "not in the part master", "on time"]
# What to do comes right after the part and its name, so it never sits off screen
SHORTFALL_COLUMNS = {
    "part_number": "Part", "part_name": "Part name", "action": "Action", "planner": "Planner",
    "status": "Status today", "on_hand_qty": "On hand", "run_out_date": "Runs out", "next_po_eta": "Next PO lands",
    "worst_backorder": "Pieces short, worst day", "pallets_short": "Pallets short",
    "pdc_restock_not_sent": "PDC restock not sent",
}
LINE_COLUMNS = {
    "po_number": "PO", "po_line": "Line", "part_number": "Part", "part_name": "Part name", "action": "Action",
    "destination": "Destination",
    "source": "Source", "qty": "Pieces", "eta_date": "Lands", "run_out_date": "Site runs out",
    "days_without_stock_before": "Days out before it lands", "pieces_above_26w": "Pieces above 26 weeks",
    "planner": "Planner",
}
MOVE_COLUMNS = {
    "part_number": "Part", "part_name": "Part name", "from_site": "From", "to_site": "To", "pieces": "Pieces",
    "pallets": "Pallets", "needed_from": "Needed from", "route": "Route", "planner": "Planner",
}
WHOLE_PIECES = ["on_hand_qty", "worst_backorder", "pdc_restock_not_sent", "qty", "pieces_above_26w", "pieces"]


def shortage_tiles(shortfalls: pd.DataFrame, lines: pd.DataFrame) -> None:
    """The first half of the to-do list: supply that is missing or lands too late."""
    late = lines[lines["review"] == "lands too late"]
    st.caption("Not enough, or too late")
    tiles = st.columns(3)
    tiles[0].metric("Parts to order", shortfalls["next_po_eta"].isna().sum(), border=True, height="stretch",
                    help="GDC parts that run out by Jan 31 with no PO landing after they run out.")
    tiles[1].metric("POs to expedite", (late["source"] == "Supplier").sum(), border=True, height="stretch",
                    help="Supplier PO lines that land after the site has already run out.")
    tiles[2].metric("Transfers to bridge", (late["source"] == "GDC Transfer").sum(), border=True, height="stretch",
                    help="GDC transfers already on the road that land too late. Cover the gap from another "
                         "site (see Stock moves).")


def surplus_tiles(lines: pd.DataFrame, moves: pd.DataFrame) -> None:
    """The second half of the to-do list: stock or supply that is too much, or at the wrong site."""
    excess = lines[lines["review"] == "adds to excess"]
    dead = lines[lines["review"] == "no demand anywhere"]
    st.caption("Too much, or in the wrong place")
    tiles = st.columns(3)
    tiles[0].metric("Moves to check", len(moves), border=True, height="stretch",
                    help=f"Spare stock that could cover a shortage at another site: {moves['pieces'].sum():,.0f} "
                         "pieces in all.")
    tiles[1].metric("Lines to push out", len(excess), border=True, height="stretch",
                    help=f"PO lines that land above the 26-week line: {excess['pieces_above_26w'].sum():,.0f} "
                         "pieces in all. For a transfer already on the road, cut the next planned restock instead.")
    tiles[2].metric("Lines to cancel", len(dead), border=True, height="stretch",
                    help=f"PO lines for parts with no demand anywhere in the network: {dead['qty'].sum():,.0f} "
                         "pieces in all. Cancel, return or redirect them.")


def show_table(table: pd.DataFrame, columns: dict[str, str], file_name: str) -> None:
    """A readable table and a button that downloads exactly the rows shown."""
    readable = table[list(columns)].copy()
    for column in WHOLE_PIECES:
        if column in readable:
            readable[column] = readable[column].round().astype("Int64")  # pieces read better without decimals
    readable = readable.rename(columns=columns).round(1)
    for column in readable.select_dtypes("str").columns:
        readable[column] = readable[column].fillna("")  # a blank cell, not the word None
    number_table(readable, hide_index=True)
    st.download_button("Download as CSV", readable.to_csv(index=False), file_name=file_name, mime="text/csv",
                       key=file_name)


def po_lines_tab(lines: pd.DataFrame) -> None:
    """PO lines for one verdict at a time, the most urgent first."""
    counts = lines["review"].value_counts()
    options = [verdict for verdict in VERDICT_ORDER if verdict in counts.index]
    labels = {verdict: f"{verdict} ({counts[verdict]})" for verdict in options}
    chosen = st.pills("Verdict", options, default=options[0] if options else None, format_func=labels.get)
    if chosen:
        lines = lines[lines["review"] == chosen]
    # the longest wait first, then the most pieces above the line, then by landing date
    lines = lines.sort_values(["days_without_stock_before", "pieces_above_26w", "eta_date"],
                              ascending=[False, False, True])
    show_table(lines, LINE_COLUMNS, "po_lines.csv")


st.title("Actions")
st.write("What the planner should do about the top issues: parts to order, PO lines to change, and stock to move.")

tables = load_tables()
pairs = tables["inventory_health"]
shortfalls = filter_rows(tables["gdc_shortfalls"].assign(location="GDC"), pairs, ["location"])
lines = filter_rows(tables["supply_review_lines"], pairs, ["destination"])
moves = filter_rows(tables["stock_moves"], pairs, ["from_site", "to_site"])
st.caption("Everything on this page follows the sidebar filters. A move counts for both the site it leaves "
           "and the site it goes to.")

shortage_tiles(shortfalls, lines)
surplus_tiles(lines, moves)

st.subheader("When pairs run out")
st.caption("Pairs that run out by Jan 31 even counting incoming supply, by the week they first run out. "
           "Darker means more pairs. Hover a cell for the count.")
weeks = run_out_weeks(filter_pairs(pairs))
if not weeks.empty:
    st.plotly_chart(run_out_chart(weeks), config=NO_TOOLBAR)
    st.markdown(run_out_takeaway(weeks))

st.subheader("To-do lists")
gdc_tab, lines_tab, moves_tab = st.tabs([f"GDC parts to order ({len(shortfalls)})", f"PO lines ({len(lines)})",
                                         f"Stock moves ({len(moves)})"])
with gdc_tab:
    st.caption("GDC parts that run out by Jan 31, the biggest shortage first. Most PDC shortages start here: "
               "the GDC can't send restock it doesn't have.")
    shortfalls = shortfalls.assign(next_po_eta=shortfalls["next_po_eta"].fillna("no PO"))
    show_table(add_part_details(shortfalls, pairs), SHORTFALL_COLUMNS, "gdc_parts_to_order.csv")
with lines_tab:
    st.caption("Each open PO line checked against the projection. Pick a verdict to see its lines.")
    lines = lines.assign(run_out_date=lines["run_out_date"].fillna("not by Jan 31"))
    po_lines_tab(add_part_details(lines, pairs))
with moves_tab:
    st.caption("Spare stock (above the 26-week line, or at a site with no demand while another site wants it) "
               "matched to the biggest need first. Matched greedily, not optimized. Moves between PDCs or back "
               "to the GDC say \"check if a transfer is possible\".")
    show_table(add_part_details(moves, pairs), MOVE_COLUMNS, "stock_moves.csv")
