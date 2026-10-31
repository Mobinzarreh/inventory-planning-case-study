"""Overview: where the network stands today, and once incoming supply lands (on Jan 31)."""
import pandas as pd
import streamlit as st

from dashboard.charts import (NO_TOOLBAR, TIED_UP, biggest_stockout_rise, most_at_risk, status_change_takeaway,
                              status_counts, status_mix_chart, status_transitions, stockout_shift,
                              stockout_shift_chart, tied_up_chart, tied_up_pallets, tied_up_takeaway, transition_chart,
                              unknown_parts_text)
from dashboard.data import filter_pairs, load_tables, number_table


def headline_tiles(pairs: pd.DataFrame) -> None:
    """Five numbers to read first: today, Jan 31, and the stock tied up above the 26-week line."""
    out_today = (pairs["status"] == "Stockout").sum()
    out_jan31 = (pairs["status_jan31"] == "Stockout").sum()
    tiles = st.columns(5)
    tiles[0].metric("Out of stock today", out_today, border=True, height="stretch",
                    help="Demand in Nov to Jan and nothing on hand on 2026-10-22.")
    tiles[1].metric("Short today", (pairs["status"] == "Short").sum(), border=True, height="stretch",
                    help="Under 4 weeks of supply.")
    tiles[2].metric("Out of stock Jan 31", out_jan31, delta=f"{out_jan31 - out_today:+,} vs today",
                    delta_color="inverse", border=True, height="stretch",
                    help="After incoming supply and the restock the GDC can actually send have landed.")
    tiles[3].metric("Run out by Jan 31", pairs["run_out_date"].notna().sum(), border=True, height="stretch",
                    help="At least one day without stock between now and Jan 31, even counting incoming supply.")
    tiles[4].metric("Excess pallets", f"{pairs['pallets_above_26w'].sum():,.0f}", border=True, height="stretch",
                    help="The part of Excess stock above the 26-week line, in pallet equivalents "
                         "(pieces ÷ qpc, per part).")




st.title("Overview")
st.write("Where each site stands today, and what changes once incoming supply and planned restock land.")

pairs = load_tables()["inventory_health"]
shown = filter_pairs(pairs)
st.caption(f"{len(shown)} of {len(pairs)} part and location pairs match the sidebar filters.")
if shown.empty:
    st.warning("No pairs match these filters.")
    st.stop()

headline_tiles(shown)

st.subheader("Inventory status by site")
st.caption("Share of part and location pairs in each status today. Hover a segment for the count, "
           "click a legend entry to hide that status.")
counts = status_counts(shown)
st.plotly_chart(status_mix_chart(counts), config=NO_TOOLBAR)
st.markdown(most_at_risk(counts))
with st.expander("Numbers behind the chart"):
    number_table(counts)

st.subheader("What changes by Jan 31")
st.caption("Once incoming supply and the restock the GDC can actually send have landed.")
left, right = st.columns(2, gap="large")
with left:
    st.markdown("**Pairs out of stock, today and on Jan 31**")
    shift = stockout_shift(shown)
    st.plotly_chart(stockout_shift_chart(shift), config=NO_TOOLBAR)
    st.markdown(biggest_stockout_rise(shift))
    with st.expander("Numbers behind the chart"):
        number_table(shift.rename(columns={"today": "Out of stock today", "jan31": "Out of stock Jan 31"}))
with right:
    st.markdown("**Where today's pairs end up on Jan 31**")
    transitions = status_transitions(shown)
    st.plotly_chart(transition_chart(transitions), config=NO_TOOLBAR)
    st.markdown(status_change_takeaway(transitions))
    with st.expander("Numbers behind the chart"):
        number_table(transitions.rename_axis(index="Today", columns="Jan 31"))

st.subheader("Pallets tied up")
st.caption("Stock that costs space and money without serving demand, in pallet equivalents (pieces ÷ qpc, per part). "
           "Hover a bar for its share of the site's pallets on hand.")
pallets = tied_up_pallets(shown)
st.plotly_chart(tied_up_chart(pallets), config=NO_TOOLBAR)
st.markdown(tied_up_takeaway(pallets))
unknown_note = unknown_parts_text(shown)  # parts with no qpc, left off the chart
if unknown_note:
    st.caption(unknown_note)
with st.expander("Numbers behind the chart"):
    readable = {"on_hand": "All pallets on hand"}
    for column, details in TIED_UP.items():
        readable[column] = details[0]  # the legend text
    number_table(pallets.rename(columns=readable).round(1))
