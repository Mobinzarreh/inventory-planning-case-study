"""Plotly charts for the dashboard, and the one-line takeaway under each.

Colors and labels come from src/status_chart.py, so the PNG and the app match.
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from dashboard.data import NOT_IN_MASTER
from src.status_chart import (AXIS_LINE, INK, INK_MUTED, INK_SECONDARY, LABEL_INK, LEGEND_TEXT, MIN_LABEL_SHARE,
                        STATUS_COLORS, SURFACE)
from src.config import LOCATIONS, SNAPSHOT_DATE
from src.status import DEAD_STOCK, STATUS_ORDER, WANTED_ELSEWHERE
from src.supply_review import END_DATE

NO_TOOLBAR = {"displayModeBar": False}  # the zoom and pan buttons only get in the way here
# Counts in the transition grid get a neutral gray scale: blue and orange already mean Excess and Short
COUNT_SCALE = [[0, "#f1f0ec"], [1, INK_SECONDARY]]
# Running out is the Stockout story, so the run-out grid goes from a light tint to the full Stockout red
RUN_OUT_SCALE = [[0, "#f6d8d5"], [1, STATUS_COLORS["Stockout"]]]
# column: (legend text, fill, edge, label ink). Wanted elsewhere is a kind of No demand, so the same gray, but only
# as an outline: that stock could still move to the site that needs it
TIED_UP = {
    "above_26w": ("Excess, above the 26-week line", STATUS_COLORS["Excess"], SURFACE, "#ffffff"),
    "dead_stock": ("No demand: dead stock", STATUS_COLORS["No demand"], SURFACE, "#ffffff"),
    "wanted_elsewhere": ("No demand: wanted at another site", SURFACE, STATUS_COLORS["No demand"], INK),
}


def status_counts(pairs: pd.DataFrame, by: str = "location") -> pd.DataFrame:
    """Pairs in each status per site (or per part type or planner), plus a total row, in the order the chart shows them."""
    # parts missing from the part master have no part type or planner: they get their own row rather than drop out
    groups = pairs[by].fillna(NOT_IN_MASTER)
    counts = pd.crosstab(groups, pairs["status"])
    counts = counts.reindex(columns=STATUS_ORDER, fill_value=0)
    if by == "location":
        sites = [site for site in LOCATIONS if site in counts.index]
        counts = counts.reindex(sites)
        counts.loc["All sites"] = counts.sum()
    else:
        counts.loc["All"] = counts.sum()
    return counts


def status_mix_chart(counts: pd.DataFrame) -> go.Figure:
    """One 100% bar per site, the same view as status_mix_by_dc.png, with the pair counts on hover."""
    shares = counts.div(counts.sum(axis=1), axis=0)
    fig = go.Figure()
    for status in STATUS_ORDER:
        # a segment too narrow for its label leaves it to the hover and the legend, as in the PNG
        labels = [f"{share:.0%}" if share >= MIN_LABEL_SHARE else "" for share in shares[status]]
        fig.add_bar(y=shares.index, x=shares[status], name=LEGEND_TEXT[status], orientation="h",
                    marker={"color": STATUS_COLORS[status], "line": {"color": SURFACE, "width": 1.5}},
                    text=labels, textposition="inside", insidetextanchor="middle",
                    textfont={"color": LABEL_INK[status]}, customdata=counts[status],
                    hovertemplate=f"%{{y}}: %{{customdata}} pairs {status} (%{{x:.0%}})<extra></extra>")
    for site, total in counts.sum(axis=1).items():
        unit = "pair" if total == 1 else "pairs"  # a group can hold a single pair, like a part type at one site
        fig.add_annotation(x=1.01, y=site, xref="paper", text=f"{total} {unit}", showarrow=False,
                           xanchor="left", font={"color": INK_MUTED, "size": 12})
    fig.update_layout(barmode="stack", height=90 + 46 * len(counts), margin={"l": 0, "r": 70, "t": 40, "b": 0},
                      xaxis={"tickformat": ".0%", "range": [0, 1], "showgrid": False},
                      yaxis={"autorange": "reversed"},
                      legend={"orientation": "h", "x": 0, "y": 1.02, "yanchor": "bottom", "traceorder": "normal"})
    return fig


def most_at_risk(counts: pd.DataFrame) -> str:
    """One sentence naming the site (or group) with the largest share of pairs that are out of stock or short."""
    sites = counts.iloc[:-1]  # the last row is the total
    at_risk = sites["Stockout"] + sites["Short"]
    share = at_risk / sites.sum(axis=1)
    site = share.idxmax()
    sentence = f"{at_risk[site]} of {sites.loc[site].sum()} pairs ({share[site]:.0%}) are out of stock or short."
    if len(sites) == 1:  # nothing to compare with
        return f"At **{site}**, {sentence}"
    return f"Most at risk: **{site}**, where {sentence}"


def stockout_shift(pairs: pd.DataFrame) -> pd.DataFrame:
    """Pairs out of stock at each site, today and on Jan 31."""
    out_today = (pairs["status"] == "Stockout").groupby(pairs["location"]).sum()
    out_jan31 = (pairs["status_jan31"] == "Stockout").groupby(pairs["location"]).sum()
    shift = pd.DataFrame({"today": out_today, "jan31": out_jan31})
    sites = [site for site in LOCATIONS if site in shift.index]
    return shift.reindex(sites)


def stockout_shift_chart(shift: pd.DataFrame) -> go.Figure:
    """A dumbbell per site: out-of-stock pairs today (gray dot) and on Jan 31 (red dot), counts at each end."""
    sites = list(shift.index)
    line_x, line_y = [], []
    for site in sites:
        line_x += [shift.at[site, "today"], shift.at[site, "jan31"], None]  # None breaks the line between sites
        line_y += [site, site, None]
    rising = shift["jan31"] >= shift["today"]
    today_side = ["middle left" if up else "middle right" for up in rising]  # each count sits on the outside
    jan31_side = ["middle right" if up else "middle left" for up in rising]
    fig = go.Figure()
    fig.add_scatter(x=line_x, y=line_y, mode="lines", line={"color": AXIS_LINE, "width": 2}, hoverinfo="skip",
                    showlegend=False)
    fig.add_scatter(x=shift["today"], y=sites, mode="markers+text", name="Today", text=shift["today"],
                    textposition=today_side, textfont={"color": INK_SECONDARY},
                    marker={"color": INK_MUTED, "size": 13, "line": {"color": SURFACE, "width": 2}},
                    hovertemplate="%{y}: %{x} pairs out of stock today<extra></extra>")
    fig.add_scatter(x=shift["jan31"], y=sites, mode="markers+text", name="Jan 31", text=shift["jan31"],
                    textposition=jan31_side, textfont={"color": INK},
                    marker={"color": STATUS_COLORS["Stockout"], "size": 13, "line": {"color": SURFACE, "width": 2}},
                    hovertemplate="%{y}: %{x} pairs out of stock on Jan 31<extra></extra>")
    top = max(shift.max().max(), 1)
    fig.update_layout(height=110 + 50 * len(sites), margin={"l": 0, "r": 10, "t": 40, "b": 0},
                      xaxis={"title": "Pairs out of stock", "range": [-0.08 * top, 1.15 * top], "zeroline": False},
                      yaxis={"autorange": "reversed"},
                      legend={"orientation": "h", "x": 0, "y": 1.02, "yanchor": "bottom"})
    return fig


def biggest_stockout_rise(shift: pd.DataFrame) -> str:
    """One sentence on the total change in out-of-stock pairs and the site where it grows most."""
    rise = shift["jan31"] - shift["today"]
    sentence = f"Out of stock goes from {shift['today'].sum()} pairs today to {shift['jan31'].sum()} on Jan 31."
    if rise.max() <= 0 or len(shift) == 1:
        return sentence
    site = rise.idxmax()
    return sentence + f" **{site}** adds the most: {shift.at[site, 'today']} → {shift.at[site, 'jan31']}."


def status_transitions(pairs: pd.DataFrame) -> pd.DataFrame:
    """Pairs by status today (rows) and status on Jan 31 (columns)."""
    table = pd.crosstab(pairs["status"], pairs["status_jan31"])
    return table.reindex(index=STATUS_ORDER, columns=STATUS_ORDER, fill_value=0)


def transition_chart(table: pd.DataFrame) -> go.Figure:
    """Where today's pairs end up on Jan 31: one cell per pair of statuses, darker for more pairs."""
    labels = table.astype(str).where(table > 0, "")  # empty cells stay blank instead of showing 0
    grid = go.Heatmap(z=table.values, x=STATUS_ORDER, y=STATUS_ORDER, text=labels.values, texttemplate="%{text}",
                      colorscale=COUNT_SCALE, showscale=False, xgap=2, ygap=2,
                      hovertemplate="%{y} today → %{x} on Jan 31: %{z} pairs<extra></extra>")
    fig = go.Figure(grid)
    fig.update_layout(height=330, margin={"l": 0, "r": 10, "t": 10, "b": 0},
                      xaxis={"title": "Status on Jan 31", "showgrid": False},
                      yaxis={"title": "Status today", "autorange": "reversed", "showgrid": False})
    return fig


def status_change_takeaway(table: pd.DataFrame) -> str:
    """Two sentences: Healthy pairs that still run out, and out-of-stock pairs that recover."""
    sentences = []
    healthy = table.loc["Healthy"].sum()
    if healthy:
        sentences.append(f"**{table.at['Healthy', 'Stockout']} of today's {healthy} Healthy pairs** are out of "
                         "stock on Jan 31, so a healthy WOS today doesn't mean a part is safe.")
    out_today = table.loc["Stockout"].sum()
    if out_today:
        recovered = out_today - table.at["Stockout", "Stockout"]
        sentences.append(f"{recovered} of today's {out_today} out-of-stock pairs recover once supply lands.")
    return " ".join(sentences)


def tied_up_pallets(pairs: pd.DataFrame) -> pd.DataFrame:
    """Pallet equivalents tied up at each site, split the same way as dc_summary (src/dc_summary.py)."""
    reason = pairs["no_demand_reason"]
    pallets = pd.DataFrame({
        "above_26w": pairs["pallets_above_26w"],
        "dead_stock": pairs["pallets_on_hand"].where(reason == DEAD_STOCK, 0),
        "wanted_elsewhere": pairs["pallets_on_hand"].where(reason == WANTED_ELSEWHERE, 0),
        "on_hand": pairs["pallets_on_hand"],
    })
    pallets = pallets.groupby(pairs["location"]).sum()
    sites = [site for site in LOCATIONS if site in pallets.index]
    return pallets.reindex(sites)


def tied_up_chart(pallets: pd.DataFrame) -> go.Figure:
    """One stacked bar per site of the pallets tied up, with the total at the end of each bar."""
    fig = go.Figure()
    for column, (label, fill, edge, ink) in TIED_UP.items():
        share = pallets[column] / pallets["on_hand"]
        width = pallets[column].where(pallets[column] > 0)  # a zero segment would still draw its outline
        fig.add_bar(y=pallets.index, x=width, name=label, orientation="h", customdata=share,
                    marker={"color": fill, "line": {"color": edge, "width": 1.5}}, texttemplate="%{x:,.0f}",
                    textposition="inside", insidetextanchor="middle", textfont={"color": ink},
                    hovertemplate=f"%{{y}}: %{{x:,.0f}} pallets, {label} "
                                  "(%{customdata:.0%} of the site's pallets on hand)<extra></extra>")
    totals = pallets[list(TIED_UP)].sum(axis=1)
    for site, total in totals.items():
        fig.add_annotation(x=total, y=site, text=f"{total:,.0f}", showarrow=False, xanchor="left", xshift=6,
                           font={"color": INK_SECONDARY, "size": 12})
    fig.update_layout(barmode="stack", height=110 + 50 * len(pallets), margin={"l": 0, "r": 40, "t": 40, "b": 0},
                      xaxis={"title": "Pallet equivalents"}, yaxis={"autorange": "reversed"},
                      uniformtext={"minsize": 10, "mode": "hide"},  # hide a count that doesn't fit its segment
                      legend={"orientation": "h", "x": 0, "y": 1.02, "yanchor": "bottom", "traceorder": "normal"})
    return fig


def unknown_parts_text(pairs: pd.DataFrame) -> str:
    """Parts missing from the part master have no qpc, so the pallet chart leaves them out. This says so, in pieces."""
    unknown = pairs[~pairs["in_part_master"]]
    if unknown.empty:
        return ""
    names = ", ".join(sorted(unknown["part_number"]))
    return (f"Not in the chart: {names}, which are missing from the part master. They have "
            f"{unknown['on_hand_qty'].sum():,.0f} pieces on hand and {unknown['supply_qty'].sum():,.0f} incoming, "
            "and no qpc to turn into pallets.")


def tied_up_takeaway(pallets: pd.DataFrame) -> str:
    """One or two sentences: where the Excess pallets sit, and how much No demand stock there is."""
    above = pallets["above_26w"]
    sentences = []
    if len(pallets) == 1:
        sentences.append(f"{above.sum():,.0f} Excess pallets sit above the 26-week line.")
    elif above.sum() > 0:
        site = above.idxmax()
        sentences.append(f"**{site}** holds {above[site]:,.0f} of the {above.sum():,.0f} Excess pallets "
                         "above the 26-week line.")
    sentences.append(f"No demand ties up {pallets['dead_stock'].sum():,.0f} pallets of dead stock and "
                     f"{pallets['wanted_elsewhere'].sum():,.0f} wanted at another site.")
    return " ".join(sentences)


def flags_by_issue_chart(flags: pd.DataFrame) -> go.Figure:
    """Flags per issue, the biggest at the top, one bar each with its count at the end."""
    counts = flags["issue"].value_counts().sort_values()
    bars = go.Bar(x=counts.values, y=counts.index, orientation="h", marker_color=INK_SECONDARY, text=counts.values,
                  textposition="outside", textfont={"color": INK_SECONDARY},
                  hovertemplate="%{y}: %{x} flags<extra></extra>")
    fig = go.Figure(bars)
    fig.update_layout(height=60 + 32 * len(counts), margin={"l": 0, "r": 10, "t": 10, "b": 0}, bargap=0.45,
                      xaxis={"title": "Flags", "range": [0, counts.max() * 1.12], "showgrid": False})
    return fig


def run_out_weeks(pairs: pd.DataFrame) -> pd.DataFrame:
    """Pairs that run out by Jan 31, by site (rows) and week (columns). The snapshot day gets its own column."""
    first_day = SNAPSHOT_DATE + pd.Timedelta(days=1)
    snapshot_day = SNAPSHOT_DATE.strftime("%b %d")
    weeks = pd.date_range(first_day, END_DATE, freq="7D").strftime("%b %d").tolist()  # weeks start on the 23rd
    sites = [site for site in LOCATIONS if site in set(pairs["location"])]
    out = pairs[pairs["run_out_date"].notna()]
    if out.empty:
        return pd.DataFrame(0, index=sites, columns=[snapshot_day] + weeks)
    days_after = (pd.to_datetime(out["run_out_date"]) - SNAPSHOT_DATE).dt.days
    week_start = first_day + pd.to_timedelta((days_after - 1) // 7 * 7, unit="D")
    week = week_start.dt.strftime("%b %d").where(days_after > 0, snapshot_day)
    table = pd.crosstab(out["location"], week)
    return table.reindex(index=sites, columns=[snapshot_day] + weeks, fill_value=0)


def run_out_chart(weeks: pd.DataFrame) -> go.Figure:
    """A site by week grid of the pairs that first run out, darker for more. The snapshot day is the first column."""
    labels = weeks.astype(str).where(weeks > 0, "")
    when = [f"miss demand on {weeks.columns[0]}, the first day"] + [f"first run out in the week of {week}"
                                                                   for week in weeks.columns[1:]]
    weekly_top = max(weeks.max().max(), 1)
    grid = go.Heatmap(z=weeks.where(weeks > 0).values, x=list(weeks.columns), y=list(weeks.index),
                      text=labels.values, texttemplate="%{text}", customdata=np.tile(when, (len(weeks), 1)),
                      colorscale=RUN_OUT_SCALE, zmin=1, zmax=weekly_top, showscale=False, xgap=2, ygap=2,
                      hovertemplate="%{y}: %{z} pairs %{customdata}<extra></extra>")
    fig = go.Figure(grid)
    fig.update_layout(height=70 + 48 * len(weeks), margin={"l": 0, "r": 10, "t": 10, "b": 0},
                      xaxis={"type": "category", "showgrid": False, "title": "Week the pair first runs out"},
                      yaxis={"type": "category", "autorange": "reversed", "showgrid": False})
    return fig


def run_out_takeaway(weeks: pd.DataFrame) -> str:
    """One sentence on how many pairs miss demand on the first day and how many follow in the next 4 weeks."""
    four_weeks_on = (SNAPSHOT_DATE + pd.Timedelta(days=28)).strftime("%b %d")
    first_day = weeks.iloc[:, 0].sum()
    next_four = weeks.iloc[:, 1:5].sum().sum()
    return (f"Of the {weeks.sum().sum()} pairs that run out by Jan 31, "
            f"**{first_day + next_four} do so within 4 weeks** "
            f"(by {four_weeks_on}): {first_day} miss demand on {weeks.columns[0]} and {next_four} follow.")
