"""The four tasks: each of Tesla's tasks answered in short, with why it was done this way and where to find more.

Every number is read from output/ for the whole network, so this page has no sidebar filters (see app.py).
"""
import html

import pandas as pd
import streamlit as st

from dashboard.charts import status_counts
from dashboard.data import load_tables, number_table, part_label
from src.status_chart import STATUS_COLORS
from src.config import SNAPSHOT_DATE
from src.status import DEAD_STOCK, EXCESS_WEEKS, SHORT_WEEKS, STATUS_ORDER, UNKNOWN_PART, WANTED_ELSEWHERE
from src.weeks_of_supply import WOS_WEEKS

ASSUMPTIONS_URL = "https://github.com/Mobinzarreh/inventory-planning-case-study/blob/main/ASSUMPTIONS.md#5-method"
# Tesla's wording, from the task README
TASKS = {
    1: ("Weeks of supply", "Calculate Weeks of Supply (WOS) for the current on-hand inventory of every part and "
                           "location. Explain your method."),
    2: ("Inventory status", "Define an inventory status metric. Set the thresholds, justify them, and classify "
                            "every part and location."),
    3: ("One view per site", "Summarize WOS at the DC level. How would you report one view per location (GDC, PDC1, "
                             "PDC2, PDC3) to the planning manager? Explain why you chose that roll-up."),
    4: ("Incoming supply and what to do", "Review the incoming supply against your findings, and recommend what the "
                                          "planner should do about the top issues. Keep it short."),
}
STATUS_RULES = {
    "Stockout": "Demand, nothing on hand (WOS 0)",
    "Short": f"WOS under {SHORT_WEEKS} weeks",
    "Healthy": f"WOS from {SHORT_WEEKS} to {EXCESS_WEEKS} weeks",
    "Excess": f"WOS over {EXCESS_WEEKS} weeks",
    "No demand": "No demand at this site in any of the 24 months",
}
# The app's look (.streamlit/config.toml and the agent card): white cards, charcoal number badges with a red ring,
# small gray labels, and a gray box for the reasons. Red stays on the badges, the status dots use the chart colors
STYLE = """
<style>
[class*="st-key-task_"] {background: #ffffff; border-radius: 16px; padding: 1.4rem 1.6rem 1rem;}
[class*="st-key-why_"] {background: #f2f2f2; border-radius: 12px; padding: 1rem 1.2rem 0.3rem;}
[class*="st-key-ask_"] {border: 1px dashed #c8c8c8; border-radius: 12px; padding: 1rem 1.2rem;}
[class*="st-key-links_"] {border-top: 1px solid #e0e0e0; padding-top: 0.5rem;}
.task-head {display: flex; gap: 0.9rem; align-items: center;}
.task-number {flex: none; width: 2.5rem; height: 2.5rem; border-radius: 999px; background: #222222;
    border: 2px solid #cc0000; color: #ffffff; font-weight: 700; font-size: 1.1rem;
    display: flex; align-items: center; justify-content: center;}
.task-title {font-size: 1.55rem; font-weight: 700; color: #222222; line-height: 1.2;}
.task-ask {margin-top: 0.8rem; padding: 0.2rem 0 0.2rem 0.9rem; border-left: 3px solid #c8c8c8; color: #666666;
    font-size: 0.92rem; font-style: italic;}
.kicker {font-size: 0.72rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: #808080;}
.task-ask .kicker {font-style: normal; margin-right: 0.4rem;}
.badge {flex: none; width: 1.6rem; height: 1.6rem; border-radius: 999px; background: #222222;
    border: 1px solid #cc0000; color: #ffffff; font-size: 0.8rem; font-weight: 600;
    display: flex; align-items: center; justify-content: center;}
.glance, .stats, .actions, .questions {display: grid; gap: 0.7rem;}
.glance {grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); margin-bottom: 0.4rem;}
.glance-tile {background: #ffffff; border: 1px solid #e0e0e0; border-radius: 12px; padding: 0.9rem 1rem;}
.glance-top {display: flex; gap: 0.55rem; align-items: center; font-size: 0.88rem; font-weight: 600; color: #444444;}
.glance-value {font-size: 1.45rem; font-weight: 700; color: #222222; margin-top: 0.5rem;}
.note {font-size: 0.86rem; color: #666666; line-height: 1.4; margin-top: 0.1rem;}
.formula {padding: 0.8rem 1rem; border-radius: 10px; background: #fafafa; border: 1px solid #e0e0e0;
    font-size: 1.15rem; font-weight: 600; color: #222222;}
.formula .note {font-weight: 400;}
.stats {grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));}
.stats.stacked {grid-template-columns: 1fr;}
.stat {padding: 0.75rem 0.9rem; border-radius: 10px; background: #fafafa; border: 1px solid #e0e0e0;}
.stat-value {font-size: 1.35rem; font-weight: 700; color: #222222;}
.status-table {width: 100%; border-collapse: collapse; font-size: 0.95rem;}
.status-table th {text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #e0e0e0; font-size: 0.72rem;
    font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: #808080;}
.status-table td {padding: 0.5rem 0.6rem; border-bottom: 1px solid #f2f2f2; color: #222222;}
.status-table td:first-child, .status-table th {white-space: nowrap;}
.status-table td:last-child, .status-table th:last-child {text-align: right; font-weight: 600;}
.status-table tr:last-child td {border-bottom: none; font-weight: 700;}
.dot {display: inline-block; width: 0.75rem; height: 0.75rem; border-radius: 3px; margin-right: 0.55rem;
    vertical-align: -0.05rem;}
.actions {grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));}
.action {display: flex; gap: 0.75rem; align-items: flex-start; padding: 0.85rem 0.95rem; border-radius: 10px;
    background: #ffffff; border: 1px solid #e0e0e0;}
.action-head, .question-head {display: block; font-weight: 700; color: #222222; margin-bottom: 0.15rem;}
.questions {grid-template-columns: repeat(auto-fit, minmax(min(100%, 380px), 1fr)); row-gap: 0.9rem;}
</style>
"""


def kicker(text: str) -> None:
    """A small gray label over a part of a card."""
    st.html(f'<div class="kicker">{text}</div>')


def task_header(number: int) -> None:
    """The task's number badge and name, then Tesla's ask in its own words."""
    title, ask = TASKS[number]
    st.html(f'<div class="task-head"><div class="task-number">{number}</div><div class="task-title">{title}</div>'
            f'</div><div class="task-ask"><span class="kicker">The task</span>{ask}</div>')


def why_box(key: str, title: str, reasons: list[str]) -> None:
    """The reasons behind an answer, in a gray box beside it."""
    with st.container(key=key):
        kicker(title)
        st.markdown("\n".join(f"- {reason}" for reason in reasons))


def stat_tiles(stats: list[tuple[str, str]], stacked: bool = False) -> str:
    """A few key numbers as small tiles: the number, then what it means. Side by side, or one under the other."""
    tiles = "".join(f'<div class="stat"><div class="stat-value">{value}</div><div class="note">{note}</div></div>'
                    for value, note in stats)
    layout = "stats stacked" if stacked else "stats"
    return f'<div class="{layout}">{tiles}</div>'


def where_to_look(number: int, pages: dict[str, str], files: str, sections: str) -> None:
    """Links to the dashboard pages behind an answer, then where it sits in the files and in ASSUMPTIONS.md."""
    with st.container(key=f"links_{number}", horizontal=True, vertical_alignment="center"):
        for path, title in pages.items():
            st.page_link(path, label=f"Open {title}", icon=":material/arrow_forward:")
        st.caption(f"{files} · [ASSUMPTIONS.md {sections}]({ASSUMPTIONS_URL})", width="content")


def at_a_glance(tables: dict, actions: int) -> None:
    """One headline per task, to read before the detail."""
    pairs = tables["inventory_health"]
    counts = pairs["status"].value_counts()
    sites = status_counts(pairs).iloc[:-1]  # the last row is the total
    shares = (sites["Stockout"] + sites["Short"]) / sites.sum(axis=1)
    worst = shares.idxmax()
    # short names here, so every tile's number sits on the same line
    tiles = [
        (1, "Weeks of supply", f"{pairs['wos'].notna().sum()} of {len(pairs)}",
         "pairs get a WOS: on hand ÷ weekly demand, Nov to Jan"),
        (2, "Inventory status", f"{counts['Stockout']} out · {counts['Short']} short",
         f"of {len(pairs)} pairs today, in 5 statuses"),
        (3, "One view per site", f"{worst} · {shares[worst]:.0%}",
         "of its pairs out of stock or short, the most at risk"),
        (4, "Supply and actions", f"{counts['Stockout']} → {(pairs['status_jan31'] == 'Stockout').sum()}",
         f"pairs out of stock, today to Jan 31, if no new order lands. {actions} actions"),
    ]
    items = "".join(f'<div class="glance-tile"><div class="glance-top"><div class="badge">{number}</div>{name}</div>'
                    f'<div class="glance-value">{value}</div><div class="note">{note}</div></div>'
                    for number, name, value, note in tiles)
    st.html(f'<div class="glance">{items}</div>')


def task_wos(pairs: pd.DataFrame) -> None:
    """Task 1: the WOS formula with one worked example, and why the next 3 months."""
    example = pairs[(pairs["part_number"] == "SKU6") & (pairs["location"] == "GDC")].iloc[0]
    with_wos = pairs["wos"].notna().sum()
    rules = [
        "At a PDC, demand is its own customers. At the GDC, it is its own customers plus the restock it ships to "
        "the PDCs, since both leave its shelf.",
        "Nothing on hand, or a backorder (negative on hand), gives WOS 0.",
        f"{with_wos} of the {len(pairs)} pairs get a WOS. The other {len(pairs) - with_wos} have no demand, so no WOS.",
    ]
    with st.container(border=True, key="task_1"):
        task_header(1)
        left, right = st.columns([3, 2], gap="large")
        with left:
            kicker("The answer")
            st.html(f'<div class="formula">WOS = on hand today ÷ average weekly demand<div class="note">Weekly '
                    f'demand = (Nov + Dec + Jan) ÷ {WOS_WEEKS:.2f} weeks, the window\'s 92 days</div></div>')
            st.markdown("\n".join(f"- {rule}" for rule in rules))
            st.html(stat_tiles([(f"{example['wos']:.1f} weeks",
                                 f"Example, SKU6 at the GDC: {example['on_hand_qty']:,.0f} pieces ÷ "
                                 f"{example['weekly_demand']:.1f} a week ({example['demand_3m']:,.0f} needed in "
                                 "Nov to Jan)")]))
        with right:
            why_box("why_1", "Why the next 3 months", [
                "It is the demand today's stock will actually meet: the median pair with stock runs out in 6 to 10 "
                "weeks.",
                "Its WOS comes closest to when stock really runs out, once the forecast is walked month by month: "
                "within 2 weeks or 20% for 219 of 253 pairs, more than with a 6, 12 or 24-month window.",
                "It uses only real forecast months (no filled blanks), and all open supply lands inside it.",
            ])
        where_to_look(1, {"dashboard/pages/worked_example.py": "Worked example: SKU98"},
                      "Workbook: sheet inventory_health, column wos", "5.1, 5.2")


def status_table(pairs: pd.DataFrame) -> str:
    """The five statuses with a dot in their chart color, their rule and today's count, as an HTML table."""
    counts = pairs["status"].value_counts()
    rows = ["<tr><th>Status</th><th>Rule</th><th>Pairs today</th></tr>"]
    for status in STATUS_ORDER:
        dot = f'<span class="dot" style="background: {STATUS_COLORS[status]}"></span>'
        rows.append(f"<tr><td>{dot}{status}</td><td>{STATUS_RULES[status]}</td><td>{counts.get(status, 0)}</td></tr>")
    rows.append(f"<tr><td>All</td><td></td><td>{len(pairs)}</td></tr>")
    return f'<table class="status-table">{"".join(rows)}</table>'


def task_status(pairs: pd.DataFrame, lines: pd.DataFrame) -> None:
    """Task 2: the five statuses, today's counts, and why the lines sit at 4 and 26 weeks."""
    reasons = pairs["no_demand_reason"].value_counts()
    weeks_out = (pd.to_datetime(lines["eta_date"]).max() - SNAPSHOT_DATE).days / 7
    with st.container(border=True, key="task_2"):
        task_header(2)
        left, right = st.columns([3, 2], gap="large")
        with left:
            kicker("The answer")
            st.html(status_table(pairs))
            st.html(f'<div class="note">No demand says why: {reasons.get(DEAD_STOCK, 0)} pairs are dead stock (no '
                    f'demand anywhere), {reasons.get(WANTED_ELSEWHERE, 0)} are wanted at another site, and '
                    f'{reasons.get(UNKNOWN_PART, 0)} are parts missing from the part master.</div>')
        with right:
            why_box("why_2", f"Why {SHORT_WEEKS} and {EXCESS_WEEKS} weeks", [
                f"**{SHORT_WEEKS} weeks** is about one restock cycle. Over half of the restock rows plan a shipment "
                "every month, so a site with less than a month of stock is likely to run out before the next one "
                "lands.",
                f"**{EXCESS_WEEKS} weeks** leaves room for normal deliveries and the wait for new supply. Whole "
                f"pallets make some deliveries over 13 weeks of demand at once, and open supply lands up to "
                f"{weeks_out:.1f} weeks out.",
                "**The same lines at every site:** open supply lands over the same span at the GDC and the PDCs, so "
                "nothing in the data shows one site waiting longer.",
            ])
        where_to_look(2, {"dashboard/pages/overview.py": "Overview"},
                      "Workbook: sheet inventory_health, column status", "5.3")


def site_view(summary: pd.DataFrame) -> pd.DataFrame:
    """The DC summary as the planning manager reads it: pairs per status, the share out or short, and pallets."""
    sites = summary.set_index("location")
    at_risk = sites["stockout"] + sites["short"]
    shares = at_risk / sites["pairs"]
    view = pd.DataFrame({
        "Pairs": sites["pairs"], "Stockout": sites["stockout"], "Short": sites["short"],
        "Stockout + Short": [f"{count} ({share:.0%})" for count, share in zip(at_risk, shares)],
        "Healthy": sites["healthy"], "Excess": sites["excess"], "No demand": sites["no_demand"],
        "Pallets on hand": sites["pallets_on_hand"].round().astype(int),
        f"Pallets above {EXCESS_WEEKS} weeks": sites["pallets_above_26w"].round().astype(int),
    })
    return view.rename_axis("Site")


def task_rollup(pairs: pd.DataFrame, summary: pd.DataFrame) -> None:
    """Task 3: one row per site, the two sites that stand out, and why a status mix and not one WOS number."""
    pdc1_wos = pairs.loc[(pairs["location"] == "PDC1") & pairs["wos"].notna(), "wos"]
    sites = summary[summary["location"] != "All sites"].set_index("location")
    shares = (sites["stockout"] + sites["short"]) / sites["pairs"]
    worst, most_excess = shares.idxmax(), sites["pallets_above_26w"].idxmax()
    excess_pallets = sites.at[most_excess, "pallets_above_26w"]
    with st.container(border=True, key="task_3"):
        task_header(3)
        kicker("The answer: one row per site, its pairs in each status and the space it ties up")
        number_table(site_view(summary))  # full width, so no column hides behind a scroll bar
        left, right = st.columns([1, 2], gap="large")
        left.html(stat_tiles([
            (f"{worst} · {shares[worst]:.0%}", f"{sites.at[worst, 'stockout'] + sites.at[worst, 'short']} of "
                                               f"{sites.at[worst, 'pairs']} pairs out of stock or short, the most "
                                               "at risk"),
            (f"{most_excess} · {excess_pallets:,.0f} pallets", f"of the {sites['pallets_above_26w'].sum():,.0f} "
                                                               f"above {EXCESS_WEEKS} weeks, the most excess"),
        ], stacked=True))
        with right:
            why_box("why_3", "Why a status mix, not one WOS number", [
                f"One number hides the spread. PDC1's average WOS is {pdc1_wos.mean():.1f} weeks, but half its pairs "
                f"have {pdc1_wos.median():.1f} or less.",
                f"An average also lets excess hide shortages: the {most_excess} holds {excess_pallets:,.0f} pallets "
                f"above {EXCESS_WEEKS} weeks while {sites.at[most_excess, 'stockout'] + sites.at[most_excess, 'short']}"
                " of its pairs are out of stock or short.",
                "Pairs, not pieces or money: a missing cheap part stops a repair just like an expensive one, and the "
                "data has no costs to weight by. Space is in pallet equivalents (pieces ÷ pieces per pallet).",
            ])
        where_to_look(3, {"dashboard/pages/overview.py": "Overview"},
                      "Workbook: sheet dc_summary, chart status_mix_by_dc.png", "5.4")


def projection_stats(pairs: pd.DataFrame) -> list[tuple[str, str]]:
    """What the supply review finds by Jan 31, as three key numbers."""
    planned = pairs["restock_planned_3m"].sum()
    sent = planned - pairs["restock_not_received"].sum()
    healthy = pairs["status"] == "Healthy"
    healthy_out = (healthy & (pairs["status_jan31"] == "Stockout")).sum()
    out_today, out_jan31 = (pairs["status"] == "Stockout").sum(), (pairs["status_jan31"] == "Stockout").sum()
    return [
        (f"{out_today} → {out_jan31}", "pairs out of stock, today to Jan 31, if no new order lands"),
        (f"{pairs['run_out_date'].notna().sum()}", f"pairs run out at some point, {healthy_out} of today's "
                                                   f"{healthy.sum()} Healthy ones among them"),
        (f"{sent / planned:.0%}", f"of the planned restock the GDC can send: {sent:,.0f} of {planned:,.0f} pieces"),
    ]


def largest_move_action(tables: dict) -> tuple[str, str]:
    """The largest suggested move (the dashboard's worked example), and why the GDC needs an order too."""
    pairs, lines, moves = tables["inventory_health"], tables["supply_review_lines"], tables["stock_moves"]
    move = moves.loc[moves["pieces"].idxmax()]
    part, from_site, to_site = move["part_number"], move["from_site"], move["to_site"]
    target = pairs[(pairs["part_number"] == part) & (pairs["location"] == to_site)].iloc[0]
    po = lines[(lines["part_number"] == part) & (lines["destination"] == to_site)].iloc[0]
    return (f"Move {move['pieces']:,} pieces of {part_label(part)} from the {from_site} to {to_site}",
            f"{to_site} is out all {target['days_without_stock']} days to Jan 31, and still "
            f"{-target['stock_jan31']:,.0f} short after {po['po_number']} lands. The {from_site}'s stock is "
            f"{to_site}'s Feb to Apr restock held early, so order for the {from_site} too.")


def planner_actions(tables: dict) -> list[tuple[str, str]]:
    """What the planner should do, most urgent first: a short headline with the size, then the detail."""
    pairs, lines, shortfalls = tables["inventory_health"], tables["supply_review_lines"], tables["gdc_shortfalls"]
    order_now = shortfalls[shortfalls["next_po_eta"].isna()]
    largest = [part_label(part) for part in order_now.nlargest(3, "worst_backorder")["part_number"]]
    late = lines[lines["review"] == "lands too late"]
    excess = lines[lines["review"] == "adds to excess"]
    no_demand = lines[lines["review"] == "no demand anywhere"]
    dead = pairs[pairs["no_demand_reason"] == DEAD_STOCK]
    return [
        (f"Order now for {len(order_now)} GDC parts", f"They run out with no PO landing after. Expedite the open PO "
         f"for {len(shortfalls) - len(order_now)} more. Largest gaps: {', '.join(largest[:2])} and {largest[2]}."),
        (f"Expedite {(late['source'] == 'Supplier').sum()} supplier POs, bridge "
         f"{(late['source'] == 'GDC Transfer').sum()} transfers", "They land after their site has already run out. "
         "Bridge the transfers with stock from another site."),
        largest_move_action(tables),
        ("Stop adding to excess", f"Push out or cut {len(excess)} PO lines ({excess['pieces_above_26w'].sum():,.0f} "
         f"pieces land above {EXCESS_WEEKS} weeks). Cancel or redirect {len(no_demand)} lines "
         f"({no_demand['qty'].sum():,.0f} pieces) for parts with no demand anywhere."),
        ("Review the dead stock", f"{dead['pallets_on_hand'].sum():,.0f} pallets in {len(dead)} pairs "
         f"({dead['part_number'].nunique()} parts) with no demand anywhere for 24 months: return or scrap."),
    ]


def candidate_moves_note(moves: pd.DataFrame) -> str:
    """The other suggested moves, as candidates to check rather than an action: matched greedily, many off-route."""
    others = moves.drop(moves["pieces"].idxmax())
    against_flow = (others["route"] == "check if a transfer is possible").sum()
    small = (others["pieces"] < 5).sum()
    return (f'<div class="note">The Actions page and the stock_moves sheet list {len(others)} more candidate moves '
            f'({others["pieces"].sum():,} pieces). {against_flow} go against the usual GDC to PDC flow and {small} '
            'are under 5 pieces, so check each one before moving stock.</div>')


def data_questions(tables: dict) -> list[tuple[str, str]]:
    """The data issues that move the numbers most, to raise with whoever owns the data."""
    pairs, flags = tables["inventory_health"], tables["flags"]
    october = flags[flags["issue"] == "runs out before the restock plan starts"]
    october_short = october["note"].str.extract(r"up to ([\d.]+) pieces short")[0].astype(float).sum()
    unknown = sorted(pairs.loc[~pairs["in_part_master"], "part_number"].unique())
    look_alike = (flags["issue"] == "possible double entry").sum() // 2  # each pair flags both of its lines
    return [
        ("October restock is missing from the plan", f"{len(october)} PDC pairs run out before November while the "
                                                     f"GDC holds the part: {october_short:,.0f} pieces short."),
        (f"{len(unknown)} parts are not in the part master", f"{', '.join(unknown[:-1])} and {unknown[-1]}: new "
                                                             "parts, or typos of known ones?"),
        (f"{look_alike} pairs of PO lines look alike", "Possible double entries. Worth a check with the buyer."),
        ("No costs or lead times", "So pallets stand in for money and space."),
    ]


def action_cards(actions: list[tuple[str, str]]) -> str:
    """The planner's actions as numbered cards, most urgent first."""
    cards = "".join(f'<div class="action"><div class="badge">{number}</div><div><span class="action-head">'
                    f'{html.escape(head)}</span><div class="note">{html.escape(text)}</div></div></div>'
                    for number, (head, text) in enumerate(actions, start=1))
    return f'<div class="actions">{cards}</div>'


def question_grid(questions: list[tuple[str, str]]) -> str:
    """The data questions side by side, each a bold headline and one line."""
    items = "".join(f'<div><span class="question-head">{html.escape(head)}</span><div class="note">'
                    f'{html.escape(text)}</div></div>' for head, text in questions)
    return f'<div class="questions">{items}</div>'


def task_supply(tables: dict, actions: list[tuple[str, str]]) -> None:
    """Task 4: what happens once supply lands, the actions in order, and the data questions to raise."""
    lines = tables["supply_review_lines"]
    late = (lines["review"] == "lands too late").sum()
    with st.container(border=True, key="task_4"):
        task_header(4)
        left, right = st.columns([3, 2], gap="large")
        with left:
            kicker("The answer")
            st.markdown("**Every pair is projected day by day to Jan 31.** Customers buy every day, open POs land on "
                        "their ETA, and the PDCs get only the restock the GDC can actually send. Incoming supply "
                        "doesn't fix it:")
            st.html(stat_tiles(projection_stats(tables["inventory_health"])))
        with right:
            why_box("why_4", "Why a dated projection, not WOS after supply", [
                f"A delivery that lands after the shelf is empty doesn't prevent the stockout: {late} of the "
                f"{len(lines)} PO lines land after their site has already run out.",
                f"Jan 31, because all open supply lands by {pd.to_datetime(lines['eta_date']).max():%b %d}, with "
                "almost three weeks left to see its effect.",
            ])
        kicker("What the planner should do, most urgent first")
        st.html(action_cards(actions))
        st.html(candidate_moves_note(tables["stock_moves"]))
        with st.container(key="ask_4"):
            kicker("Raise with the data owners")
            st.html(question_grid(data_questions(tables)))
        where_to_look(4, {"dashboard/pages/actions.py": "Actions",
                          "dashboard/pages/worked_example.py": "Worked example: SKU98"},
                      "Workbook: sheets supply_review_lines, gdc_shortfalls, stock_moves · Summary slides 2 to 5",
                      "5.5")


st.html(STYLE)
st.title("The four tasks")
st.write("Tesla's four tasks, each answered in short: the answer, why it was done this way, and where to find "
         "the detail.")
st.caption("Every number on this page is read from output/ and covers the whole network.")

tables = load_tables()
actions = planner_actions(tables)
at_a_glance(tables, len(actions))
task_wos(tables["inventory_health"])
task_status(tables["inventory_health"], tables["supply_review_lines"])
task_rollup(tables["inventory_health"], tables["dc_summary"])
task_supply(tables, actions)
