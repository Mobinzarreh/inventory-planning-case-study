"""How it works: the end-to-end flow and the key decisions behind the numbers (details in ASSUMPTIONS.md).

Every count on this page is read from output/, so the flow stays true if the pipeline changes.
"""
import pandas as pd
import streamlit as st

from dashboard.data import OUTPUT_TABLES, load_tables, raw_to_clean
from src.status_chart import AXIS_LINE, INK, INK_MUTED
from src.weeks_of_supply import WOS_MONTHS

STEP_STYLE = 'shape=box, style="rounded,filled", fillcolor="#f1f0ec", color="{line}", fontcolor="{ink}"'


def flow_counts(tables: dict[str, pd.DataFrame]) -> dict:
    """The numbers shown on each step of the flow."""
    funnel = raw_to_clean(tables).loc["All files"]
    pairs, flags = tables["inventory_health"], tables["flags"]
    set_aside = int((flags["issue"] == "pair set aside").sum())
    return {"read": funnel["Rows read"], "standardized": funnel["Values standardized"],
            "dropped": funnel["Exact copies dropped"], "filled": funnel["Values filled or estimated"],
            "kept": funnel["Rows kept"], "pairs": len(pairs), "joined": len(pairs) + set_aside, "set_aside": set_aside,
            "with_wos": int(pairs["wos"].notna().sum()), "days": tables["daily_stock"]["date"].nunique(),
            "lines": len(tables["supply_review_lines"]), "shortfalls": len(tables["gdc_shortfalls"]),
            "moves": len(tables["stock_moves"]), "flags": len(flags), "csvs": len(OUTPUT_TABLES)}


def flow_chart(n: dict) -> str:
    """The pipeline as a Graphviz diagram, top to bottom, with the flag list fed from the steps that raise flags."""
    steps = [
        ("raw", f"Raw files from Tesla\n4 CSVs, {n['read']:,} rows, never edited"),
        ("read", "Read every column as text\nsrc/load_raw.py"),
        ("std", f"Standardize formats\n{n['standardized']:,} values written one way, no row added or removed"),
        ("clean", f"Clean\n{n['dropped']} exact copies dropped, {n['filled']} values filled\n{n['kept']:,} rows kept"),
        ("master", f"Master table: one row per part and site\n{n['joined']} pairs joined, {n['set_aside']} empty ones "
                   f"set aside, {n['pairs']} kept"),
        ("wos", f"WOS and status\n{n['with_wos']} pairs with demand get a WOS, 5 statuses"),
        ("review", f"Supply review: day by day to Jan 31\n{n['days']} days, {n['lines']} PO lines judged, "
                   f"{n['shortfalls']} GDC shortfalls, {n['moves']} moves"),
        ("out", f"output/\n{n['csvs']} CSVs, the Excel workbook and a chart"),
        ("app", "This dashboard\nreads output/ only, recalculates nothing"),
    ]
    style = STEP_STYLE.format(line=AXIS_LINE, ink=INK)
    lines = ["digraph {", 'graph [rankdir=TB, nodesep=0.4, ranksep=0.22, fontname="Inter"];',
             f'node [{style}, fontname="Inter", fontsize=14, width=3.6, margin="0.15,0.06"];',
             f'edge [color="{INK_MUTED}", arrowsize=0.7];']
    for key, label in steps:
        lines.append(f'{key} [label="{label}"];')
    lines.append(" -> ".join(key for key, _ in steps) + " [weight=10];")  # a heavy weight keeps the chain straight
    lines.append(f'flags [label="Flag list\\n{n["flags"]} findings,\\neach with what\\nit might mean", shape=note, '
                 f'style=filled, fillcolor="#ffffff", width=1.8];')
    lines.append("{rank=same; wos; flags}")  # the flag list sits beside the chain
    # constraint=false: these arrows show where flags come from without bending the main chain
    lines.append(f'{{clean master review}} -> flags [style=dashed, color="{AXIS_LINE}", constraint=false];')
    lines.append(f'flags -> out [style=dashed, color="{AXIS_LINE}", constraint=false];')
    lines.append("}")
    return "\n".join(lines)


def data_decisions(flags: pd.DataFrame) -> list[str]:
    """How the raw files were read and cleaned, with the count of filled months that fall in the WOS window."""
    filled = flags[flags["issue"].isin(["blank forecast month filled", "negative forecast month filled"])]
    filled_in_window = filled["record"].str.extract(r"(\d{4}-\d{2})")[0].isin(WOS_MONTHS).sum()
    in_window = "none" if filled_in_window == 0 else str(filled_in_window)
    return [
        "**Read every column as text.** Odd values like \"1,041\" or \"N/A\" are seen and fixed on purpose, "
        "never guessed by the reader.",
        "**Fix formats, never meanings.** Spellings, numbers and dates are written one way, and no row is added "
        "or removed.",
        "**Nothing is dropped or changed silently.** Only exact copies are dropped, and every drop, fill and odd "
        "value gets a flag with what it might mean.",
        f"**A blank or negative forecast month takes the average of its neighbours.** It keeps the pace of "
        f"demand, and {in_window} of the {len(filled)} filled months fall in Nov to Jan, the months WOS uses.",
    ]


def measure_decisions(pairs: pd.DataFrame) -> list[str]:
    """How WOS, status and space are defined, with PDC1's average and median WOS as the example."""
    pdc1_wos = pairs.loc[(pairs["location"] == "PDC1") & pairs["wos"].notna(), "wos"]
    return [
        "**The GDC's demand includes the restock it ships to the PDCs.** Restock leaves the GDC's shelf just "
        "like a sale.",
        "**WOS = on hand ÷ average weekly demand over Nov to Jan.** That is the demand today's stock will "
        "actually meet.",
        "**Short under 4 weeks, Excess over 26.** About one restock cycle at the low end. At the high end, room "
        "for normal pallet-sized deliveries and the 12-week wait for open supply.",
        f"**One status mix per site, not one WOS number.** An average hides shortages behind excess: PDC1's "
        f"average WOS is {pdc1_wos.mean():.1f} weeks, but its median is {pdc1_wos.median():.1f}.",
        "**Space in pallet equivalents** (pieces ÷ pieces per pallet). Pieces mix bolts and windshields, and "
        "pallets put them on one scale of warehouse space.",
    ]


def supply_decisions(pairs: pd.DataFrame, lines: pd.DataFrame) -> list[str]:
    """How the projection to Jan 31 works, with the restock the GDC can send and the last ETA."""
    planned = pairs["restock_planned_3m"].sum()
    sent = planned - pairs["restock_not_received"].sum()
    last_eta = pd.to_datetime(lines["eta_date"]).max()
    return [
        f"**A dated projection to Jan 31.** Supply that lands after the shelf is empty doesn't prevent the "
        f"stockout, and all open supply lands by {last_eta:%b %d}.",
        "**Customers buy every day, restock ships on the 1st, all in whole pieces.** Restock months come in "
        "whole pallets and customer months mostly don't, and the results barely move if restock is spread out.",
        f"**PDCs get only the restock the GDC can send.** The plan asks for {planned:,.0f} pieces in Nov to Jan, "
        f"and the GDC can send {sent:,.0f}.",
        "**Late October: customers buy, no restock moves.** The plan has no October rows, so the gap is flagged "
        "for the planner instead of assumed away.",
    ]


def decision_lines(tables: dict[str, pd.DataFrame]) -> dict[str, list[str]]:
    """The key decisions, one line each with why, in three groups. Numbers are read from output/."""
    pairs = tables["inventory_health"]
    return {"The data": data_decisions(tables["flags"]),
            "The measures": measure_decisions(pairs),
            "The supply review": supply_decisions(pairs, tables["supply_review_lines"])}


st.title("How it works")
st.write("The path from Tesla's raw CSVs to this dashboard, and the few decisions every number depends on. "
         "Each decision, with its evidence and the code that applies it, is written up in ASSUMPTIONS.md.")
st.caption("The sidebar filters don't apply on this page. Every count here is read from output/.")

tables = load_tables()
flow, decisions = st.columns([1, 1], gap="large")
with flow:
    st.subheader("End to end")
    st.graphviz_chart(flow_chart(flow_counts(tables)))
    st.caption("One command, run_pipeline.py, runs every step in about 3 seconds. Each step checks its row counts and "
               "totals, and stops if a join drops or doubles a row.")
with decisions:
    st.subheader("Key decisions")
    for group, items in decision_lines(tables).items():
        st.markdown(f"**{group}**")
        st.markdown("\n".join(f"- {item}" for item in items))
