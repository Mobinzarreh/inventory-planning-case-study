# Service Parts Inventory Health Check

My take-home for the service parts planning case: weeks of supply and status for every part at the GDC and the three PDCs, a roll-up per DC, and a review of incoming supply to Jan 31. The raw data in `data/` is exactly as received.

- **Results:** `output/inventory_health_check.xlsx`
- **Summary for the planning manager:** `output/summary_slides.pptx` (5 slides)
- **Definitions, assumptions and what I'd do with more time:** `ASSUMPTIONS.md`
- **Planning agent demo** (the bonus): [`docs/agent_demo.md`](docs/agent_demo.md), Tesla's four example questions and three more, with screenshots
- **Live app** (the planning agent and a dashboard, open to try your own questions): https://inventory-planning-case-study-a3gaqkykrzjhyq5mqi875s.streamlit.app/ (it takes a minute to wake up if nobody has opened it for a while)

## Run it

Python 3.11. In PowerShell (on macOS or Linux, use `.venv/bin/python`):

```powershell
git clone https://github.com/Mobinzarreh/inventory-planning-case-study.git
cd inventory-planning-case-study
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_pipeline.py
```

`run_pipeline.py` goes from the raw CSVs to every table and chart in `output/` in about 3 seconds. Each step prints its row counts, and the run stops if a join drops or doubles rows. To open the app locally, run `.\.venv\Scripts\python.exe -m streamlit run app.py`.

## Output

The workbook holds these seven tables as sheets. Each one is also saved as a CSV, and `inventory_health.csv` also keeps the 24 monthly forecast columns and 8 helper columns that the sheet leaves out.

| Table | One row is | Answers |
|---|---|---|
| `inventory_health` | a part at a location (340 rows) | Tasks 1 and 2: WOS and status today, and what happens by Jan 31 |
| `dc_summary` | a DC, plus all sites | Task 3: status mix, pallet equivalents, watch list |
| `supply_review_lines` | an open PO line (109 rows) | Task 4: the verdict on each line and what to do |
| `gdc_shortfalls` | a GDC part that runs out by Jan 31 (53 rows) | Task 4: when it runs out and whether a PO is coming |
| `stock_moves` | a candidate move between sites (48 rows) | Task 4: spare stock that could cover a shortage, to check before moving |
| `flags` | one data finding (139 rows) | What looked odd, what it might mean, and the rule in `ASSUMPTIONS.md` |
| `standardizing_log` | a file and column (15 rows) | Every spelling, number and date format fixed |

`output/` also has `daily_stock.csv` (the day-by-day projection to Jan 31), `status_mix_by_dc.png` (task 3's chart) and `clean/` (the four input files after cleaning). If you open `flags.csv` directly in Excel, rule 4.10 shows as 4.1, so use the workbook for that one.

## Planning agent (bonus)

The app opens on the planning agent. Ask about stock, supply and demand in plain English and get answers, charts and breakdowns. A LangChain agent picks one of four tools: read-only SQL on the output tables, the dashboard's charts, any other chart, and a what-if stock move. The numbers come from the data and the code, never from the model, and **How I got this** under each answer shows every step and its SQL. The design is in `ASSUMPTIONS.md` 5.6. The demo, with seven questions and their screenshots, is in [`docs/agent_demo.md`](docs/agent_demo.md).

![The agent answering how many pallets of no-demand stock sit at each location, with the SQL behind the answer](docs/agent_demo/04_no_demand_pallets.png)

To run it locally, add a `.env` file in the repo root (`.gitignore` keeps it out of git). Without the key, the dashboard still works.

```
ASU_CREATEAI_API_KEY=...
# optional: trace each run in LangSmith
LANGSMITH_API_KEY=...
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=inventory-planning-assistant
```

It also runs in the terminal, `.\.venv\Scripts\python.exe -m planning_agent.cli "Which GDC parts need an order?"`, and `-m planning_agent.evaluate` runs its test questions.

## Dashboard

The same app has a dashboard that only reads `output/`, so every number matches the workbook. Pages: The four tasks (each task answered in short, with where to find the detail), Overview, Actions, Worked example: SKU98, Data quality and How it works. Screenshots of every page are in [`docs/dashboard_demo.md`](docs/dashboard_demo.md).

## Code layout

```
run_pipeline.py    runs every step, from data/ to output/
src/               one module per step: config, load_raw, standardize, clean, master_table,
                   weeks_of_supply, status, dc_summary, supply_review, write_outputs, status_chart
exploration/       short read-only scripts with the evidence behind ASSUMPTIONS.md
app.py             the app: the planning agent first, then the dashboard
planning_agent/    agent, prompt, tools, database (a read-only SQLite copy of output/),
                   chat_page, cli, evaluate (the test set)
dashboard/         data.py reads output/, charts.py and part_charts.py draw, pages/ holds one file per page
docs/              the demo screenshots: agent_demo.md and dashboard_demo.md, each with its folder of images
```

Each `src` module also runs on its own and prints a few hand checks, for example `.\.venv\Scripts\python.exe -m src.supply_review`.
