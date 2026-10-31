"""The app: the planning agent, then the dashboard. Run from the repo root:
.venv\\Scripts\\python.exe -m streamlit run app.py

Both only read the tables in output/, so run run_pipeline.py first. Every number on every page comes from there.
"""
import pandas as pd
import streamlit as st

from dashboard.data import load_tables, missing_tables
from src.config import FORECAST_MONTHS, LOCATIONS, SNAPSHOT_DATE
from src.supply_review import END_DATE

REPO_URL = "https://github.com/Mobinzarreh/inventory-planning-case-study"
# The agent's entry is the first section of the sidebar. A red outline and an "AI agent" tag, in Tesla red on the
# charcoal sidebar, make it stand out on every page
AGENT_IN_SIDEBAR = """
<style>
[data-testid="stSidebarNavItems"] > div:first-child [data-testid="stSidebarNavLink"] {
    border: 1px solid #cc0000; background: rgba(204, 0, 0, 0.14); font-weight: 600;}
[data-testid="stSidebarNavItems"] > div:first-child [data-testid="stSidebarNavLink"]::after {
    content: "AI agent"; flex: none; white-space: nowrap; margin-left: auto; padding: 0.1rem 0.5rem;
    border-radius: 999px; background: #cc0000; color: #ffffff; font-size: 0.65rem; font-weight: 600;
    letter-spacing: 0.05em; text-transform: uppercase;}
</style>
"""


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
    st.error(f"These output tables are missing: {', '.join(missing)}. Run run_pipeline.py first.")
    st.stop()

# The agent opens first, in its own section. Then the dashboard: results for the planning manager, then how we
# got there
agent = st.Page("planning_agent/chat_page.py", title="Ask the agent", icon=":material/auto_awesome:", default=True)
four_tasks = st.Page("dashboard/pages/four_tasks.py", title="The four tasks", icon=":material/task_alt:")
pages = {
    "AI planning agent": [agent],
    "Dashboard: results": [
        four_tasks,
        st.Page("dashboard/pages/overview.py", title="Overview", icon=":material/dashboard:"),
        st.Page("dashboard/pages/actions.py", title="Actions", icon=":material/checklist:"),
        st.Page("dashboard/pages/worked_example.py", title="Worked example: SKU98", icon=":material/search:"),
    ],
    "Dashboard: how we got here": [
        st.Page("dashboard/pages/data_quality.py", title="Data quality", icon=":material/rule:"),
        st.Page("dashboard/pages/how_it_works.py", title="How it works", icon=":material/account_tree:"),
    ],
}
navigation = st.navigation(pages)
st.html(AGENT_IN_SIDEBAR)
# the filters belong to the dashboard. The agent has its own, and the four answers cover the whole network
if navigation.url_path not in [agent.url_path, four_tasks.url_path]:
    sidebar_filters(load_tables()["inventory_health"])
navigation.run()
st.sidebar.caption(f"Built by Mobin Zarreh · [Source on GitHub]({REPO_URL})")
