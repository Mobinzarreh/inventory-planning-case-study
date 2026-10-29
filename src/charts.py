"""Charts for the summary, saved as PNG files in output/."""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # write files only, never open a window
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Patch

from src.config import LOCATIONS, ROOT
from src.status import STATUS_ORDER

# Too little stock in warm colors, Healthy as a neutral gray (nothing to act on), too much in blue, and
# No demand as a darker gray off the scale. Checked for color-blind readers: neighbours stay apart.
STATUS_COLORS = {"Stockout": "#d03b3b", "Short": "#ec835a", "Healthy": "#c3c2b7", "Excess": "#2a78d6", "No demand": "#898781"}
LABEL_INK = {"Stockout": "#ffffff", "Short": "#ffffff", "Healthy": "#0b0b0b", "Excess": "#ffffff", "No demand": "#ffffff"}
LEGEND_TEXT = {"Stockout": "Stockout (no stock)", "Short": "Short (under 4 weeks)", "Healthy": "Healthy (4 to 26 weeks)",
               "Excess": "Excess (over 26 weeks)", "No demand": "No demand"}
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
AXIS_LINE = "#c3c2b7"
MIN_LABEL_SHARE = 0.045  # a segment narrower than this has no room for its label, the legend and table carry it


def draw_status_bar(ax, row_y: float, shares: pd.Series) -> None:
    """One 100% bar, segments in status order, each labelled with its share when the label fits."""
    left = 0.0
    for status in STATUS_ORDER:
        share = shares[status]
        # a surface-colored edge leaves a thin gap between segments instead of a drawn border
        ax.barh(row_y, share, left=left, height=0.62, color=STATUS_COLORS[status], edgecolor=SURFACE, linewidth=1.5)
        if share >= MIN_LABEL_SHARE:
            ax.text(left + share / 2, row_y, f"{share:.0%}", ha="center", va="center", fontsize=8,
                    color=LABEL_INK[status])
        left += share


def style_axes(ax, row_positions: list[float], row_names: list[str]) -> None:
    """Quiet axes: site names on the left, a hairline baseline, percent ticks in muted ink."""
    ax.set_facecolor(SURFACE)
    ax.set_xlim(0, 1)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1])
    ax.set_xticklabels(["0%", "25%", "50%", "75%", "100%"], color=INK_MUTED, fontsize=8)
    ax.set_yticks(row_positions)
    ax.set_yticklabels(row_names, color=INK, fontsize=9)
    ax.invert_yaxis()  # GDC on top, all sites at the bottom
    ax.tick_params(length=0)
    for side in ["top", "right", "left"]:
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS_LINE)
    ax.spines["bottom"].set_linewidth(0.5)


def plot_status_mix(dc_summary: pd.DataFrame, path: Path) -> None:
    """One 100% stacked bar per DC, plus all sites: the share of part and location pairs in each status (5.4)."""
    sites = dc_summary.reindex(LOCATIONS + ["All sites"])
    shares = sites[[status.lower().replace(" ", "_") + "_share" for status in STATUS_ORDER]]
    shares.columns = STATUS_ORDER
    row_positions = [0, 1, 2, 3, 4.35]  # a little extra space sets the all-sites bar apart
    fig, ax = plt.subplots(figsize=(8, 3.8), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    for row_y, site in zip(row_positions, sites.index):
        draw_status_bar(ax, row_y, shares.loc[site])
        ax.text(1.015, row_y, f"{int(sites.loc[site, 'pairs'])} pairs", va="center", fontsize=8, color=INK_MUTED)
    style_axes(ax, row_positions, list(sites.index))
    handles = [Patch(color=STATUS_COLORS[status], label=LEGEND_TEXT[status]) for status in STATUS_ORDER]
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0, 1.02), ncol=5, frameon=False, fontsize=7.5,
              handlelength=1.2, handletextpad=0.5, columnspacing=1.2, labelcolor=INK_SECONDARY)
    fig.text(0.02, 0.97, "Inventory status by site", fontsize=12, color=INK, weight="bold", va="top")
    fig.text(0.02, 0.905, "Share of part and location pairs in each status. Stock on 2026-10-22, "
             "weeks of supply on Nov to Jan demand.", fontsize=8.5, color=INK_SECONDARY, va="top")
    fig.subplots_adjust(left=0.1, right=0.9, top=0.76, bottom=0.1)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    print(f"  wrote {path.relative_to(ROOT)}")
