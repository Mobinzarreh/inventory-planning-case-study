"""Run the whole analysis, from the raw CSVs in data/ to the files in output/.

Run from the repo root: .venv\\Scripts\\python.exe run_all.py
Each step prints its own checks (row counts and totals), so a row lost or doubled in a join shows up here.
"""
from src.charts import plot_status_mix
from src.clean import clean_all, save_clean_tables
from src.config import OUTPUT_DIR
from src.load_raw import read_all_raw
from src.master import build_master
from src.output import LINE_COLUMNS, build_flag_list, build_pair_table, select_columns, write_outputs
from src.rollup import build_dc_summary
from src.standardize import standardize_all, standardizing_log
from src.status import add_status
from src.supply_review import review_supply
from src.wos import add_wos


def main() -> None:
    """Read, standardize, clean, build the master table, add WOS and status, summarize, review supply, write."""
    raw_tables = read_all_raw()
    standardized = standardize_all(raw_tables)
    fixes = standardizing_log(raw_tables, standardized)
    clean_tables, cleaning_flags = clean_all(standardized)
    save_clean_tables(clean_tables)
    master, set_aside_flags = build_master(clean_tables)
    master, timing_flags = add_status(add_wos(master))
    dc_summary = build_dc_summary(master, timing_flags)
    master, review_tables, review_flags = review_supply(master, clean_tables)
    print("Writing the output files")
    flags = build_flag_list([cleaning_flags, set_aside_flags, timing_flags, review_flags])
    outputs = {
        "inventory_health": build_pair_table(master, timing_flags, flags),
        "dc_summary": dc_summary.reset_index(),
        "supply_review_lines": select_columns(review_tables["supply_lines"], LINE_COLUMNS, []),
        "gdc_shortfalls": review_tables["gdc_shortfalls"],
        "stock_moves": review_tables["moves"],
        "flags": flags,
        "standardizing_log": fixes,
        "daily_stock": review_tables["daily_stock"],
    }
    write_outputs(outputs)
    plot_status_mix(dc_summary, OUTPUT_DIR / "status_mix_by_dc.png")


if __name__ == "__main__":
    main()
