"""The assistant's four tools. Each returns a short text for the model and an artifact for the page.

The artifact holds the full table, the chart and the SQL, so they reach the planner without passing through the
model. The model only sees the first rows, enough to write its answer.
"""
import json
from dataclasses import dataclass
from typing import Literal

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from langchain.tools import ToolRuntime, tool
from sqlalchemy import Engine

from dashboard.charts import (biggest_stockout_rise, flags_by_issue_chart, most_at_risk, run_out_chart,
                              run_out_takeaway, run_out_weeks, status_change_takeaway, status_counts,
                              status_mix_chart, status_transitions, stockout_shift, stockout_shift_chart,
                              tied_up_chart, tied_up_pallets, tied_up_takeaway, transition_chart, unknown_parts_text)
from dashboard.data import load_clean_demand, load_tables, part_label
from dashboard.part_charts import daily_stock_chart, demand_chart, site_table
from src.charts import INK_SECONDARY, STATUS_COLORS
from src.config import FORECAST_MONTHS, LOCATIONS
from src.standardize import standardize_part_number
from src.status import STATUS_ORDER, label_status
from src.wos import WOS_MONTHS

MODEL_ROWS = 20  # what the model reads: enough to answer, few enough to keep each step quick
PAGE_ROWS = 1000  # what the page shows under an answer
CHART_ROWS = 5000  # more points than this is a query that forgot to group
# for series that aren't statuses: the four reference hues that don't clash with the status colors
# (red, orange, blue and grays), checked for color-blind readers on the dashboard's background
SERIES_COLORS = ["#1baf7a", "#6250d6", "#eda100", "#e87ba4"]
CHART_TITLES = {
    "demand_24m": "Demand, November 2026 to October 2028",
    "stock_to_jan31": "Stock day by day to Jan 31",
    "status_mix": "Inventory status today",
    "stockouts_today_vs_jan31": "Pairs out of stock, today and on Jan 31",
    "status_today_to_jan31": "Where today's pairs end up on Jan 31",
    "pallets_tied_up": "Pallets tied up",
    "run_out_timeline": "When pairs first run out",
    "flags_by_issue": "Data-quality flags by issue",
}
ABBREVIATIONS = {"wos": "WOS", "eta": "ETA", "po": "PO", "26w": "26 weeks", "3m": "3 months", "24m": "24 months"}
COLUMN_LABELS = {"part_description": "Part name"}  # the dashboard's tables call it that too
Site = Literal["GDC", "PDC1", "PDC2", "PDC3"]


def readable(column: str) -> str:
    """A column name as a label: 'wos_after' -> 'WOS after', 'pallets_above_26w' -> 'Pallets above 26 weeks'."""
    if column in COLUMN_LABELS:
        return COLUMN_LABELS[column]
    words = [ABBREVIATIONS.get(word, word) for word in str(column).split("_")]
    label = " ".join(words)
    return label[:1].upper() + label[1:]


@dataclass
class Context:
    """What the tools can reach while the agent runs: the read-only database."""
    engine: Engine


def read_query(query: str, engine: Engine) -> pd.DataFrame:
    """Run one SELECT. Anything else is refused here, on top of the database being read-only."""
    words = query.split(maxsplit=1)
    # a PRAGMA could switch the read-only lock off, so only SELECT and WITH get through
    if not words or words[0].upper() not in ("SELECT", "WITH"):
        raise ValueError("only one SELECT (or WITH ... SELECT) query is allowed")
    return pd.read_sql(query, engine)


def preview(table: pd.DataFrame) -> str:
    """The first rows as CSV for the model, with the row count so it knows when it only sees part of them."""
    rows = table.head(MODEL_ROWS).round(2).to_csv(index=False)
    if len(table) > MODEL_ROWS:
        return (f"{len(table)} rows. You see only the first {MODEL_ROWS} (the planner sees them all), so state no "
                f"largest, smallest, total or trend from them: query for it.\n{rows}")
    return f"{len(table)} rows:\n{rows}"


def table_data(table: pd.DataFrame) -> dict:
    """A table as plain lists, so the chat memory can store it and the page can rebuild it."""
    data = json.loads(table.head(PAGE_ROWS).to_json(orient="split", index=False))
    data["total_rows"] = len(table)
    return data


def known_part(part: str) -> str:
    """The part number in standard form ('sku98' -> 'SKU98'), or an error the model can act on."""
    part = standardize_part_number(part)
    if part not in set(load_tables()["inventory_health"]["part_number"]):
        raise ValueError(f"{part} is not in the data. To find a part by name, query parts.part_description")
    return part


@tool(response_format="content_and_artifact", parse_docstring=True)
def run_sql(query: str, runtime: ToolRuntime[Context]) -> tuple[str, dict | None]:
    """Run one read-only SQLite query on the planning tables. The planner sees the rows as a table.

    Use it for lists, counts, sums and breakdowns. Let the query do every count, sum and share.

    Args:
        query: One SELECT or WITH query. Name the columns you need rather than SELECT *.
    """
    try:
        rows = read_query(query, runtime.context.engine)
    except Exception as error:  # any failure goes back to the model so it can fix its query
        return f"Error: {error}", None
    return preview(rows), {"sql": query, "table": table_data(rows)}


def demand_totals(part: str) -> pd.DataFrame:
    """Pieces per site and demand type: the next 3 months (what WOS uses) and all 24."""
    demand = load_clean_demand()
    rows = demand[demand["part_number"] == part]
    totals = rows[["location", "demand_type", "ship_to"]].copy()
    totals["nov_to_jan"] = rows[WOS_MONTHS].sum(axis=1)
    totals["all_24_months"] = rows[FORECAST_MONTHS].sum(axis=1)
    return totals


def part_chart(name: str, part: str) -> tuple[go.Figure, pd.DataFrame]:
    """One part's chart from the worked example page, with the numbers behind it."""
    tables = load_tables()
    pairs = tables["inventory_health"]
    if name == "demand_24m":
        return demand_chart(load_clean_demand(), pairs, part), demand_totals(part)
    figure = daily_stock_chart(tables["daily_stock"], tables["supply_review_lines"], pairs, part)
    return figure, site_table(pairs, part).reset_index()


def chosen_pairs(where: str | None, engine: Engine) -> pd.DataFrame:
    """The pairs the condition picks, taken from the dashboard's own table so every column keeps its type."""
    pairs = load_tables()["inventory_health"]
    if not where:
        return pairs
    keys = read_query(f"SELECT part_number, location FROM inventory_health WHERE {where}", engine)
    return pairs.merge(keys, on=["part_number", "location"], validate="one_to_one")


def pairs_chart(name: str, pairs: pd.DataFrame, group_by: str) -> tuple[go.Figure, pd.DataFrame, str]:
    """One of the Overview or Actions charts for these pairs, the numbers behind it and its takeaway."""
    if name == "status_mix":
        table = status_counts(pairs, by=group_by)
        return status_mix_chart(table), table, most_at_risk(table)
    if name == "stockouts_today_vs_jan31":
        table = stockout_shift(pairs)
        return stockout_shift_chart(table), table, biggest_stockout_rise(table)
    if name == "status_today_to_jan31":
        table = status_transitions(pairs)
        return transition_chart(table), table, status_change_takeaway(table)
    if name == "pallets_tied_up":
        table = tied_up_pallets(pairs)
        figure, takeaway = tied_up_chart(table), f"{tied_up_takeaway(table)} {unknown_parts_text(pairs)}"
        # the total per site is what "how many no-demand pallets" asks for, so code adds it, not the model
        table["no_demand"] = table["dead_stock"] + table["wanted_elsewhere"]
        return figure, table, takeaway
    table = run_out_weeks(pairs).rename_axis("location")
    return run_out_chart(table), table, run_out_takeaway(table)


def draw_chart(name: str, part: str | None, where: str | None, group_by: str,
               engine: Engine) -> tuple[go.Figure, pd.DataFrame, str, str]:
    """The figure, its numbers, its takeaway and its title, for any ready-made chart."""
    title = CHART_TITLES[name]
    if name in ("demand_24m", "stock_to_jan31"):
        if not part:
            raise ValueError(f"{name} needs a part number")
        part = known_part(part)
        figure, table = part_chart(name, part)
        return figure, table, "", f"{title}: {part_label(part)}"
    if name == "flags_by_issue":
        flags = read_query(f"SELECT * FROM flags WHERE {where or '1 = 1'}", engine)
        table = flags["issue"].value_counts().rename_axis("issue").reset_index(name="flags")
        return flags_by_issue_chart(flags), table, f"{len(flags)} flags in all.", title
    pairs = chosen_pairs(where, engine)
    if pairs.empty:
        raise ValueError("no pairs match that condition")
    figure, table, takeaway = pairs_chart(name, pairs, group_by)
    return figure, table.reset_index(), takeaway, f"{title} ({len(pairs)} pairs)"


@tool(response_format="content_and_artifact", parse_docstring=True)
def show_chart(name: Literal["demand_24m", "stock_to_jan31", "status_mix", "stockouts_today_vs_jan31",
                             "status_today_to_jan31", "pallets_tied_up", "run_out_timeline", "flags_by_issue"],
               runtime: ToolRuntime[Context], part: str | None = None, where: str | None = None,
               group_by: Literal["location", "part_type", "planner"] = "location") -> tuple[str, dict | None]:
    """Draw one of the dashboard's ready-made charts for the planner, with a takeaway written by code.

    For one part (needs part): demand_24m is the 24-month demand at each site, customers and the restock the GDC
    ships to PDCs. stock_to_jan31 is the projected stock day by day to Jan 31 at each site, with arrivals marked.
    For many pairs (where picks them, no where means all 340): status_mix is the share of pairs in each status
    today, one bar per site, part type or planner (group_by). stockouts_today_vs_jan31 compares pairs out of
    stock today and on Jan 31 per site. status_today_to_jan31 shows where today's statuses end up on Jan 31.
    pallets_tied_up is the Excess pallets above the 26-week line and the No demand pallets per site.
    run_out_timeline is the week each pair first runs out, per site. flags_by_issue counts the data-quality flags.

    Args:
        name: Which chart.
        part: Part number for demand_24m and stock_to_jan31, like SKU98.
        where: Optional SQL condition on inventory_health that picks the pairs (on flags for flags_by_issue),
            like "planner LIKE '%okafor%' AND location = 'PDC2'".
        group_by: For status_mix only: one bar per site, part type or planner.
    """
    try:
        figure, table, takeaway, title = draw_chart(name, part, where, group_by, runtime.context.engine)
    except Exception as error:
        return f"Error: {error}", None
    content = f"Chart shown to the planner: {title}.\n"
    if takeaway:
        content += f"Takeaway (written by code, safe to quote): {takeaway}\n"
    artifact = {"title": title, "figure": figure.to_json(), "note": takeaway, "table": table_data(table)}
    if where:
        artifact["sql"] = f"WHERE {where}"
    return content + preview(table), artifact


def category_order(rows: pd.DataFrame, column: str | None) -> list[str] | None:
    """Statuses and sites in their usual order, anything else as it comes."""
    if column is None:
        return None
    values = set(rows[column].dropna())
    for order in (STATUS_ORDER, LOCATIONS):
        if values <= set(order):
            return order
    return None


def custom_chart(rows: pd.DataFrame, kind: str, x: str, y: str, color: str | None) -> go.Figure:
    """A plain bar or line chart in the dashboard's style: statuses in their own colors, other series in fixed order."""
    missing = [column for column in (x, y, color) if column and column not in rows.columns]
    if missing:
        raise ValueError(f"the query has no column {', '.join(missing)}. It returned: {', '.join(rows.columns)}")
    if rows.empty or len(rows) > CHART_ROWS:
        raise ValueError(f"{len(rows):,} rows can't be charted. Group or filter in the query first")
    orders = {}
    for column in (x, color):
        order = category_order(rows, column)
        if order:
            orders[column] = order
    status_series = color is not None and orders.get(color) == STATUS_ORDER
    colors = SERIES_COLORS if color else [INK_SECONDARY]  # one series needs no color of its own
    labels = {column: readable(column) for column in (x, y, color) if column}
    options = {"color": color, "category_orders": orders, "color_discrete_sequence": colors, "labels": labels,
               "color_discrete_map": STATUS_COLORS if status_series else {}}
    if kind == "line":
        figure = px.line(rows, x=x, y=y, markers=len(rows) <= 40, **options)
        figure.update_traces(line={"width": 2})
        figure.update_yaxes(tickformat=",")
    elif rows[x].astype(str).str.len().max() > 12:  # long names read better on horizontal bars
        figure = px.bar(rows, x=y, y=x, orientation="h", **options)
        figure.update_yaxes(autorange="reversed")
        figure.update_xaxes(tickformat=",")
    else:
        figure = px.bar(rows, x=x, y=y, **options)
        figure.update_yaxes(tickformat=",")
    figure.update_layout(height=380, margin={"l": 0, "r": 10, "t": 40, "b": 0}, bargap=0.3,
                         legend={"orientation": "h", "x": 0, "y": 1.02, "yanchor": "bottom", "title": None})
    figure.update_xaxes(showgrid=False)
    return figure


@tool(response_format="content_and_artifact", parse_docstring=True)
def make_chart(query: str, kind: Literal["bar", "line"], x: str, y: str, title: str, runtime: ToolRuntime[Context],
               color: str | None = None) -> tuple[str, dict | None]:
    """Draw a bar or line chart from one SQL query, for anything the ready-made charts don't cover.

    Group and sum in the query, so each bar or point is one row. Statuses get the dashboard's status colors.

    Args:
        query: One SELECT or WITH query that returns the x, y and color columns.
        kind: bar for categories, line for values over time (a month or date column on x).
        x: Column for the x axis: categories, months or dates.
        y: Column with the numbers.
        title: Short title in plain words with the unit, like "Pieces landing per week, by site".
        color: Optional column to split the bars or lines by, like location or status.
    """
    try:
        rows = read_query(query, runtime.context.engine)
        figure = custom_chart(rows, kind, x, y, color)
        # the model may only see part of the rows, so code picks the peaks it is likely to mention
        largest = rows.nlargest(3, y).round(2).to_csv(index=False)
    except Exception as error:
        return f"Error: {error}", None
    artifact = {"title": title, "figure": figure.to_json(), "sql": query, "table": table_data(rows)}
    content = f"Chart shown to the planner: {title}.\nThe 3 largest {y} values, from all rows:\n{largest}\n"
    return content + preview(rows), artifact


def move_outcome(part: str, from_site: str, to_site: str, pieces: int) -> pd.DataFrame:
    """Stock, WOS and status at both sites before and after the move, with the pipeline's own rules."""
    if from_site == to_site or pieces <= 0:
        raise ValueError("a move needs two different sites and more than 0 pieces")
    pairs = load_tables()["inventory_health"]
    rows = pairs[pairs["part_number"] == part].set_index("location")
    for site in (from_site, to_site):
        if site not in rows.index:
            raise ValueError(f"{part} has no row at {site}: no stock and no demand there")
    available = max(rows.at[from_site, "on_hand_qty"], 0)
    if pieces > available:
        raise ValueError(f"{from_site} has only {available:,.0f} pieces of {part} on hand")
    sites = rows.loc[[from_site, to_site]]
    stock_after = sites["on_hand_qty"] + pd.Series([-pieces, pieces], index=sites.index)
    # same as the pipeline (5.2, 4.6): negative stock is a backorder, and no demand means no WOS
    wos_after = (stock_after.clip(lower=0) / sites["weekly_demand"]).where(sites["demand_24m"] > 0)
    return pd.DataFrame({
        "site": sites.index,
        "on_hand_before": sites["on_hand_qty"],
        "on_hand_after": stock_after,
        "weekly_demand": sites["weekly_demand"].round(1),
        "wos_before": sites["wos"],
        "wos_after": wos_after.round(2),
        "status_before": sites["status"],
        "status_after": label_status(pd.DataFrame({"wos": wos_after})),
    }).reset_index(drop=True)


@tool(response_format="content_and_artifact", parse_docstring=True)
def what_if_move(part: str, from_site: Site, to_site: Site, pieces: int) -> tuple[str, dict | None]:
    """Weeks of supply (WOS) and status at both sites if some pieces of a part moved between them today.

    Same WOS formula and status lines as the pipeline. Today's view only: the projection to Jan 31 is not rerun.

    Args:
        part: Part number, like SKU98.
        from_site: The site the pieces leave.
        to_site: The site that receives them.
        pieces: How many whole pieces move.
    """
    try:
        part = known_part(part)
        table = move_outcome(part, from_site, to_site, pieces)
    except ValueError as error:
        return f"Error: {error}", None
    pairs = load_tables()["inventory_health"]
    qpc = pairs.loc[pairs["part_number"] == part, "qpc"].iloc[0]
    pallets = f" ({pieces / qpc:,.1f} pallets)" if qpc > 0 else ""  # parts missing from the master have no qpc
    title = f"What if {pieces:,} pieces{pallets} of {part_label(part)} move from {from_site} to {to_site} today"
    content = f"{title}. Today's view only, the projection to Jan 31 is not rerun.\n{preview(table)}"
    return content, {"title": title, "table": table_data(table)}


TOOLS = [run_sql, show_chart, make_chart, what_if_move]
