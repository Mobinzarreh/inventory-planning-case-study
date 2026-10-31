"""The output tables as a read-only SQLite database in memory, for the agent's SQL tool.

It is filled from the same loader as the dashboard (dashboard/data.py), so the two can't disagree.
Run from the repo root to check it: .venv\\Scripts\\python.exe -m planning_agent.database
"""
import pandas as pd
import streamlit.logger
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from dashboard.data import load_clean, load_clean_demand, load_tables
from src.config import FORECAST_MONTHS


def monthly_demand(demand: pd.DataFrame) -> pd.DataFrame:
    """The cleaned forecast as one row per part, site, demand type and month, so a 24-month chart is one query."""
    keys = ["part_number", "location", "demand_type", "ship_to"]
    long = demand.melt(id_vars=keys, value_vars=FORECAST_MONTHS, var_name="month", value_name="pieces")
    return long.sort_values(keys + ["month"]).reset_index(drop=True)


def database_tables() -> dict[str, pd.DataFrame]:
    """The tables the agent can query, by name: the dashboard's output tables, the part master and the demand.

    Reshaped only, no number is recalculated.
    """
    tables = load_tables()
    # its 24 month columns ("2026-11", ...) are awkward in SQL, and demand_monthly has the same forecast
    tables["inventory_health"] = tables["inventory_health"].drop(columns=FORECAST_MONTHS)
    tables["parts"] = load_clean("parts")
    tables["demand_monthly"] = monthly_demand(load_clean_demand())
    return tables


def build_database() -> Engine:
    """Copy the tables into SQLite in memory, then lock it so a query can only read."""
    # one shared connection: each new connection to "sqlite://" would open its own empty database, and
    # Streamlit runs every browser session in its own thread
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for name, table in database_tables().items():
        table.to_sql(name, engine, index=False)
    with engine.connect() as connection:
        connection.execute(text("PRAGMA query_only = ON"))
    return engine


def check_database(engine: Engine) -> None:
    """Row counts against the tables they came from, a write that must fail, and two sums against dc_summary."""
    for name, table in database_tables().items():
        rows = pd.read_sql(f"SELECT COUNT(*) AS n FROM {name}", engine)["n"].iloc[0]
        print(f"  {name}: {rows:,} rows ({'same' if rows == len(table) else 'DIFFERENT'} as the source)")
    try:
        with engine.connect() as connection:
            connection.execute(text("DELETE FROM flags"))
        print("  write: NOT blocked")
    except OperationalError as error:
        print(f"  write: blocked ({error.orig})")
    stockout = pd.read_sql("SELECT location, COUNT(*) AS pairs FROM inventory_health WHERE status = 'Stockout' "
                           "GROUP BY location", engine)
    pallets = pd.read_sql("SELECT location, ROUND(SUM(pallets_on_hand), 1) AS pallets FROM inventory_health "
                          "WHERE status = 'No demand' GROUP BY location", engine)
    summary = pd.read_sql("SELECT location, stockout, ROUND(dead_stock_pallets + wanted_elsewhere_pallets, 1) "
                          "AS no_demand_pallets FROM dc_summary WHERE location <> 'All sites'", engine)
    check = summary.merge(stockout, on="location").merge(pallets, on="location")
    print("\n  pairs and pallets from inventory_health next to dc_summary:")
    print(check.to_string(index=False))


if __name__ == "__main__":
    streamlit.logger.set_log_level("error")  # the dashboard's cache warns when it runs outside the app, harmlessly
    check_database(build_database())
