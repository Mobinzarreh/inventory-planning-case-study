"""Charts and tables for one part across its sites: stock and status, the day-by-day projection to Jan 31,
and the 24-month demand. The worked example and the part picker both use them."""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.status_chart import INK, INK_MUTED, INK_SECONDARY
from src.config import FORECAST_MONTHS, LOCATIONS

BACKORDER_FILL = "rgba(208, 59, 59, 0.15)"  # a light Stockout red: stock below zero is demand not met
CUSTOMER_COLOR = INK_SECONDARY
RESTOCK_COLOR = INK_MUTED


def part_order(part: str) -> int:
    """Sort key so SKU2 comes before SKU10."""
    return int(part.removeprefix("SKU"))


def part_sites(pairs: pd.DataFrame, part: str) -> list[str]:
    """The sites where this part has a row, in the usual order."""
    present = set(pairs.loc[pairs["part_number"] == part, "location"])
    return [site for site in LOCATIONS if site in present]


def site_table(pairs: pd.DataFrame, part: str) -> pd.DataFrame:
    """One row per site: stock, WOS and status today, and what the projection says by Jan 31."""
    rows = pairs[pairs["part_number"] == part].set_index("location").reindex(part_sites(pairs, part))
    table = pd.DataFrame({
        "On hand": rows["on_hand_qty"].round().astype("Int64"),
        "Nov to Jan demand": rows["demand_3m"].round().astype("Int64"),
        "Weekly demand": rows["weekly_demand"].round(1),
        "WOS": rows["wos"].round(1),
        "Status today": rows["status"],
        "Incoming": rows["supply_qty"].round().astype("Int64"),
        "Runs out": rows["run_out_date"].fillna("not by Jan 31"),
        "Stock on Jan 31": rows["stock_jan31"].round().astype("Int64"),
        "Status on Jan 31": rows["status_jan31"],
    })
    return table.rename_axis("Site")


def add_stock_line(fig: go.Figure, stock: pd.DataFrame, column: int) -> None:
    """The projected stock as a line, with the part below zero shaded as unmet demand."""
    backorder = stock["projected_stock"].clip(upper=0)
    fig.add_scatter(x=stock["date"], y=backorder, fill="tozeroy", fillcolor=BACKORDER_FILL, line={"width": 0},
                    hoverinfo="skip", row=1, col=column)
    fig.add_scatter(x=stock["date"], y=stock["projected_stock"], mode="lines", line={"color": INK, "width": 2},
                    hovertemplate="%{x|%b %d}: %{y:,.0f} pieces<extra></extra>", row=1, col=column)


def add_arrivals(fig: go.Figure, stock: pd.DataFrame, arrivals: pd.DataFrame, column: int) -> None:
    """A marker and label for each open PO line or transfer, on the day it lands."""
    if arrivals.empty:
        return
    stock_on_day = stock.set_index("date")["projected_stock"].reindex(arrivals["eta_date"])
    labels = [f"+{qty:,.0f}" for qty in arrivals["qty"]]
    fig.add_scatter(x=arrivals["eta_date"], y=stock_on_day.values, mode="markers+text", text=labels,
                    textposition="top center", textfont={"color": INK_SECONDARY, "size": 11},
                    marker={"symbol": "triangle-up", "size": 11, "color": INK},
                    customdata=arrivals[["po_number", "source"]].values,
                    hovertemplate="%{customdata[0]} (%{customdata[1]}): %{text} pieces land on %{x|%b %d}"
                                  "<extra></extra>", row=1, col=column)


def daily_stock_chart(daily: pd.DataFrame, lines: pd.DataFrame, pairs: pd.DataFrame, part: str) -> go.Figure:
    """One panel per site: projected stock each day to Jan 31, below zero shaded, arrivals marked."""
    sites = part_sites(pairs, part)
    fig = make_subplots(rows=1, cols=len(sites), subplot_titles=sites, horizontal_spacing=0.07)
    for column, site in enumerate(sites, start=1):
        stock = daily[(daily["part_number"] == part) & (daily["location"] == site)]
        add_stock_line(fig, stock, column)
        arrivals = lines[(lines["part_number"] == part) & (lines["destination"] == site)]
        add_arrivals(fig, stock, arrivals, column)
    # each site keeps its own scale (a PDC can be 30 times smaller than the GDC), so read the axis numbers
    fig.update_xaxes(tickformat="%b %d", nticks=5, showgrid=False)
    fig.update_yaxes(zeroline=True, zerolinecolor=INK_MUTED, zerolinewidth=1, tickformat=",")
    fig.update_layout(height=330, showlegend=False, margin={"l": 0, "r": 10, "t": 40, "b": 0})
    return fig


def demand_chart(demand: pd.DataFrame, pairs: pd.DataFrame, part: str) -> go.Figure:
    """One panel per site: 24 months of demand. At the GDC, its own customers plus the restock it ships to PDCs."""
    sites = part_sites(pairs, part)
    rows = demand[demand["part_number"] == part]
    fig = make_subplots(rows=1, cols=len(sites), subplot_titles=sites, horizontal_spacing=0.07)
    for column, site in enumerate(sites, start=1):
        at_site = rows[rows["location"] == site]
        customers = at_site.loc[at_site["demand_type"] == "Direct", FORECAST_MONTHS].sum()
        restock = at_site.loc[at_site["demand_type"] == "Replenishment", FORECAST_MONTHS].sum()
        fig.add_bar(x=FORECAST_MONTHS, y=customers, name="Customers at the site", marker_color=CUSTOMER_COLOR,
                    legendgroup="customers", showlegend=column == 1, row=1, col=column,
                    hovertemplate="%{x|%b %Y}: %{y:,.0f} pieces for customers<extra></extra>")
        if restock.sum() > 0:
            fig.add_bar(x=FORECAST_MONTHS, y=restock, name="Restock the GDC ships to PDCs",
                        marker_color=RESTOCK_COLOR, legendgroup="restock", showlegend=True, row=1, col=column,
                        hovertemplate="%{x|%b %Y}: %{y:,.0f} pieces of restock<extra></extra>")
    fig.update_xaxes(tickformat="%b %y", nticks=6, showgrid=False)
    fig.update_yaxes(tickformat=",", rangemode="tozero")
    fig.update_layout(barmode="stack", bargap=0.15, height=330, margin={"l": 0, "r": 10, "t": 60, "b": 0},
                      legend={"orientation": "h", "x": 0, "y": 1.12, "yanchor": "bottom"})
    return fig
