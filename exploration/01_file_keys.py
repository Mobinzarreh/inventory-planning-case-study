"""Do part numbers and locations match across the four files once spellings are standardized?

Read-only check. Run from the repo root: .venv\\Scripts\\python.exe exploration\\01_file_keys.py
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `src` imports work when run as a plain script

from src.load_raw import read_all_raw
from src.standardize import standardize_all

# Standardized but not cleaned, so exact duplicate rows are still in these counts
tables = standardize_all(read_all_raw())
parts = tables["parts"]
inventory = tables["inventory"]
supply = tables["supply"]
demand = tables["demand"]
print("rows:", {name: len(table) for name, table in tables.items()})

print("\n1. Part numbers vs the part master")
master = set(parts["part_number"])
for name, table in [("inventory", inventory), ("supply", supply), ("demand", demand)]:
    file_parts = set(table["part_number"])
    not_in_master = sorted(file_parts - master)
    print(f"{name}: {len(file_parts)} parts, {len(file_parts & master)} in parts.csv, not in parts.csv: {not_in_master}")
print("master parts with no inventory row:", sorted(master - set(inventory["part_number"])))
print("master parts with no demand row:", sorted(master - set(demand["part_number"])))
print("master parts with no supply line:", len(master - set(supply["part_number"])))

print("\n2. Location values")
print("inventory location:", inventory["location"].value_counts().to_dict())
print("demand location:", demand["location"].value_counts().to_dict())
print("demand ship_to:", demand["ship_to"].value_counts().to_dict())
print("supply destination:", supply["destination"].value_counts().to_dict())

print("\n3. Demand type by location")
print(pd.crosstab(demand["demand_type"], demand["location"]))
replen = demand[demand["demand_type"] == "Replenishment"]
print("Replenishment rows by ship_to:", replen["ship_to"].value_counts().to_dict())

print("\n4. Supply source by destination")
print(pd.crosstab(supply["source"], supply["destination"]))
