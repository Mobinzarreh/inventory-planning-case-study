"""The dashboard. Run from the repo root: .venv\\Scripts\\python.exe -m streamlit run app.py

It only reads the tables in output/, so run run_all.py first. Every number on every page comes from there.
"""
import pandas as pd
import streamlit as st

from dashboard.data import load_tables, missing_tables
from src.config import FORECAST_MONTHS, LOCATIONS, SNAPSHOT_DATE
from src.supply_review import END_DATE


def sidebar_filters(pairs) -> None:
    """Site, planner and part type filters, shared by every page that lists parts."""
    st.sidebar.header("Filters")
    st.sidebar.multiselect("Site", LOCATIONS, key="sites", placeholder="All sites")
    st.sidebar.multiselect("Planner", sorted(pairs["planner"].dropna().unique()), key="planners",
                           placeholder="All planners")
    st.sidebar.multiselect("Part type", sorted(pairs["part_type"].dropna().unique()), key="part_types",
                           placeholder="All part types")
    st.sidebar.caption("Leave a filter empty to keep everything. Parts missing from the part master "
                       "have no planner or part type, so they drop out once either filter is set.")
    first, last = pd.Period(FORECAST_MONTHS[0]), pd.Period(FORECAST_MONTHS[-1])
    st.sidebar.divider()
    st.sidebar.caption(f"Stock on {SNAPSHOT_DATE:%b %d, %Y}. Forecast {first:%b %Y} to {last:%b %Y}. "
                       f"Supply review to {END_DATE:%b %d, %Y}.")


st.set_page_config(page_title="Inventory Health Check", page_icon=":material/inventory_2:", layout="wide")

missing = missing_tables()
if missing:
    st.error(f"These output tables are missing: {', '.join(missing)}. Run run_all.py first.")
    st.stop()

# Results first for the planning manager, then how we got there
pages = {
    "Results": [
        st.Page("dashboard/overview.py", title="Overview", icon=":material/dashboard:", default=True),
        st.Page("dashboard/actions.py", title="Actions", icon=":material/checklist:"),
        st.Page("dashboard/worked_example.py", title="Worked example: SKU98", icon=":material/search:"),
    ],
    "How we got here": [
        st.Page("dashboard/data_quality.py", title="Data quality", icon=":material/rule:"),
        st.Page("dashboard/how_it_works.py", title="How it works", icon=":material/account_tree:"),
    ],
}
navigation = st.navigation(pages)
sidebar_filters(load_tables()["inventory_health"])
navigation.run()
