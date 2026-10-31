"""The planning agent's test set. Run from the repo root:

    .venv\\Scripts\\python.exe -m planning_agent.evaluate                    (Claude Sonnet 5.5)
    .venv\\Scripts\\python.exe -m planning_agent.evaluate --model="GPT-5.5"

Each question is checked three ways: the agent used a tool that fits, its answer names the right facts (worked out
here with pandas from output/, so they stay true if the pipeline changes), and every number in the answer appears in
something the model saw (a tool result, the question or the system prompt), up to rounding. A number the model
worked out in its head fails that last check. The written answers are printed too, to read by eye.
"""
import os
import re
import sys
import time
import uuid

import streamlit.logger
from langgraph.checkpoint.memory import InMemorySaver

streamlit.logger.set_log_level("error")  # the dashboard's cache warns when it runs outside the app, harmlessly
os.environ["LANGSMITH_PROJECT"] = "inventory-planning-assistant-dev"  # test runs stay out of the real project

# imported after those lines, or the warnings print and the traces land in the real project
from planning_agent.agent import DEFAULT_MODEL, ask, build_agent, message_text  # noqa: E402
from planning_agent.database import build_database  # noqa: E402
from planning_agent.prompt import RULES  # noqa: E402
from dashboard.data import load_tables  # noqa: E402
from src.config import LOCATIONS  # noqa: E402

CHART_TOOLS = {"show_chart", "make_chart"}
DATA_TOOLS = {"run_sql", "show_chart", "make_chart"}
SAYS_NOT_FOUND = ["not in", "no row", "isn't", "doesn't", "don't", "no record", "not found", "couldn't find",
                  "no data", "does not", "do not", "nothing on", "can't", "cannot"]
# small whole numbers (counts under a dozen, months, list numbers) are too common to tell anything apart
SMALLEST_CHECKED = 13


def status_cases(tables: dict) -> list[dict]:
    """Tesla's four examples and the status questions, with the facts each answer must name."""
    pairs, summary = tables["inventory_health"], tables["dc_summary"].set_index("location")
    okafor = pairs[pairs["planner"] == "Daniel Okafor"]
    excess = okafor[(okafor["location"] == "PDC2") & (okafor["status"] == "Excess")]["part_number"]
    gdc = pairs[pairs["location"] == "GDC"]
    at_risk = gdc["status"].isin(["Stockout", "Short"]).groupby(gdc["part_type"]).mean()
    no_demand = [summary.at[site, "dead_stock_pallets"] + summary.at[site, "wanted_elsewhere_pallets"]
                 for site in LOCATIONS]
    pdc1 = pairs[pairs["location"] == "PDC1"]
    out_pdc1 = [(pdc1["status"] == "Stockout").sum(), (pdc1["status_jan31"] == "Stockout").sum()]
    return [
        {"name": "Tesla 1: Excess list", "turns": ["Show me all Excess parts for Daniel Okafor at PDC2"],
         "tools": DATA_TOOLS, "facts": sorted(excess)},
        {"name": "Tesla 2: status by part type", "turns": ["Break down status by part type at the GDC"],
         "tools": CHART_TOOLS, "facts": [at_risk.idxmax()], "chart": True},
        {"name": "Tesla 3: 24-month demand", "turns": ["Chart the 24-month demand for part SKU98 across locations"],
         "tools": CHART_TOOLS, "facts": ["PDC2"], "chart": True},
        {"name": "Tesla 4: no-demand pallets", "turns": ["How many pallets of no-demand inventory sit at each location?"],
         "tools": DATA_TOOLS, "facts": no_demand},
        {"name": "PDC1 today vs Jan 31", "turns": ["How does PDC1 look today compared with Jan 31?"],
         "tools": DATA_TOOLS, "facts": out_pdc1},
        {"name": "planner with most Stockouts", "turns": ["Which planner has the most Stockout pairs?"],
         "tools": DATA_TOOLS, "facts": [pairs[pairs["status"] == "Stockout"]["planner"].value_counts().idxmax()]},
    ]


def supply_cases(tables: dict) -> list[dict]:
    """Questions about incoming supply, run-outs, actions and moves."""
    pairs, lines, shortfalls = tables["inventory_health"], tables["supply_review_lines"], tables["gdc_shortfalls"]
    okafor_out = pairs[(pairs["planner"] == "Daniel Okafor") & pairs["run_out_date"].notna()]
    sku98_pdc2 = pairs[(pairs["part_number"] == "SKU98") & (pairs["location"] == "PDC2")].iloc[0]
    sku82_line = lines[(lines["part_number"] == "SKU82") & (lines["destination"] == "PDC2")].iloc[0]
    return [
        {"name": "Okafor run-out timeline", "turns": ["When do Daniel Okafor's parts run out?"],
         "tools": DATA_TOOLS, "facts": [len(okafor_out)]},
        {"name": "SKU98 stock to Jan 31", "turns": ["Chart the stock for SKU98 day by day to Jan 31"],
         "tools": CHART_TOOLS, "facts": [-sku98_pdc2["stock_jan31"]], "chart": True},
        {"name": "POs to expedite", "turns": ["Which PO lines should I expedite, and why?"],
         "tools": DATA_TOOLS, "facts": [(lines["action"] == "expedite the PO").sum()]},
        {"name": "GDC parts to order", "turns": ["Which GDC parts need an order now?"],
         "tools": DATA_TOOLS, "facts": [shortfalls["action"].str.contains("order now").sum()]},
        {"name": "what-if move", "turns": ["What if I move 926 pieces of SKU98 from the GDC to PDC2?"],
         "tools": {"what_if_move"}, "facts": [926, "Healthy"]},
        {"name": "custom chart", "turns": ["Chart the pieces landing per week by site from the open supply"],
         "tools": CHART_TOOLS, "facts": [], "chart": True},
        {"name": "memory follow-up", "turns": ["Show me all Excess parts for Daniel Okafor at PDC2",
                                               "Which of those have supply on the way?"],
         "tools": DATA_TOOLS, "facts": ["SKU82"], "say_one_of": [sku82_line["po_number"].lower(), "transfer"]},
    ]


def trap_cases() -> list[dict]:
    """Questions the data can't answer, or shouldn't: the agent must say so, not make something up."""
    return [
        {"name": "trap: made-up part", "turns": ["What is the status of SKU999 at PDC2?"],
         "tools": None, "facts": ["SKU999"], "say_one_of": SAYS_NOT_FOUND},
        {"name": "trap: unknown part SKU903", "turns": ["Who is the planner for SKU903?"],
         "tools": None, "facts": ["part master"]},
        {"name": "trap: shared name", "turns": ["How is the compressor doing?"],
         "tools": None, "facts": ["SKU57", "SKU88", "SKU98"]},
        {"name": "trap: money", "turns": ["How much money is tied up in excess stock at the GDC?"],
         "tools": None, "facts": [], "say_one_of": ["cost", "price", "value"], "must_not": ["$"]},
        {"name": "trap: off-topic", "turns": ["Write me a short poem about the ocean"],
         "tools": set(), "facts": [], "say_one_of": ["planning", "inventory", "stock", "parts"]},
    ]


def without_codes(text: str) -> str:
    """The text without codes like SKU98, PO1022 and PDC1, whose digits aren't quantities."""
    return re.sub(r"\b(SKU|PO|PDC)\d+", " ", text, flags=re.IGNORECASE)


def numbers_in(text: str, thousands: bool = True) -> list[tuple[float, int, bool]]:
    """Every number in a text as (value, decimals, is a percent).

    With thousands, "1,063" is one number, as in written answers. Without, every comma splits, as in the CSV the
    tools return ("2027-02,720.0" is 720, not 2,720).
    """
    pattern = r"(\d{1,3}(?:,\d{3})+(?!\d)(?:\.\d+)?|\d+(?:\.\d+)?)(%?)" if thousands else r"(\d+(?:\.\d+)?)(%?)"
    found = []
    for match in re.finditer(pattern, text):
        digits = match.group(1).replace(",", "")
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        found.append((float(digits), decimals, match.group(2) == "%"))
    return found


def grounded(value: float, decimals: int, percent: bool, evidence: list[float]) -> bool:
    """True if the number appears in what the model saw, give or take one unit of its last digit.

    One unit, not half: 862.4 for 862.46 is a rounding slip, not a number from nowhere.
    """
    tolerance = 10 ** -decimals + 1e-9
    candidates = [(value, tolerance)]
    if percent:  # 46% can come from a share of 0.46
        candidates.append((value / 100, tolerance / 100))
    for number, allowed in candidates:
        if any(abs(seen - number) <= allowed for seen in evidence):
            return True
    return False


def made_up_numbers(answer: str, evidence_text: str) -> list[str]:
    """Numbers in the answer that appear in nothing the model saw. Codes like SKU98 and PDC1 aren't numbers."""
    # what the model saw is prose and CSV mixed, so read it both ways and keep both
    evidence = [value for value, _, _ in numbers_in(evidence_text) + numbers_in(evidence_text, thousands=False)]
    missing = []
    for value, decimals, percent in numbers_in(without_codes(answer)):
        if decimals == 0 and not percent and value < SMALLEST_CHECKED:
            continue
        if not grounded(value, decimals, percent, evidence):
            missing.append(f"{value:g}{'%' if percent else ''}")
    return missing


def mentions(answer: str, fact) -> bool:
    """A text fact appears in the answer, or a number does, rounded any reasonable way."""
    if isinstance(fact, str):
        return fact.lower() in answer.lower()
    for value, decimals, _ in numbers_in(without_codes(answer)):
        allowed = 0.5 if decimals == 0 else 0.1  # 194.34 may read as 194, 194.3 or 194.4
        if abs(value - abs(float(fact))) <= allowed:
            return True
    return False


def run_turns(agent, engine, turns: list[str]) -> tuple[list, str, str]:
    """Ask each turn in one conversation. Returns all messages, the last answer, and what the model saw."""
    thread_id, messages = str(uuid.uuid4()), []
    for question in turns:
        messages += list(ask(agent, question, thread_id, engine))
    seen = [RULES] + turns + [message_text(message) for message in messages if message.type == "tool"]
    answers = [message_text(message) for message in messages if message.type == "ai" and not message.tool_calls]
    return messages, answers[-1] if answers else "", "\n".join(seen)


def tools_used(messages: list) -> set[str]:
    """The names of the tools the agent called."""
    used = set()
    for message in messages:
        if message.type == "ai":
            used.update(call["name"] for call in message.tool_calls)
    return used


def drew_chart(messages: list) -> bool:
    """True if a tool result carries a chart for the page."""
    artifacts = [message.artifact for message in messages if message.type == "tool" and message.artifact]
    return any("figure" in artifact for artifact in artifacts)


def judge(case: dict, messages: list, answer: str, seen: str) -> dict:
    """The three checks for one case, plus the extra wording checks for the traps."""
    used, expected = tools_used(messages), case["tools"]
    problems = []
    if expected == set() and used:  # off-topic: no tool at all
        problems.append(f"tools {sorted(used)}")
    elif expected and used.isdisjoint(expected):
        problems.append(f"tools {sorted(used) or 'none'}")
    if case.get("chart") and not drew_chart(messages):
        problems.append("no chart drawn")
    missing = [str(fact) for fact in case["facts"] if not mentions(answer, fact)]
    if missing:
        problems.append(f"missing {missing}")
    plain = answer.lower().replace("’", "'")  # some models write "don’t" with a curly apostrophe
    if case.get("say_one_of") and not any(word in plain for word in case["say_one_of"]):
        problems.append(f"says none of {case['say_one_of']}")
    if any(word in answer for word in case.get("must_not", [])):
        problems.append(f"says {case['must_not']}")
    made_up = made_up_numbers(answer, seen)
    if made_up:
        problems.append(f"numbers from nowhere {made_up}")
    return {"tools": sorted(used), "problems": problems}


def main(arguments: list[str]) -> None:
    """Run every case on one model, print one line per case, then the answers to read."""
    model_name = DEFAULT_MODEL
    for argument in arguments:
        if argument.startswith("--model="):
            model_name = argument.removeprefix("--model=")
    tables = load_tables()
    cases = status_cases(tables) + supply_cases(tables) + trap_cases()
    agent, engine = build_agent(model_name, InMemorySaver()), build_database()
    results = []
    for case in cases:
        start = time.time()
        try:
            messages, answer, seen = run_turns(agent, engine, case["turns"])
            verdict = judge(case, messages, answer, seen)
        except Exception as error:  # one failed call shouldn't stop the rest of the run
            answer, verdict = "", {"tools": [], "problems": [f"error {type(error).__name__}: {error}"]}
        results.append((case, answer, verdict, time.time() - start))
        status = "PASS" if not verdict["problems"] else "FAIL"
        print(f"{status}  {case['name']:<30} {time.time() - start:5.1f} s  tools {verdict['tools']}  "
              f"{'; '.join(verdict['problems'])}", flush=True)
    passed = sum(not verdict["problems"] for _, _, verdict, _ in results)
    print(f"\n{model_name}: {passed} of {len(results)} passed\n")
    for case, answer, verdict, _ in results:
        print("=" * 100, f"\n{case['name']}: {' / '.join(case['turns'])}\n\n{answer}\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # the Windows console can't print every character in the answers
    main(sys.argv[1:])
