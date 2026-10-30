# Service Parts Inventory Health Check

My take-home for the service parts planning case. The raw data is in `data/`, exactly as received. Every definition, assumption and data decision is in `ASSUMPTIONS.md`, with the evidence behind it and where it sits in the code.

## Setup

Python 3.11. In PowerShell:

```powershell
git clone https://github.com/Mobinzarreh/inventory-planning-case-study.git
cd inventory-planning-case-study
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

On macOS or Linux, use `.venv/bin/python` instead of `.\.venv\Scripts\python.exe`.

## Run

```powershell
.\.venv\Scripts\python.exe run_all.py
```

This one command goes from the raw CSVs to every output file, in about 3 seconds. It only reads `data/` and writes to `output/`. Each step prints its row counts and totals. The run stops with an error if a join drops or doubles rows, or if a total stops matching the source files.

## Output

Everything lands in `output/`. Open `inventory_health_check.xlsx` in Excel: it holds the first seven tables below as sheets. The same tables are also saved as CSV files for code to read, along with `daily_stock.csv`, which is CSV only.

| File | One row is | Answers |
|---|---|---|
| `inventory_health.csv` | a part at a location (340 rows) | Tasks 1, 2 and 4: WOS, status, the projection to Jan 31, and any flags |
| `dc_summary.csv` | a DC, plus all sites (5 rows) | Task 3: status mix, pallet equivalents, watch list |
| `supply_review_lines.csv` | an open PO line (109 rows) | Task 4: the verdict on each line and what to do |
| `gdc_shortfalls.csv` | a GDC part that runs out by Jan 31 (53 rows) | Task 4: when it runs out and whether a PO is coming |
| `stock_moves.csv` | a suggested move between sites (48 rows) | Task 4: spare stock that could cover a shortage |
| `flags.csv` | one finding (139 rows) | What looked odd in the data, what it might mean, and the rule in `ASSUMPTIONS.md` |
| `standardizing_log.csv` | a file and column (15 rows) | Every spelling, number and date format fixed while standardizing, with counts and examples |
| `daily_stock.csv` | a part at a location on one day (34,680 rows) | The day-by-day projection to Jan 31 behind `run_out_date` and `stock_jan31`. CSV only, too long to be a useful sheet |
| `status_mix_by_dc.png` | a chart, one bar per DC | Task 3: the share of pairs in each status at each site |
| `clean/` | the four input files after cleaning | The same file names as in `data/` |

If you open `flags.csv` directly in Excel, rule 4.10 shows up as 4.1. The workbook keeps it as text.

Main columns in `inventory_health`:
- `wos`: weeks of supply, today's on-hand ÷ average weekly demand over Nov to Jan (ASSUMPTIONS.md 5.2). It is blank when the pair has no demand.
- `status`: Stockout, Short (under 4 weeks), Healthy (4 to 26), Excess (over 26) or No demand (5.3). `no_demand_reason` says why a pair has no demand.
- `on_watch_list` and `timing_flag`: Healthy pairs over 13 weeks, and pairs that run out sooner than their WOS suggests (5.3).
- `run_out_date`, `days_without_stock`, `stock_jan31`, `wos_jan31` and `status_jan31`: what happens once incoming supply and planned restock land (5.5).
- `restock_not_received`: planned restock for a PDC that the GDC cannot send by Jan 31 (5.5).
- `flags`: the issues from the flag list for this part and location.

## Planning agent

The app opens on the planning agent, the bonus task: ask about stock, supply and demand in plain English, and get answers, charts and breakdowns. A live copy runs at https://inventory-planning-case-study-a3gaqkykrzjhyq5mqi875s.streamlit.app/ (if nobody has opened it for a while, it takes a minute to wake up). The dashboard is in the same app, in its own sidebar sections.

![The planning agent's opening screen](docs/agent_welcome.png)

How it works:
- A LangChain agent (`create_agent`, a ReAct loop on LangGraph) picks a tool, reads the result, and repeats until it can answer. It has four tools:
  - `run_sql`: one read-only SQL query on an in-memory SQLite copy of the `output/` tables.
  - `show_chart`: the dashboard's own charts (status mix, stockouts today and on Jan 31, pallets tied up, when pairs run out, one part's demand and stock), each with the takeaway sentence the code writes.
  - `make_chart`: any other bar or line chart, from one query.
  - `what_if_move`: weeks of supply and status at both sites if stock moved today.
- The numbers come from the data and the code, never from the model. Each tool sends its full table or chart straight to the page, and the model sees only the first 20 rows, enough to write its answer. Its advice comes from the pipeline's verdict and action columns. Under every answer, **How I got this** shows each step and its SQL.
- It remembers the conversation, so a follow-up like "which of those have supply on the way?" works. **New conversation** starts over.
- The model is picked in the sidebar: Claude Sonnet 5.5 (default), Claude Opus 5.5 or GPT-5.5, through ASU's CreateAI gateway. Six models were tested, and these three got every answer right and made nothing up (`ASSUMPTIONS.md` 5.6).

To run it locally, put the key in a `.env` file in the repo root (`.gitignore` keeps it out of git):

```
ASU_CREATEAI_API_KEY=...
# optional: trace every run in LangSmith
LANGSMITH_API_KEY=...
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=inventory-planning-assistant
```

Then run `run_all.py` and start the app:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

It opens in the browser at http://localhost:8501. Without the key, the agent page says so and the dashboard pages still work. The agent also runs in the terminal. Several questions in one run are one conversation:

```powershell
.\.venv\Scripts\python.exe -m assistant.ask "Show me all Excess parts for Daniel Okafor at PDC2" "Which of those have supply on the way?"
```

**Test set.** `.\.venv\Scripts\python.exe -m assistant.check` asks 18 questions: Tesla's four examples, more charts, supply and actions, a what-if, a follow-up that needs memory, and five traps (a part that doesn't exist, a part missing from the part master, a name shared by three parts, a money question the data can't answer, an off-topic request). Each answer must use a fitting tool, name the right facts (worked out with pandas from `output/` at run time), and contain no number the model didn't see in a tool result. Add `--model="GPT-5.5"` to test another model. All three models pass 18 of 18.

**Demo.** Tesla's second and fourth examples, and a what-if:

![Status by part type at the GDC, drawn with the dashboard's chart](docs/agent_status_by_part_type.png)

![No-demand pallets per site, with the SQL behind the answer opened](docs/agent_no_demand_pallets.png)

![What if 926 pieces of SKU98 move from the GDC to PDC2](docs/agent_what_if.png)

## Dashboard

An interactive view of the same results, for reading and deciding rather than for computing. It's in the same app as the planning agent: start it as above, and the dashboard pages are below the agent in the sidebar. The dashboard only reads the tables in `output/` and recalculates nothing, so every number matches the CSVs and the workbook. The sidebar filters by site, planner and part type. Parts show by name with the code in brackets, like Compressor (SKU98). The code always stays, because 21 names are shared by two or three parts. There are five pages:

- **Overview:** headline numbers, the status mix by site, what changes by Jan 31, and the pallets tied up in excess and no-demand stock.
- **Actions:** GDC parts to order, PO lines by verdict, suggested moves and when pairs run out, each list with a download button.
- **Worked example: SKU98:** one part followed from the raw rows to the suggested move, then the same views for any part.
- **Data quality:** what was found in the raw files, how each problem was handled, and the full flag list.
- **How it works:** the end-to-end flow with the counts at each step, and the key decisions behind the numbers.

## Code layout

- `run_all.py` runs every step in order.
- `app.py` starts the app: the planning agent first, then the dashboard. The dashboard's pages and charts are in `dashboard/` (`data.py` reads `output/`, `charts.py` and `part_charts.py` draw the charts, one file per page).
- `assistant/` holds the planning agent: `agent.py` (the models and the agent), `prompt.py` (the system prompt and the guide to the tables), `tools.py` (the four tools), `database.py` (the read-only SQLite copy of `output/`), `page.py` (the chat page), `ask.py` (the terminal version) and `check.py` (the test set).
- `docs/` holds the demo screenshots.
- `src/` holds one module per step. Each module also runs on its own and prints a few hand checks, for example `.\.venv\Scripts\python.exe -m src.supply_review`. The modules are:
  - `config.py`: paths, snapshot date, forecast months.
  - `load_raw.py`: reads the raw CSVs, every column as text (ASSUMPTIONS.md 2.2).
  - `standardize.py`: part numbers, locations, demand types, planner and part type labels, quantities and dates, and the log of every value it changed (section 3).
  - `clean.py`: duplicates, negatives, blank months, missing `qpc`, unknown parts, and the flag list (section 4).
  - `master.py`: one row per part and location, and which demand counts at each site (4.1 to 4.3, 5.1).
  - `wos.py`, `status.py`, `rollup.py`: WOS, status and the DC summary (5.2 to 5.4).
  - `supply_review.py`: the day-by-day projection to Jan 31 and the supply review (5.5).
  - `output.py` and `charts.py`: write the tables and the chart in `output/`.
- `exploration/` holds short read-only scripts with the evidence for each decision. `ASSUMPTIONS.md` points to the one behind each entry. Run them the same way, for example `.\.venv\Scripts\python.exe exploration\08_wos_evidence.py`.
- `notebooks/01_data_exploration.ipynb` is the first look at the raw files.
