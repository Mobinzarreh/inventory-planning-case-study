"""Read the tables in output/ for the dashboard. The dashboard shows these numbers, it never recalculates them."""
import pandas as pd
import streamlit as st

from src.config import OUTPUT_DIR, RAW_FILES

OUTPUT_TABLES = ["inventory_health", "dc_summary", "supply_review_lines", "gdc_shortfalls", "stock_moves", "flags",
                 "standardizing_log", "daily_stock"]
DUPLICATE_ISSUE = "exact duplicate row dropped"
FILLED_ISSUES = ["blank forecast month filled", "negative forecast month filled", "qpc estimated"]
# the cleaned input files in output/clean/, under the raw file names
CLEAN_FILES = {name: "clean/" + file.removesuffix(".csv") for name, file in RAW_FILES.items()}
NOT_IN_MASTER = "not in part master"


def missing_tables() -> list[str]:
    """Output tables that don't exist yet, so the app can ask for a run of run_all.py instead of crashing."""
    names = OUTPUT_TABLES + list(CLEAN_FILES.values())
    return [name for name in names if not (OUTPUT_DIR / f"{name}.csv").exists()]


@st.cache_data
def read_table(name: str, modified: float) -> pd.DataFrame:
    """One output table. The modified time is only part of the cache key, so a fresh run_all.py shows up."""
    if name == "flags":
        return pd.read_csv(OUTPUT_DIR / "flags.csv", dtype=str)  # keeps rule "4.10" from turning into 4.1
    return pd.read_csv(OUTPUT_DIR / f"{name}.csv")


def load_tables() -> dict[str, pd.DataFrame]:
    """Every output table by name, read once and kept in memory until its file changes."""
    tables = {}
    for name in OUTPUT_TABLES:
        path = OUTPUT_DIR / f"{name}.csv"
        tables[name] = read_table(name, path.stat().st_mtime)
    return tables


def load_clean(name: str) -> pd.DataFrame:
    """One cleaned input table (parts, inventory, supply or demand) from output/clean/."""
    path = OUTPUT_DIR / f"{CLEAN_FILES[name]}.csv"
    return read_table(CLEAN_FILES[name], path.stat().st_mtime)


def load_clean_demand() -> pd.DataFrame:
    """The cleaned demand rows: Direct per site, and at the GDC the restock it ships to each PDC."""
    return load_clean("demand")


def filter_rows(table: pd.DataFrame, pairs: pd.DataFrame, site_columns: list[str]) -> pd.DataFrame:
    """Keep the rows that match the sidebar filters. Planner and part type come from each part in the pairs table.

    A row matches the site filter if any of its site columns does, so a move counts at both ends.
    An empty filter keeps everything.
    """
    sites = st.session_state.get("sites", [])
    if sites:
        table = table[table[site_columns].isin(sites).any(axis=1)]
    parts = pairs.drop_duplicates("part_number")
    for column, key in [("planner", "planners"), ("part_type", "part_types")]:
        chosen = st.session_state.get(key, [])
        if chosen:
            keep = parts.loc[parts[column].isin(chosen), "part_number"]
            table = table[table["part_number"].isin(keep)]
    return table


def filter_pairs(pairs: pd.DataFrame) -> pd.DataFrame:
    """The part and location pairs that match the sidebar filters."""
    return filter_rows(pairs, pairs, ["location"])


def raw_to_clean(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per raw file, from rows read to rows kept, plus a total row. Rows read = kept + exact copies dropped."""
    flags, log = tables["flags"], tables["standardizing_log"]
    rows = []
    for name, file in RAW_FILES.items():
        in_file = flags[flags["file"] == name]
        dropped = int((in_file["issue"] == DUPLICATE_ISSUE).sum())
        kept = len(load_clean(name))
        rows.append({"File": file, "Rows read": kept + dropped, "Exact copies dropped": dropped, "Rows kept": kept,
                     "Values standardized": int(log.loc[log["file"] == file, "values_changed"].sum()),
                     "Values filled or estimated": int(in_file["issue"].isin(FILLED_ISSUES).sum()),
                     "Flags": len(in_file)})
    funnel = pd.DataFrame(rows).set_index("File")
    funnel.loc["All files"] = funnel.sum()
    return funnel


def number_columns(table: pd.DataFrame) -> dict:
    """Column settings for st.dataframe so numbers show a thousands separator (1,039 rather than 1039)."""
    numeric = table.select_dtypes("number").columns
    return {column: st.column_config.NumberColumn(format="localized") for column in numeric}


def number_table(table: pd.DataFrame, **options) -> None:
    """st.dataframe with a thousands separator on every number column."""
    st.dataframe(table, column_config=number_columns(table), **options)


def part_names() -> pd.Series:
    """Each part's name (its description in the part master), by part number."""
    return load_clean("parts").set_index("part_number")["part_description"]


def part_label(part: str) -> str:
    """Name first, then the code: 'Compressor (SKU98)'. The code stays because names repeat (3 parts are Compressor)."""
    names = part_names()
    if part not in names.index:
        return f"{part} ({NOT_IN_MASTER})"
    return f"{names[part]} ({part})"


def add_part_name(table: pd.DataFrame) -> pd.DataFrame:
    """Add a part_name column from the part master. Parts missing from it say so rather than show a blank."""
    names = table["part_number"].map(part_names())
    return table.assign(part_name=names.fillna(NOT_IN_MASTER))


def add_part_details(table: pd.DataFrame, pairs: pd.DataFrame) -> pd.DataFrame:
    """Add each part's name and planner. One row per part on the right, so no row can be doubled."""
    planners = pairs.drop_duplicates("part_number")[["part_number", "planner"]]
    return add_part_name(table).merge(planners, on="part_number", how="left", validate="many_to_one")
