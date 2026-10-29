"""DC-level summary: one row per location, as defined in ASSUMPTIONS.md 5.4."""
import math

import pandas as pd

from src.clean import clean_all
from src.config import LOCATIONS
from src.load_raw import read_all_raw
from src.master import build_master
from src.standardize import standardize_all
from src.status import DEAD_STOCK, EXCESS_WEEKS, STATUS_ORDER, WANTED_ELSEWHERE, add_status
from src.wos import add_wos

WATCH_WEEKS = 13  # Healthy pairs above this go on the watch list (5.3)


def add_pallet_equivalents(master: pd.DataFrame) -> pd.DataFrame:
    """Pallet equivalents on hand and above the 26-week line. Blank for parts not in the part master (4.10)."""
    master = master.copy()
    stock = master["on_hand_qty"].clip(lower=0)
    # only Excess pairs have stock above the line; No demand stock is counted separately, never twice
    above_line = (stock - EXCESS_WEEKS * master["weekly_demand"]).clip(lower=0).where(master["status"] == "Excess", 0)
    master["pallets_on_hand"] = stock / master["qpc"]
    master["pallets_above_26w"] = above_line / master["qpc"]
    return master


def count_statuses(master: pd.DataFrame) -> pd.DataFrame:
    """Pairs in each status per DC, as counts and as shares of all pairs at that DC."""
    counts = pd.crosstab(master["location"], master["status"])
    counts = counts.reindex(index=LOCATIONS, columns=STATUS_ORDER, fill_value=0)
    counts.loc["All sites"] = counts.sum()
    shares = counts.div(counts.sum(axis=1), axis=0)
    counts.columns = [status.lower().replace(" ", "_") for status in STATUS_ORDER]
    shares.columns = [f"{column}_share" for column in counts.columns]
    counts.insert(0, "pairs", counts.sum(axis=1))
    return pd.concat([counts, shares], axis=1)


def space_and_actions(master: pd.DataFrame, timing_flags: pd.DataFrame) -> pd.DataFrame:
    """Pallet equivalents per DC, plus the watch list and timing flags, which overlap the statuses."""
    reason = master["no_demand_reason"]
    on_watch = (master["status"] == "Healthy") & (master["wos"] > WATCH_WEEKS)
    columns = pd.DataFrame({
        "location": master["location"],
        "pallets_on_hand": master["pallets_on_hand"],
        "pallets_above_26w": master["pallets_above_26w"],
        "dead_stock_pallets": master["pallets_on_hand"].where(reason == DEAD_STOCK, 0),
        "wanted_elsewhere_pallets": master["pallets_on_hand"].where(reason == WANTED_ELSEWHERE, 0),
        # 4.10: no qpc for these parts, so they are reported in pieces only
        "unknown_part_pieces": master["on_hand_qty"].where(~master["in_part_master"], 0),
        "watch_list": on_watch.astype(int),
    })
    by_dc = columns.groupby("location").sum().reindex(LOCATIONS)
    by_dc["timing_flags"] = timing_flags["location"].value_counts().reindex(LOCATIONS, fill_value=0)
    by_dc.loc["All sites"] = by_dc.sum()
    return by_dc


def build_dc_summary(master: pd.DataFrame, timing_flags: pd.DataFrame) -> pd.DataFrame:
    """One row per DC plus all sites: status mix first, then space, then the overlapping action counts."""
    master = add_pallet_equivalents(master)
    summary = count_statuses(master).join(space_and_actions(master, timing_flags))
    # the all-sites row must add back to the master table, or a pair was lost or counted twice
    if summary.loc["All sites", "pairs"] != len(master):
        raise ValueError("DC summary pairs do not add up to the master table")
    if not math.isclose(summary.loc["All sites", "pallets_on_hand"], master["pallets_on_hand"].sum()):
        raise ValueError("DC summary pallets do not add up to the master table")
    print(f"  DC summary: {len(LOCATIONS)} DCs, {len(master)} pairs, "
          f"{summary.loc['All sites', 'pallets_on_hand']:,.0f} pallet equivalents on hand, totals match")
    return summary.rename_axis("location")


if __name__ == "__main__":
    clean_tables, flag_list = clean_all(standardize_all(read_all_raw()))
    master, set_aside_flags = build_master(clean_tables)
    master, timing_flags = add_status(add_wos(master))
    dc_summary = build_dc_summary(master, timing_flags)
    print("\nDC summary (one column per DC here, one row per DC in the output):")
    print(dc_summary.T.round(2).to_string())
