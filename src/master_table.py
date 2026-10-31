"""Build the master table: one row per part and location, from the clean tables.

Each source is first squashed to one row per pair (like GROUP BY), then joined on part and location,
so no join can duplicate rows. Row counts and totals are checked after every join.
The 24 month columns hold each site's demand as defined in ASSUMPTIONS.md 5.1.
"""
import math

import pandas as pd

from src.clean import FLAG_COLUMNS, clean_all
from src.config import FORECAST_MONTHS
from src.load_raw import read_all_raw
from src.standardize import standardize_all

PAIR = ["part_number", "location"]
PART_COLUMNS = ["part_number", "part_description", "part_type", "planner", "qpc", "qpc_is_estimate"]
MASTER_COLUMNS = [
    "part_number", "location", "pair_group", "in_part_master",
    "part_description", "part_type", "planner", "qpc", "qpc_is_estimate",
    "has_inventory_row", "on_hand_qty",
    "direct_24m", "restock_out_24m", "demand_24m", "direct_elsewhere_24m", "restock_in_24m", "direct_first_month",
    "supply_lines", "supplier_qty", "transfer_qty", "supply_qty", "first_eta",
] + FORECAST_MONTHS


def demand_by_pair(demand: pd.DataFrame) -> pd.DataFrame:
    """5.1: a PDC's demand is its Direct row, the GDC's is its Direct row plus every restock row."""
    demand = demand.copy()
    row_total = demand[FORECAST_MONTHS].sum(axis=1)
    is_direct = demand["demand_type"] == "Direct"
    demand["direct_24m"] = row_total.where(is_direct, 0)
    demand["restock_out_24m"] = row_total.where(~is_direct, 0)
    # customers only, no restock: the days before November use this rate (5.2)
    demand["direct_first_month"] = demand[FORECAST_MONTHS[0]].where(is_direct, 0)
    # restock rows already sit at location GDC, so grouping by part and location adds them to the GDC's demand
    value_columns = ["direct_24m", "restock_out_24m", "direct_first_month"] + FORECAST_MONTHS
    by_pair = demand.groupby(PAIR, as_index=False)[value_columns].sum()
    by_pair["demand_24m"] = by_pair["direct_24m"] + by_pair["restock_out_24m"]
    return by_pair


def restock_in_by_pair(demand: pd.DataFrame) -> pd.DataFrame:
    """Planned GDC shipments into each PDC: the restock rows, keyed by the PDC in ship_to."""
    restock = demand[demand["demand_type"] == "Replenishment"].copy()
    restock["restock_in_24m"] = restock[FORECAST_MONTHS].sum(axis=1)
    by_pair = restock.groupby(["part_number", "ship_to"], as_index=False)["restock_in_24m"].sum()
    return by_pair.rename(columns={"ship_to": "location"})


def supply_by_pair(supply: pd.DataFrame) -> pd.DataFrame:
    """Incoming supply per part and destination, split by source (a GDC Transfer counts at the PDC, 5.1)."""
    unexpected = set(supply["source"]) - {"Supplier", "GDC Transfer"}
    if unexpected:
        raise ValueError(f"Unexpected supply source: {unexpected}")
    supply = supply.copy()
    supply["supplier_qty"] = supply["qty"].where(supply["source"] == "Supplier", 0)
    supply["transfer_qty"] = supply["qty"].where(supply["source"] == "GDC Transfer", 0)
    by_pair = supply.groupby(["part_number", "destination"], as_index=False).agg(
        supply_lines=("qty", "size"),
        supply_qty=("qty", "sum"),
        supplier_qty=("supplier_qty", "sum"),
        transfer_qty=("transfer_qty", "sum"),
        first_eta=("eta_date", "min"),
    )
    return by_pair.rename(columns={"destination": "location"})


def source_totals(clean: dict[str, pd.DataFrame]) -> dict[str, float]:
    """Totals from the clean files that the master table must still add up to after every join."""
    demand = clean["demand"]
    restock = demand[demand["demand_type"] == "Replenishment"]
    return {
        "on_hand_qty": clean["inventory"]["on_hand_qty"].sum(),
        "demand_24m": demand[FORECAST_MONTHS].sum().sum(),
        "supply_qty": clean["supply"]["qty"].sum(),
        "restock_in_24m": restock[FORECAST_MONTHS].sum().sum(),
    }


def check_totals(pairs: pd.DataFrame, expected: dict[str, float], step: str) -> None:
    """Stop if a pair repeats or a total moved, because that means a join duplicated or dropped rows."""
    if pairs.duplicated(PAIR).any():
        raise ValueError(f"{step}: a part and location appears more than once")
    for column, source_total in expected.items():
        if column in pairs and not math.isclose(pairs[column].sum(), source_total):
            raise ValueError(f"{step}: {column} adds up to {pairs[column].sum()}, the source has {source_total}")
    print(f"  {step}: {len(pairs)} rows, totals match")


def join_sources(clean: dict[str, pd.DataFrame], expected: dict[str, float]) -> pd.DataFrame:
    """FULL OUTER JOIN of stock, demand and supply on part and location, then LEFT JOIN the restock coming in."""
    stock = clean["inventory"][["part_number", "location", "on_hand_qty"]]
    pairs = stock.merge(demand_by_pair(clean["demand"]), on=PAIR, how="outer")
    check_totals(pairs, expected, "inventory + demand (full outer join)")
    pairs = pairs.merge(supply_by_pair(clean["supply"]), on=PAIR, how="outer")
    check_totals(pairs, expected, "+ supply (full outer join)")
    # every restock row matches a PDC Direct row (5.1), so a left join loses nothing; the total check proves it
    pairs = pairs.merge(restock_in_by_pair(clean["demand"]), on=PAIR, how="left")
    check_totals(pairs, expected, "+ restock in (left join)")
    return pairs


def fill_missing_sources(pairs: pd.DataFrame) -> pd.DataFrame:
    """A pair missing from a file gets 0 from it: no inventory row is 0 on hand (4.1), no demand row is no demand (4.2)."""
    pairs = pairs.copy()
    # the inventory file has no blank on-hand cells, so a blank here means there was no row
    pairs["has_inventory_row"] = pairs["on_hand_qty"].notna()
    zero_columns = ["on_hand_qty", "direct_24m", "restock_out_24m", "demand_24m", "restock_in_24m", "direct_first_month",
                    "supply_lines", "supply_qty", "supplier_qty", "transfer_qty"] + FORECAST_MONTHS
    pairs[zero_columns] = pairs[zero_columns].fillna(0)
    pairs["supply_lines"] = pairs["supply_lines"].astype(int)
    return pairs


def add_part_info(pairs: pd.DataFrame, parts: pd.DataFrame, expected: dict[str, float]) -> pd.DataFrame:
    """LEFT JOIN the part master, so parts missing from it (4.10) stay in with blank details."""
    pairs = pairs.merge(parts[PART_COLUMNS], on="part_number", how="left", indicator="master_match")
    pairs["in_part_master"] = pairs["master_match"] == "both"
    pairs["qpc_is_estimate"] = pairs["qpc_is_estimate"].eq(True)
    pairs = pairs.drop(columns="master_match")
    check_totals(pairs, expected, "+ part master (left join)")
    return pairs


def label_pairs(pairs: pd.DataFrame) -> pd.DataFrame:
    """Group each pair by what the files say about it (4.1 to 4.3), and add demand for the part elsewhere."""
    pairs = pairs.copy()
    has_stock_row = pairs["has_inventory_row"]
    has_demand = pairs["demand_24m"] > 0  # 4.2: an all-zero row counts as no demand
    has_supply = pairs["supply_qty"] > 0
    group = pd.Series("set aside", index=pairs.index)
    group.loc[has_stock_row & has_demand] = "inventory and demand"
    group.loc[~has_stock_row & has_demand] = "demand, no inventory row"
    group.loc[has_stock_row & ~has_demand] = "inventory, no demand"
    group.loc[~has_stock_row & ~has_demand & has_supply] = "supply only"
    pairs["pair_group"] = group
    # customer demand for the same part at the other sites, to tell stock wanted elsewhere from stock nobody wants
    part_direct = pairs.groupby("part_number")["direct_24m"].transform("sum")
    pairs["direct_elsewhere_24m"] = part_direct - pairs["direct_24m"]
    return pairs


def set_aside_empty_pairs(pairs: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """4.3: pairs with no inventory row, no real demand and no supply leave the table and go to the flag list."""
    is_empty = pairs["pair_group"] == "set aside"
    empty = pairs[is_empty]
    flags = pd.DataFrame({
        "rule": "4.3",
        "issue": "pair set aside",
        "file": "demand",
        "part_number": empty["part_number"],
        "location": empty["location"],
        "record": "demand row, zero for all 24 months",
        "note": "no inventory, no demand and no incoming supply. Possibly a forecast row left over for a part this site no longer carries",
    }).reindex(columns=FLAG_COLUMNS)
    print(f"  set aside (4.3): {is_empty.sum()} pairs moved to the flag list")
    return pairs[~is_empty].reset_index(drop=True), flags


def sort_pairs(pairs: pd.DataFrame) -> pd.DataFrame:
    """Sort by location, then by part number as a number (SKU2 before SKU10)."""
    pairs = pairs.copy()
    pairs["part_order"] = pairs["part_number"].str.removeprefix("SKU").astype(int)
    pairs = pairs.sort_values(["location", "part_order"]).drop(columns="part_order")
    return pairs.reset_index(drop=True)


def build_master(clean: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One row per part and location with stock, demand, supply and part info. Returns the table and the 4.3 flags."""
    print("Building the master table")
    expected = source_totals(clean)
    pairs = join_sources(clean, expected)
    pairs = fill_missing_sources(pairs)
    pairs = add_part_info(pairs, clean["parts"], expected)
    pairs = label_pairs(pairs)
    pairs, set_aside_flags = set_aside_empty_pairs(pairs)
    check_totals(pairs, expected, "after setting aside empty pairs")
    master = sort_pairs(pairs)[MASTER_COLUMNS]
    return master, set_aside_flags


if __name__ == "__main__":
    clean_tables, flag_list = clean_all(standardize_all(read_all_raw()))
    master, set_aside_flags = build_master(clean_tables)
    print("\npairs by group and location:")
    print(pd.crosstab(master["pair_group"], master["location"], margins=True))
    check_columns = ["part_number", "location", "pair_group", "on_hand_qty", "direct_24m", "restock_out_24m",
                     "demand_24m", "restock_in_24m", "transfer_qty", "supplier_qty"]
    print("\nhand check, SKU82 at every site:")
    print(master.loc[master["part_number"] == "SKU82", check_columns].to_string(index=False))
    print("\nparts not in the part master:")
    print(master.loc[~master["in_part_master"], check_columns + ["in_part_master"]].to_string(index=False))
    print("\nset aside:")
    print(set_aside_flags[["part_number", "location", "record"]].to_string(index=False))
    print("\ncolumns:", len(master.columns), "| rows:", len(master))
