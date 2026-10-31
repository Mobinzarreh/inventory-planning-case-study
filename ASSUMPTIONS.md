# Assumptions and definitions

The definitions, assumptions and data decisions behind the numbers, and what I would do with more time. Each entry gives the rule, why (with the number that settled it), and where it sits in the code. The short answer to each task is on the dashboard's "The four tasks" page. Section numbers double as the `rule` IDs in `output/flags.csv`, which lists every data fix and flag (also on the Data quality page), and the `exploration/` scripts rerun the evidence.

## 1. Definitions

The terms used in the output, the summary and the rest of this file.

| Term | Meaning |
|---|---|
| Pair | One part at one location. The output has 340 (4.3). |
| Pieces | Every quantity and calculation is in pieces (eaches), as the task states. |
| Pallet equivalents | Pieces ÷ `qpc` (pieces per standard pallet), per part, then summed. A measure of space, not counted pallet positions. 500 pieces with a `qpc` of 250 is 2. |
| Direct demand | Customer demand served from a site. |
| Planned restock | The Replenishment rows: the GDC's monthly plan to ship to a PDC (`ship_to`). A month and a quantity, no date. |
| Open supply | The incoming supply lines, each with an ETA: supplier POs, and GDC transfers already on the road to a PDC. |
| WOS | Weeks of supply: today's on-hand ÷ average weekly demand over Nov 2026 to Jan 2027 (5.2). |
| Status | Stockout (demand, nothing on hand), Short (under 4 weeks), Healthy (4 to 26), Excess (over 26), No demand (none at that site in 24 months) (5.3). |
| Dead stock | No demand, and the part has no demand anywhere in the network. |
| Wanted elsewhere | No demand here, but customer demand for the part at another site. |
| Backorder | Stock below zero: orders taken with nothing on the shelf (4.6). |
| Run-out date | The first day the projection ends with at least one whole piece of demand unmet (5.5). |

Dates: on-hand is as of 2026-10-22, the forecast runs Nov 2026 to Oct 2028 (24 months), and the supply review runs to 2027-01-31.

Files join on `part_number` plus location, never on `part_description`: 100 parts share 75 names (SKU57, SKU88 and SKU98 are all Compressor). A supply line is one PO line (`po_number` + `po_line`).

**Planned restock comes in whole pallets.** All 2,633 positive restock months (parts with a usable `qpc`) are whole numbers of pallets, against 11% to 14% for on-hand, POs, transfers and Direct demand. It is a pattern, not a rule, so nothing is rounded because of it, but it is evidence for 4.7, 4.9 and the restock timing in 5.5.

## 2. Reading the raw files

**2.1 The files in `data/` are never edited.** Every fix happens in code, so it can be traced and rerun.

**2.2 Every column is read as text, then converted in one place.** A default read guesses types without warning: `on_hand_qty` loads as text because of two thousands commas (`1,041`), and blank cells and `N/A` become the same missing value. Reading as text keeps every raw value countable, and the conversion stops with an error on any value it does not recognize. *Code: `src/load_raw.py`, `src/standardize.py`.*

## 3. Standardizing formats

These steps change how a value is written, never what it means, and no row is added or removed. Every change is counted in `output/standardizing_log.csv` (1,848 values in 15 file and column pairs) and shown on the Data quality page.

| | Field | Raw examples | Rule | Why |
|---|---|---|---|---|
| 3.1 | Part numbers | `sku16`, `SKU-53`, ` SKU24`, `SKU0022` | Drop case, dashes, spaces and leading zeros | Inventory's 117 raw spellings become 102 parts, and every part then matches `parts.csv` except SKU901 to SKU903 (4.10). No new clashes. |
| 3.2 | Locations (`location`, `destination`, `ship_to`) | `gdc`, `Global DC`, `PDC-3`, `pdc3` | One name per site | Up to 16 raw spellings per column collapse to exactly the four sites, with nothing left over. |
| 3.3 | Demand type | `DIRECT`, `Direct `, `Replen` | Direct or Replenishment | Matches the task: all 212 Replenishment rows are at the GDC with a PDC in `ship_to`, and no Direct row has one. |
| 3.4 | Planner and part type | `DANIEL OKAFOR`, `glass ` | Labels that differ only in case or spaces are one label, in its most common spelling | 10 planner spellings become 5 planners, 13 part types become 10. Nothing genuinely different is merged. |
| 3.5 | Quantities | `38.0`, `"1,041"` | Whole numbers, commas removed | No quantity has a real fraction. Blank and `N/A` stay missing until cleaning (4.8). |
| 3.6 | Dates | `2026-11-13`, `11/15/2026`, `04-Dec-2026` | Slash dates are month/day | 7 of the 18 slash dates fit either order. As month/day they all land inside the other ETAs' range, while as day/month five would fall before the snapshot. |

*Code: `src/standardize.py` (`standardize_part_number`, `standardize_location`, `standardize_demand_type`, `standardize_label`, `parse_quantity`, `parse_date`).*

## 4. Cleaning decisions

Nothing is dropped or changed silently: every row set aside, changed or odd gets a flag saying what was found and what it might mean, such as "possible double entry, check with the buyer". The 139 flags, with their full text, are in `output/flags.csv`, the workbook's `flags` sheet and the Data quality page. The cleaned files are in `output/clean/`. *Code: `clean_all` in `src/clean.py`.*

**4.1 A part at a location with no inventory row has 0 on hand.** 41 pairs have a demand row but no inventory row, 35 of them with real demand. The inventory file has no blank cells, so a missing row is the only way stock goes unrecorded, and 0 shows the pair instead of hiding it. The restock plan agrees: all 33 such PDC pairs have a restock row. *Code: `fill_missing_sources` in `src/master_table.py` (column `has_inventory_row`). Evidence: `exploration/04`, `05`.*

**4.2 A demand row of zeros in all 24 months counts as no demand.** 12 Direct rows are all zero, and the GDC plans no restock for any of the 9 at PDCs, so the plan treats them like missing rows. No forecast starts late either: every pair with demand has some in the first 3 months. *Code: `label_pairs` in `src/master_table.py`. Evidence: `exploration/02`, `04`.*

**4.3 The output covers every pair with an inventory row, real demand or incoming supply: 340 pairs.** 304 have an inventory row, 35 have demand but no row, and 1 has only supply (SKU903 at PDC1, 4.10). A planner can act on any of them. The 6 pairs whose only record is an all-zero demand row are set aside and flagged (possibly a forecast row left over for a part the site no longer carries). *Code: `label_pairs` and `set_aside_empty_pairs` in `src/master_table.py`. Evidence: `exploration/05`.*

**4.4 Exact duplicate rows: one copy is kept.** 12 rows across the four files are full copies of another row. Once they are dropped no key repeats anywhere, so each copy is the same record twice: keeping both would bring PO1011 line 10 in as 8,864 pieces of SKU43 instead of 4,432. *Code: `drop_exact_copies` in `src/clean.py`. Evidence: `exploration/06`, section 1.*

**4.5 Supply lines that look alike are all kept, and flagged.** 11 pairs of lines send the same part and quantity to the same site on different PO lines and dates (1,136 pieces of SKU43 to the GDC on PO1050 and again on PO1006). Each has its own PO number and line, and ordering the same lot size again is common, so nothing proves a double entry. If one is, that part's supply is overstated. *Code: `flag_look_alike_supply` in `src/clean.py`. Evidence: `exploration/06`, section 1.*

**4.6 Negative on-hand is kept as a backorder.** 4 rows, from −4 to −18, all with real demand: most likely orders taken while the shelf was empty. Resetting to 0 would hide pieces already owed: SKU92 at PDC2 has 49 pieces coming, but only 31 are free once its 18 waiting orders are filled. WOS is 0, status is Stockout, and incoming supply fills the backorder first. *Code: `flag_negative_on_hand` in `src/clean.py`. Evidence: `exploration/06`, section 2a.*

**4.7 A negative forecast month is treated as a blank and filled as in 4.8.** 6 cells, all in Mar 2027 or later. Neither demand nor a planned shipment can be negative. Zero would understate months like SKU98's restock, which runs 704 to 824 around its −12, and flipping the sign fits some cells but not others. The average of the neighbouring months fits all six. *Code: `fill_forecast_gaps` in `src/clean.py`. Evidence: `exploration/06`, sections 2b and 6.*

**4.8 A blank forecast month gets the average of the month before and the month after** (the last month takes the month before). 42 cells are blank or `N/A`. A blank means unknown, not zero: SKU44 at the GDC in 2028-01 sits between 561 and 452. With 4.7, the 48 fills add 0.5% to all forecast pieces, and none falls in Nov to Jan, the WOS window. Filled restock months are not rounded to pallets. *Code: `fill_forecast_gaps` in `src/clean.py`. Evidence: `exploration/06`, sections 3 and 6.*

**4.9 A missing or zero `qpc` is estimated from the restock pallet pattern.** SKU57 and SKU34 have no `qpc` and SKU5 has 0. Since restock comes in whole pallets (section 1), the largest number that divides every restock month of a part should be its `qpc`, and it gives the right value for 84 of the 85 parts with a known one. So SKU57 gets 6, SKU34 6 and SKU5 4, marked "estimated" in the output. `qpc` only turns pieces into pallets, so WOS and status never depend on it. *Code: `estimate_missing_qpc` in `src/clean.py`, run before 4.7 and 4.8 so filled months never feed it. Evidence: `exploration/06`, section 4.*

**4.10 Parts not in the part master are kept and flagged, not mapped to a known part, and not called dead stock.** SKU901 has 35 pieces at PDC2, SKU902 has 120 at the GDC, and SKU903 has 60 arriving at PDC1 on PO9999 (the only PO number outside PO1001 to PO1054). The pieces are real, so they stay in piece totals. With no `qpc` they are left out of pallet totals, and every pallet figure says so. Every other odd spelling resolves by format (3.1), and a typo test against the restock plan rules out SKU91 and SKU93 and can't confirm the rest, so no stock is moved to a guess. A missing forecast doesn't prove a part obsolete, so they are not dead stock. Each is flagged as a possible new part, typo or test record, with the best guess. SKU1 at PDC2 is flagged too, because SKU901 could be SKU1 (17 pieces would become 52, still Healthy). *Code: `flag_unknown_parts` in `src/clean.py`. Evidence: `exploration/06` section 5, `exploration/07`.*

## 5. Method

**5.1 Which demand counts at each site.** A PDC's demand is its own Direct row. The GDC's demand is its own Direct row plus all its Replenishment rows, because both leave its shelf, to customers or to a PDC. PDC Direct demand is never added to the GDC as well, and a GDC transfer is incoming supply at the PDC it is heading to, never at the GDC (the task says it has already left).
- Why: the restock plan already turns PDC demand into GDC shipments. All 212 Replenishment rows match a Direct row for the same part at the PDC in `ship_to`, and the plan allows for each PDC's own stock: restock alone keeps up with PDC demand in a median 40% of months, but restock plus the PDC's on-hand keeps up in every month for 140 of the 212. Adding PDC demand to the GDC would count the same pieces twice.
- In round numbers: PDC1 sells 100 a month, and the GDC sells 50 to its own customers and plans to ship PDC1 80. The GDC needs 50 + 80 = 130 a month, not 50 + 100 + 80 = 230.
- Side effect: restock months are whole pallets and often zero, so GDC demand is lumpy from month to month (handled in 5.2).

*Code: `demand_by_pair`, `restock_in_by_pair` and `supply_by_pair` in `src/master_table.py`. Evidence: `exploration/02`, `03`.*

**5.2 Weeks of supply (WOS) for today's stock.** WOS = on-hand ÷ average weekly demand over Nov 2026 to Jan 2027, where weekly demand = (Nov + Dec + Jan) ÷ 13.14 (the window's 92 days ÷ 7). Demand follows 5.1, and months with no planned restock count as zeros. On-hand of 0 or less gives WOS 0 (4.6), and a pair with no demand gets no WOS.
- Example: SKU6 at the GDC has 76 pieces and needs 115 + 21 + 16 = 152 in Nov to Jan, so 11.6 a week and a WOS of 6.6 weeks.
- Why the next 3 months:
  - It is the demand today's stock will actually meet. Walking the forecast forward, the median pair with stock runs out in 6.4 to 10.2 weeks, depending on the site.
  - It matches that run-out time best: within 2 weeks or 20% for 219 of 253 pairs, against 214 for a 6-month window, 211 for 12 months and 196 for 24.
  - It uses only real forecast numbers (none of the 48 filled cells), demand has no strong trend (every 3-month block is within 0.90 to 1.15 of it), and all open supply lands inside it.
- WOS counts from the snapshot, Oct 22: the 10 days before November have no forecast, so they count at the Nov to Jan rate, not as free cover.
- Known limit: one average rate can hide timing. SKU6's 6.6 weeks run out after 4.1, because November alone needs 115. 34 of the 253 pairs with stock are off by more than 2 weeks and 20%, and the dated projection (5.5) shows them.

*Code: `add_wos` in `src/weeks_of_supply.py`. Evidence: `exploration/08`.*

**5.3 Inventory status.** Every pair gets one of five statuses from its WOS (5.2). The lines are my choice, and the same at all four sites.

| Status | Rule |
|---|---|
| Stockout | Demand, but WOS 0: nothing on hand, a backorder, or no inventory row |
| Short | WOS above 0 and under 4 weeks |
| Healthy | WOS from 4 to 26 weeks |
| Excess | WOS over 26 weeks |
| No demand | No demand at this site in any of the 24 months. `no_demand_reason` says why: dead stock, wanted at another site, or not in the part master (4.10, never dead stock) |

- Why 4 weeks: it is about one restock cycle. 112 of the 212 restock rows plan a shipment every month, so a site with less than a month of stock is likely to run out before the next delivery. It is also where WOS and the actual run-out time agree: no pair with 4 weeks or more runs out within 4 weeks.
- Why 26 weeks:
  - Normal deliveries should not look like Excess. Whole pallets make some deliveries big: 39 of the 212 restock rows deliver more than 13 weeks of demand at once.
  - Restocking takes time. Open supply lands up to 11.7 weeks after the snapshot, so a site waiting on a slow order needs about 12 weeks of stock to bridge the wait.
  - A higher line would hide most of the excess: 1,168 pallets sit above 26 weeks, against 618 above 39 and 232 above 52.
- Why the same lines everywhere: open supply lands over the same span at the GDC and the PDCs, so nothing in the data shows one site waiting longer than another.
- Watch list: the 23 Healthy pairs above 13 weeks stay Healthy, but are counted as a group in the DC summary (5.4).
- Timing flag: a pair with 4 weeks or more that still runs out within 4 weeks, when the forecast is walked month by month, keeps its status and gets the flag "runs out sooner than WOS suggests". No pair gets it in this run.

*Code: `label_status`, `label_no_demand_reason` and `flag_early_run_out` in `src/status.py`. Evidence: `exploration/09`.*

**5.4 DC-level summary.** One row per DC, plus an all-sites row, in three parts:
1. **Status mix:** pairs in each status (5.3), as counts and as shares of the DC's pairs. This leads the view.
2. **Space, in pallet equivalents:** on hand, above the 26-week line (Excess pairs only), dead stock, and wanted at another site, with nothing counted twice. Parts not in the part master are shown in pieces (4.10).
3. **Action counts** that overlap the statuses and are never added to them: the watch list and the timing flags (5.3).

- Why a status mix and not one WOS number: one number hides the spread, and each way of making it has its own bias. PDC1's average WOS is 11.0 weeks, its median is 2.6, and weighting by pieces or by pallets gives 4.4 or 10.6. An average also lets excess on some parts hide shortages on others: the GDC holds 862 pallet equivalents above 26 weeks while its Stockout and Short pairs are 2,838 pieces short of 4 weeks. The status mix is WOS sorted into the 5.3 bands, so it keeps the spread, and WOS per pair stays in the output.
- Why count pairs: a missing cheap part can stop a repair just like an expensive one, and the data has no cost or criticality to weight by.
- Why pallet equivalents for space: pieces mix bolts (1,000 per pallet) and windshields (10), and pallet equivalents put them on one scale of warehouse space.

*Code: `build_dc_summary` in `src/dc_summary.py` (it checks that the all-sites row adds back to the master table) and `plot_status_mix` in `src/status_chart.py`. Evidence: `exploration/10`.*

**5.5 Incoming supply review: a dated projection to Jan 31.** Every pair is projected day by day from the snapshot to 2027-01-31, in whole pieces: stock = on-hand + supply landed + planned restock received − demand, so far. Each PO line lands in full on its ETA, stock below zero is a backorder (4.6), and a day that ends with at least one whole piece of demand unmet is a day without stock. Each pair gets a run-out date, its days without stock, and its stock, WOS and status on Jan 31 (the 5.2 rate and the 5.3 lines). Each PO line gets a verdict and an action.
- Why a dated projection and not WOS after supply: a delivery that lands after the shelf is empty does not prevent the stockout. 42 of the 109 PO lines land after their site has already run out.
- Why Jan 31: all open supply lands by 2027-01-12, so every line lands with almost three weeks left to see its effect, and it is the same window as WOS. The data has no order dates, so lead times are unknown, and no new order is assumed to land before Jan 31.

**Timing: customers buy every day, planned restock ships once a month.** The two flows work differently, so they get different patterns.
- Planned restock has a month and a quantity but no date, unlike open supply, which has an ETA and lands on it. Restock months are whole pallets (section 1), which reads as one monthly shipment: 28% are a single pallet, and only 14% could be split into four weekly whole-pallet shipments. So it ships on the 1st of the month it covers.
- Customer demand is loose pieces. Only 13% of customer months are whole pallets, and the median is 16 pieces a month. So each month's demand is spread evenly over its days in whole pieces: a glass that sells 7 a month leaves the shelf about every 4 days.
- Evidence: the same monthly numbers, timed other ways (`exploration/12`).

  | | Chosen | Restock spread evenly | Customers once a month (1st / 15th / last day) |
  |---|---|---|---|
  | Pairs that run out by Jan 31 | 158 | 158 | 167 / 155 / 150 |
  | PO lines that land too late | 42 | 44 | 54 / 38 / 28 |
  | Stockout on Jan 31 | 114 | 116 | 117 / 114 / 114 |

  Spreading restock over the month changes almost nothing, so the results don't hang on the real restock cadence. Batching customers once a month does: late lines swing from 28 to 54 with the day picked, so customers stay a daily flow.

**Planned restock at the PDCs: only what the GDC can send.** The GDC fills all its orders in date order, its own customers and the PDCs alike. When it is empty, orders wait, and a landing PO serves them first. Restock counts at the PDC on the day it leaves the GDC.
- Why: the plan asks the GDC for 27,311 pieces in Nov to Jan, but with its stock and open POs it can send 14,813 (54%). On Jan 31, counting the full plan would leave 8 PDC pairs in Stockout (too hopeful), leaving restock out would leave 145, and counting what the GDC can send leaves 62.
- GDC transfers already on the road count apart from the plan: only 1 of the 43 matches the planned restock in the month it lands.

**No restock before November.** The plan has no October rows, so no restock moves from Oct 22 to 31, while customers keep buying at November's rate. Assuming October restock at November's pace would add 4,475 pieces the plan does not show. Flag, on 40 PDC pairs that run out before November while the GDC holds the part (1,002 pieces short): "runs out before the restock plan starts".

**Verdict for each PO line** (a later rule wins):
- on time: keep.
- adds to excess: part of the line sits above 26 weeks on Jan 31. Push out or cut those pieces (for a transfer already on the road, cut the next planned restock).
- lands too late: the site is already short when it lands. Expedite the PO, or bridge a transfer from another site.
- no demand here or anywhere: redirect to a site with demand, or cancel or return.
- not in the part master: check the part number before it lands (4.10).

**GDC shortfalls.** A GDC part that runs out by Jan 31 is "order now" when no PO lands after it runs out (47 of the 53), and "expedite the open PO and check it covers the gap" otherwise.

**Moves.** Spare stock (above 26 weeks at an Excess pair, or at a no-demand pair another site wants) goes to the biggest backorder first. At the GDC only its own customers count as need, because restock it can't send already shows up at the PDCs. A move against the usual GDC to PDC route says "check if a transfer is possible". The list is candidates to check, not a plan: 35 of the 48 go against the usual route. Caveat: the GDC's 1,540 pieces of SKU98 look Excess on the Nov to Jan rate (65 weeks), but they are PDC2's Feb to Apr restock, held early. Sending 926 to PDC2 now is still right, since it is the same PDC, but the GDC then needs an order.

**Known limits:**
- The real restock cadence and GDC to PDC transit time are unknown, taken as once a month on the 1st and zero.
- Demand is the expected pace, not random, so run-out dates are expected dates and safety stock is not modelled.
- Each PO lands in full on its ETA.
- Moves are matched greedily, biggest need first, not optimized, and have no minimum size: 13 of the 48 are under 5 pieces.

*Code: `src/supply_review.py`: `project_stock` for the projection (saved as `output/daily_stock.csv`), `review_supply_lines`, `gdc_shortfalls` and `suggest_moves` for the lists, `flag_before_restock_plan` for the October flags. Evidence: `exploration/11`, `12`.*

**5.6 The planning agent (the bonus).** A language model answers planning questions in plain English but never computes a number. It picks one of four tools (`run_sql`, `show_chart`, `make_chart`, `what_if_move`) and their inputs, such as a read-only SQL query, and writes the words. Every number, table and chart comes from `output/` through our code, because the output tables already hold every derived number and recommendation.
- Each tool sends its full table or chart straight to the page. The model sees only a preview (the first 20 rows and the row count), enough to write its answer, and its advice comes from the pipeline's verdict and action columns. "How I got this" under each answer shows every step and its SQL.
- The what-if shows today's WOS and status at both sites (5.2, 5.3). It does not rerun the projection to Jan 31, and the answer says so.
- The model runs through ASU's CreateAI gateway. Claude Sonnet 5.5 is the default, and the app also offers Claude Opus 5.5 and GPT-5.5.

*Code: `planning_agent/` (`tools.py` for the tools, `prompt.py` for the rules).*

## 6. What I would do with more time

This analysis takes one snapshot and checks the plan that already exists. With more time I would grow it into a small digital twin of the network: the same data and rules, kept up to date and run forward many times, so a planner can test a choice before making it. The dashboard and the agent would be its front end.

**Simulation: stock, space and workload, day by day.** The projection (5.5) runs once, at the expected pace of demand. A simulation would run the same 102 days many times, with demand drawn at random around the forecast, and track space and handling as well as stock:
- Service risk: each run-out date becomes a range, for example "a 90% chance this part runs out between Nov 20 and Dec 5", and the Short and Excess lines can be set per part from lead time and demand variability (a safety stock rule) instead of 4 and 26 weeks for every part (5.3).
- Warehouse occupancy: pallet equivalents on hand per site and week, shown as a site-by-week heatmap against each site's capacity, to show where and when space runs short. The GDC already holds 2,645 of the 4,393 pallets on hand (60%), and 862 of them sit above 26 weeks.
- Receiving and shipping load: pallets arriving per site and day (PO ETAs and planned restock) and leaving (to customers and PDCs), to show the peak days a dock or a crew has to absorb.
- Before and after: the same runs for today's plan and for a changed one (the SKU98 move, a cut excess line, the optimizer's plan below), side by side, so planners, industrial engineers and managers see what a decision does to stockouts, space and workload, and where the bottleneck moves.

**Optimization.** Let a model choose the plan instead of only checking it. This is multi-period inventory planning on a two-level network (suppliers feed the GDC, the GDC feeds three PDCs), the same family of problem as production planning.
- Decisions, per part and month: supplier orders into the GDC, shipments from the GDC to each PDC, and the stock each site carries.
- Rules: stock balance at every site and month (backorders carry forward, as in 4.6), shipments in whole pallets (`qpc`), a minimum stock level such as the 4-week line, and a space limit per site in pallet equivalents.
- Goal: the lowest holding cost plus a penalty for each piece short, and possibly a fixed cost per shipment.

**What-if analysis for disruptions.** Today's what-if moves stock and shows today's WOS only (5.6). I would let it rerun the full projection under a scenario, compare it with the base case, and show which pairs newly run out, and when: a supplier PO that slips 2 or 4 weeks or arrives short, a supplier or a site stopped for a week, the GDC unable to ship to one PDC for a while, or a demand spike for one part type (a recall or a fleet campaign). The optimizer could then suggest the cheapest recovery.

**Cost and criticality.** With unit costs and a criticality class per part, rank shortages by service impact and show the money tied up in excess and dead stock. The DC view counts pairs and pallets because the data has neither (5.4).

**The agent.** Today it answers one question at a time with four tools (5.6). Next:
- The twin as tools: run a simulation, a disruption what-if or the optimizer, then explain the result.
- Workflows, not only questions: an orchestrated, multi-step run on each new snapshot that reruns the pipeline, finds what changed since the last one, drafts the planner's action list and sends it for approval. A person signs off before anything is ordered or moved.
- Connected to the team's tools through MCP (Model Context Protocol, an open standard for connecting AI agents to data and apps): its tools offered as an MCP server, so other agents and assistants can call them, and MCP connectors to the team's data sources and BI tools such as Tableau or Power BI, so it works on live data and its answers land where the team already looks.
- The basics for real use: conversations kept in a database so they survive a restart, a test set grown from real planner questions, and sign-in on any public deployment.
