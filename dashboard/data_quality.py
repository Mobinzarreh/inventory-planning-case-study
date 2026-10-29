"""Data quality: what we found in the raw files and how each problem was handled (details in ASSUMPTIONS.md)."""
import pandas as pd
import streamlit as st

from dashboard.charts import NO_TOOLBAR, flags_by_issue_chart
from dashboard.data import add_part_name, filter_rows, load_tables, number_columns, raw_to_clean
from src.config import RAW_FILES

LOG_COLUMNS = {"file": "File", "column": "Column", "what_was_fixed": "What was fixed", "values_changed": "Values",
               "raw_spellings": "Raw spellings", "examples": "Examples"}
# what it might mean sits next to the part, so it is not off screen
FLAG_COLUMNS = {"rule": "Rule", "issue": "Issue", "part_number": "Part", "part_name": "Part name",
                "location": "Site", "note": "What it might mean", "original_value": "Was", "value_used": "Now", "record": "Record",
                "file": "File"}


def wrapped_table(table: pd.DataFrame) -> None:
    """A static table that wraps long text, for short tables where every word should be readable."""
    st.table(table.style.hide(axis="index").format(thousands=","))


def quality_tiles(totals: pd.Series, flag_count: int) -> None:
    """Four numbers: rows kept, values standardized, values filled, and findings for a person to check."""
    tiles = st.columns(4)
    tiles[0].metric("Rows kept", f"{totals['Rows kept']:,}", delta=f"-{totals['Exact copies dropped']} exact copies",
                    delta_color="off", border=True, height="stretch",
                    help=f"{totals['Rows read']:,} rows read from the four raw files. Only exact copies are dropped, "
                         "and each one is on the flag list.")
    tiles[1].metric("Values standardized", f"{totals['Values standardized']:,}", border=True, height="stretch",
                    help="Spellings, numbers and dates written one standard way. What a value means never changes.")
    tiles[2].metric("Values filled or estimated", totals["Values filled or estimated"], border=True, height="stretch",
                    help="Blank and negative forecast months filled from their neighbours, and pallet sizes (qpc) "
                         "estimated. Each one is on the flag list with its old and new value.")
    tiles[3].metric("Flags to check", flag_count, border=True, height="stretch",
                    help="Every finding, with what was found and what it might mean.")


def unknown_parts(flags: pd.DataFrame) -> pd.DataFrame:
    """The parts missing from the part master, where they sit, and the best guess recorded in the flag note."""
    unknown = flags[flags["issue"] == "part not in part master"]
    guess = unknown["note"].str.split("test record. ", n=1).str[1]
    return pd.DataFrame({"Part": unknown["part_number"], "Site": unknown["location"], "Found in": unknown["record"],
                         "What it might be": guess})


def flag_list(flags: pd.DataFrame, pairs: pd.DataFrame) -> None:
    """The full flag list, narrowed by issue, a search word and the sidebar filters, with a download."""
    left, right = st.columns([1, 1])
    issues = left.multiselect("Issue", flags["issue"].value_counts().index.tolist(), placeholder="All issues")
    search = right.text_input("Search", placeholder="A part, a part name, a PO or a word in the note")
    shown = add_part_name(filter_rows(flags, pairs, ["location"]))
    if issues:
        shown = shown[shown["issue"].isin(issues)]
    if search:
        row_text = shown.fillna("").agg(" ".join, axis=1)
        shown = shown[row_text.str.contains(search, case=False, regex=False)]
    readable = shown.assign(file=shown["file"].map(RAW_FILES))[list(FLAG_COLUMNS)].rename(columns=FLAG_COLUMNS)
    st.caption(f"{len(shown)} of {len(flags)} flags shown. Click a cell to read a long note in full.")
    st.dataframe(readable.fillna(""), hide_index=True,
                 column_config={"What it might mean": st.column_config.TextColumn(width="large")})
    st.download_button("Download as CSV", readable.to_csv(index=False), file_name="flags.csv", mime="text/csv")


st.title("Data quality")
st.write("What we found in the raw files, how each problem was handled, and what it might mean. Every result in "
         "this dashboard is built on the cleaned data, and nothing was dropped or changed without a record.")

tables = load_tables()
flags, log, pairs = tables["flags"], tables["standardizing_log"], tables["inventory_health"]
funnel = raw_to_clean(tables)
quality_tiles(funnel.loc["All files"], len(flags))

st.subheader("From raw to clean, file by file")
st.caption("Standardizing writes every value one way and never adds or removes a row. Cleaning then drops only "
           "exact copies, fills blank or negative forecast months, and keeps everything else with a flag.")
st.dataframe(funnel, column_config=number_columns(funnel))
st.markdown("**What was messy, and how it was standardized**")
wrapped_table(log[list(LOG_COLUMNS)].rename(columns=LOG_COLUMNS))

st.subheader("What we found")
st.caption("Each flag is one finding, from cleaning or from the supply review, with what it might mean.")
st.plotly_chart(flags_by_issue_chart(flags), config=NO_TOOLBAR)
st.markdown("**Parts missing from the part master, and what they might be**")
wrapped_table(unknown_parts(flags))

st.subheader("The flag list")
st.caption("The sidebar filters apply here. Flags on a whole file row (like a duplicate in the part master) have "
           "no site, so they drop out once a site is chosen.")
flag_list(flags, pairs)
