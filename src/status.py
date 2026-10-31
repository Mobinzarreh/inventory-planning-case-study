"""Inventory status for each part and location, as defined in ASSUMPTIONS.md 5.3."""
import pandas as pd

from src.clean import FLAG_COLUMNS, clean_all
from src.config import FORECAST_MONTHS, SNAPSHOT_DATE
from src.load_raw import read_all_raw
from src.master_table import build_master
from src.standardize import standardize_all
from src.weeks_of_supply import add_wos, weeks_until_run_out

SHORT_WEEKS = 4  # about one month: most sites are restocked every month
EXCESS_WEEKS = 26  # half a year: above a normal restock delivery for 92% of restock rows
STATUS_ORDER = ["Stockout", "Short", "Healthy", "Excess", "No demand"]
DEAD_STOCK = "dead stock: no demand anywhere in the network"
WANTED_ELSEWHERE = "wanted at another site"
UNKNOWN_PART = "not in the part master (4.10)"


def label_status(master: pd.DataFrame) -> pd.Series:
    """Stockout, Short, Healthy or Excess from WOS, and No demand where there is no WOS."""
    status = pd.Series("Healthy", index=master.index)
    status.loc[master["wos"] > EXCESS_WEEKS] = "Excess"
    status.loc[master["wos"] < SHORT_WEEKS] = "Short"
    status.loc[master["wos"] == 0] = "Stockout"
    status.loc[master["wos"].isna()] = "No demand"
    return status


def label_no_demand_reason(master: pd.DataFrame) -> pd.Series:
    """Why a No demand pair has no demand: nobody wants the part, another site does, or the part is unknown."""
    no_demand = master["status"] == "No demand"
    reason = pd.Series(None, index=master.index, dtype="str")
    reason.loc[no_demand] = DEAD_STOCK
    reason.loc[no_demand & (master["direct_elsewhere_24m"] > 0)] = WANTED_ELSEWHERE
    # 4.10: a missing forecast does not make an unknown part obsolete, so it is not called dead stock
    reason.loc[no_demand & ~master["in_part_master"]] = UNKNOWN_PART
    return reason


def flag_early_run_out(master: pd.DataFrame) -> pd.DataFrame:
    """Pairs whose WOS is at least SHORT_WEEKS but whose stock runs out sooner, because the first months are heavy."""
    candidates = master[master["wos"] >= SHORT_WEEKS].copy()
    candidates["run_out_weeks"] = [
        weeks_until_run_out(on_hand, gap_demand, list(months))
        for on_hand, gap_demand, months in
        zip(candidates["on_hand_qty"], candidates["gap_demand"], candidates[FORECAST_MONTHS].to_numpy())
    ]
    early = candidates[candidates["run_out_weeks"] < SHORT_WEEKS]
    run_out_date = (SNAPSHOT_DATE + pd.to_timedelta(early["run_out_weeks"] * 7, unit="D")).dt.strftime("%Y-%m-%d")
    note = ("WOS " + early["wos"].round(1).astype(str) + " weeks, but at the month-by-month forecast the stock runs out on "
            + run_out_date + " (" + early["run_out_weeks"].round(1).astype(str) + " weeks). Status kept from WOS, "
            + "the supply review checks whether supply or planned restock arrives in time")
    flags = pd.DataFrame({
        "rule": "5.3",
        "issue": "runs out sooner than WOS suggests",
        "file": "inventory",
        "part_number": early["part_number"],
        "location": early["location"],
        "record": "on-hand against the month-by-month forecast",
        "note": note,
    }).reindex(columns=FLAG_COLUMNS)
    print(f"  status: pairs that run out within {SHORT_WEEKS} weeks although WOS is {SHORT_WEEKS} or more: {len(flags)}, flagged")
    return flags


def add_status(master: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add status and no_demand_reason. Returns the table and the 5.3 timing flags."""
    master = master.copy()
    master["status"] = label_status(master)
    master["no_demand_reason"] = label_no_demand_reason(master)
    return master, flag_early_run_out(master)


if __name__ == "__main__":
    clean_tables, flag_list = clean_all(standardize_all(read_all_raw()))
    master, set_aside_flags = build_master(clean_tables)
    master, timing_flags = add_status(add_wos(master))
    print("\npairs by status and location:")
    print(pd.crosstab(master["status"], master["location"], margins=True).reindex(STATUS_ORDER + ["All"]).to_string())
    pallets = master["on_hand_qty"].clip(lower=0) / master["qpc"]
    print("\npallets on hand by status (parts not in the part master left out):")
    print(pallets.groupby(master["status"]).sum().reindex(STATUS_ORDER).round(0).to_string())
    print("\nNo demand reasons:")
    print(master.groupby("no_demand_reason").agg(pairs=("part_number", "size"), pieces=("on_hand_qty", "sum"),
                                                 incoming=("supply_qty", "sum")).to_string())
    print("\ntiming flags:")
    print(timing_flags[["part_number", "location", "note"]].to_string(index=False))
    check_columns = ["part_number", "location", "on_hand_qty", "wos", "status", "no_demand_reason"]
    hand_checks = [("SKU6", "GDC"), ("SKU92", "PDC2"), ("SKU1", "PDC2"), ("SKU16", "PDC3"), ("SKU38", "PDC3"),
                   ("SKU902", "GDC"), ("SKU903", "PDC1"), ("SKU82", "PDC2")]
    print("\nhand checks:")
    for part, location in hand_checks:
        row = master[(master["part_number"] == part) & (master["location"] == location)]
        print(row[check_columns].round(1).to_string(index=False, header=part == "SKU6"))
    print("\nrows:", len(master))
