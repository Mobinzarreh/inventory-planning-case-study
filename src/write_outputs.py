"""The output files: one row per part and location with WOS, status and the projection to Jan 31,
the DC summary, the supply review tables and the full flag list. Each is written as a CSV in
output/, and all of them together as one Excel workbook. The day-by-day stock is CSV only."""
import pandas as pd
from xlsxwriter.utility import xl_col_to_name

from src.clean import FLAG_COLUMNS
from src.config import FORECAST_MONTHS, OUTPUT_DIR, ROOT
from src.dc_summary import WATCH_WEEKS, add_pallet_equivalents

WORKBOOK = "inventory_health_check.xlsx"
CSV_ONLY = ["daily_stock"]  # 34,680 rows: data for the dashboard's charts, too long to be a useful sheet
PAIR_COLUMNS = [
    # the part and site
    "part_number", "location", "part_description", "part_type", "planner", "qpc", "qpc_is_estimate", "in_part_master",
    # tasks 1 and 2: today's stock, WOS and status (5.2, 5.3)
    "on_hand_qty", "pallets_on_hand", "demand_3m", "weekly_demand", "wos", "status", "no_demand_reason",
    "on_watch_list", "timing_flag", "pallets_above_26w",
    # task 4: open supply and the projection to Jan 31 (5.5)
    "supply_lines", "supply_qty", "supplier_qty", "transfer_qty", "first_eta", "restock_planned_3m",
    "restock_not_received", "run_out_date", "days_without_stock", "worst_backorder", "stock_jan31", "wos_jan31",
    "status_jan31",
    # flags on this pair
    "flags",
]
# the data behind the numbers: in the CSV, which the dashboard and the agent read, but not on the Excel sheet,
# where a planner reads the columns above
DETAIL_COLUMNS = ["pair_group", "has_inventory_row", "direct_24m", "restock_out_24m", "demand_24m",
                  "direct_elsewhere_24m", "restock_in_24m", "direct_first_month"] + FORECAST_MONTHS
PAIR_COLUMNS = PAIR_COLUMNS + DETAIL_COLUMNS
LEFT_OUT = ["gap_demand"]  # a working column of the run-out walk, the same as direct_first_month for 10 days
LINE_COLUMNS = [
    "po_number", "po_line", "part_number", "destination", "source", "qty", "pallets", "eta_date", "review", "action",
    "status", "status_jan31", "run_out_date", "short_when_landing", "days_without_stock_before", "pieces_above_26w",
    "stock_jan31", "no_demand_reason", "qpc",
]
# labels that look like numbers but are text on purpose: as a number, rule 4.10 would read 4.1
TEXT_LABELS = ["po_line", "rule"]


def build_flag_list(flag_tables: list[pd.DataFrame]) -> pd.DataFrame:
    """All flags in one list, in rule order: cleaning (4.3 to 4.10), timing (5.3) and supply review (5.5)."""
    flags = pd.concat(flag_tables, ignore_index=True).reindex(columns=FLAG_COLUMNS)
    # sort 4.3 before 4.10, as numbers rather than text
    rule_parts = flags["rule"].str.split(".", expand=True).astype(int)
    order = rule_parts.sort_values([0, 1], kind="stable").index
    flags = flags.loc[order].reset_index(drop=True)
    print(f"  flag list: {len(flags)} rows, by rule {flags['rule'].value_counts(sort=False).to_dict()}")
    return flags


def flags_by_pair(flags: pd.DataFrame) -> pd.DataFrame:
    """The flag issues on each part and location as one text, so the main table points into the flag list."""
    issues = flags[["part_number", "location", "rule", "issue"]].drop_duplicates()
    issues = issues.assign(issue=issues["rule"] + " " + issues["issue"])
    return issues.groupby(["part_number", "location"], as_index=False)["issue"].agg("; ".join).rename(columns={"issue": "flags"})


def select_columns(table: pd.DataFrame, columns: list[str], left_out: list[str]) -> pd.DataFrame:
    """Put columns in reading order. Stop if a column would be dropped without being listed as left out."""
    missing = set(table.columns) - set(columns) - set(left_out)
    if missing:
        raise ValueError(f"columns not placed in the output: {sorted(missing)}")
    return table[columns]


def build_pair_table(master: pd.DataFrame, timing_flags: pd.DataFrame, flags: pd.DataFrame) -> pd.DataFrame:
    """The main output: one row per part and location, the key columns first and the 24 demand months last."""
    table = add_pallet_equivalents(master)
    table["on_watch_list"] = (table["status"] == "Healthy") & (table["wos"] > WATCH_WEEKS)
    timing_pairs = set(zip(timing_flags["part_number"], timing_flags["location"]))
    table["timing_flag"] = [pair in timing_pairs for pair in zip(table["part_number"], table["location"])]
    table = table.merge(flags_by_pair(flags), on=["part_number", "location"], how="left", validate="one_to_one")
    if len(table) != len(master):
        raise ValueError("main table: the flag join changed the number of rows")
    print(f"  main table: {len(table)} pairs, {table['flags'].notna().sum()} with at least one flag")
    return select_columns(table, PAIR_COLUMNS, LEFT_OUT)


def round_for_reading(table: pd.DataFrame) -> pd.DataFrame:
    """Two decimals are plenty for WOS, pallets and spread-out demand; dates and text are left alone."""
    table = table.copy()
    float_columns = table.select_dtypes("float").columns
    table[float_columns] = table[float_columns].round(2)
    return table


def quiet_text_labels(sheet, table: pd.DataFrame) -> None:
    """Tell Excel the label columns are text on purpose, so it marks no cell as a 'number stored as text'."""
    ranges = []
    for column in TEXT_LABELS:
        if column in table.columns:
            letter = xl_col_to_name(table.columns.get_loc(column))
            ranges.append(f"{letter}2:{letter}{len(table) + 1}")
    if ranges:
        sheet.ignore_errors({"number_stored_as_text": " ".join(ranges)})


def write_outputs(tables: dict[str, pd.DataFrame]) -> None:
    """Each table as a CSV in output/, and all but the CSV-only ones as sheets of one Excel workbook."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    tables = {name: round_for_reading(table) for name, table in tables.items()}
    for name, table in tables.items():
        path = OUTPUT_DIR / f"{name}.csv"
        table.to_csv(path, index=False)
        print(f"  wrote {path.relative_to(ROOT)} ({len(table)} rows)")
    sheets = {name: table for name, table in tables.items() if name not in CSV_ONLY}
    workbook = OUTPUT_DIR / WORKBOOK
    with pd.ExcelWriter(workbook, engine="xlsxwriter", date_format="YYYY-MM-DD",
                        datetime_format="YYYY-MM-DD") as writer:
        for name, table in sheets.items():
            if name == "inventory_health":
                table = table.drop(columns=DETAIL_COLUMNS)  # the planner's view, the CSV keeps every column
            table.to_excel(writer, sheet_name=name, index=False, freeze_panes=(1, 2))
            quiet_text_labels(writer.sheets[name], table)
    print(f"  wrote {workbook.relative_to(ROOT)} ({len(sheets)} sheets)")
