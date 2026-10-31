"""The planning agent: ask about the inventory in plain English, get answers, charts and breakdowns.

The model only picks a tool and its inputs. Every number, table and chart comes from the planning tables and our
code (planning_agent/tools.py), and reaches the page without passing through the model.
"""
import os
import threading
import time
import uuid

import openai
import pandas as pd
import plotly.io
import streamlit as st
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from sqlalchemy import Engine
from streamlit.errors import StreamlitSecretNotFoundError

from planning_agent.agent import DEFAULT_MODEL, MODELS, ask, build_agent, message_text
from planning_agent.database import build_database
from planning_agent.tools import CHART_TITLES, PAGE_ROWS, readable
from dashboard.charts import NO_TOOLBAR
from dashboard.data import number_columns

EXAMPLES = [
    "Show me all Excess parts for Daniel Okafor at PDC2",
    "Break down status by part type at the GDC",
    "Chart the 24-month demand for SKU98 across locations",
    "How many pallets of no-demand inventory sit at each location?",
    "Which PO lines should I expedite, and why?",
    "What if I move 926 pieces of SKU98 from the GDC to PDC2?",
]
AVATARS = {"user": ":material/person:", "assistant": ":material/auto_awesome:"}
SHORT_TABLE_ROWS = 5  # the prompt lets the answer itself hold a table this short, so the page folds it away
ROLE = ("An AI agent on the service parts planning team. Ask about stock, supply and demand across the GDC and the "
        "three PDCs, in plain English, and get answers, charts and breakdowns.")
STEPS = ["You ask in plain English", "The agent plans and picks a tool", "Code runs the query, chart or what-if",
         "You get the answer, the chart and the SQL behind it"]
PROMISES = ("Numbers from the planning tables, never from the model · Read-only SQL · Charts from the dashboard · "
            "Remembers the conversation")
# a four-point sparkle, the usual mark for AI, in white on the agent's dark tile. A character rather than an SVG,
# which st.html strips out
SPARKLE = "✦"
# the app's palette (.streamlit/config.toml): a charcoal card with red as the agent's own color. Red stays in this
# frame and out of the answers, where the charts use it for Stockout
STYLE = """
<style>
.agent-card {border-radius: 16px; background: #222222; padding: 1.5rem 1.7rem; margin-bottom: 1rem;
    box-shadow: 0 10px 30px rgba(34, 34, 34, 0.18);}
.agent-head {display: flex; gap: 1rem; align-items: flex-start;}
.agent-mark {flex: none; width: 48px; height: 48px; border-radius: 14px; background: #cc0000; color: #ffffff;
    display: flex; align-items: center; justify-content: center; font-size: 1.6rem; line-height: 1;}
.agent-title {font-size: 1.9rem; font-weight: 700; line-height: 1.15; color: #ffffff; letter-spacing: 0.01em;}
.agent-pill {display: inline-block; vertical-align: middle; margin-left: 0.6rem; padding: 0.15rem 0.6rem;
    border-radius: 999px; background: #cc0000; color: #ffffff; font-size: 0.7rem; font-weight: 600;
    letter-spacing: 0.06em; text-transform: uppercase;}
.agent-role {color: #c8c8c8; font-size: 1.02rem; margin-top: 0.35rem;}
.agent-steps {display: flex; flex-wrap: wrap; gap: 0.6rem; margin-top: 1.2rem;}
.agent-step {flex: 1 1 180px; display: flex; gap: 0.6rem; align-items: center; padding: 0.6rem 0.8rem;
    border-radius: 10px; background: #2e2e2e; color: #f2f2f2; font-size: 0.88rem;}
.agent-step b {flex: none; width: 1.5rem; height: 1.5rem; border-radius: 999px; background: #222222;
    border: 1px solid #cc0000; color: #ffffff; display: flex; align-items: center; justify-content: center;
    font-size: 0.78rem;}
.agent-promises {margin-top: 0.9rem; color: #a6a6a6; font-size: 0.8rem;}
.st-key-examples button {min-height: 3.4rem; border: 1px solid #e0e0e0; background: #ffffff; border-radius: 0.6rem;}
.st-key-examples button:hover {border-color: #cc0000; color: #cc0000; background: #ffffff;}
</style>
"""


def load_secrets() -> None:
    """On Streamlit Cloud the keys sit in the app's secrets. Copy them to the environment, where the code reads them."""
    try:
        secrets = dict(st.secrets)
    except StreamlitSecretNotFoundError:  # running locally: the keys come from .env
        return
    for name, value in secrets.items():
        if isinstance(value, str):
            os.environ.setdefault(name, value)


@st.cache_resource
def shared_memory() -> InMemorySaver:
    """One memory for the whole server. Each browser session has its own conversation (thread) in it."""
    return InMemorySaver()


@st.cache_resource
def shared_database() -> Engine:
    """The read-only database, built once from output/ when the server starts."""
    return build_database()


@st.cache_resource
def agent_for(model_name: str):
    """One agent per model, all writing to the same memory."""
    return build_agent(model_name, shared_memory())


def new_conversation() -> None:
    """A fresh thread: the agent forgets the earlier questions. A question still running finishes in the old one."""
    st.session_state["thread_id"] = str(uuid.uuid4())
    st.session_state["turn_notes"] = {}
    st.session_state.pop("job", None)


def turns(messages: list) -> list[dict]:
    """Group the conversation into turns: the question, the tool calls and results, and the answer."""
    grouped = []
    for message in messages:
        if message.type == "human":
            grouped.append({"question": message, "calls": [], "results": [], "answer": ""})
        elif not grouped:
            continue
        elif message.type == "tool":
            grouped[-1]["results"].append(message)
        elif message.tool_calls:  # any text next to a tool call is the model thinking aloud, not the answer
            grouped[-1]["calls"].extend(message.tool_calls)
        else:
            grouped[-1]["answer"] += message_text(message)
    return grouped


def step_label(call: dict) -> str:
    """What a tool call is doing, in plain words, for the live status line."""
    args = call["args"]
    if call["name"] == "show_chart":
        return f"Drawing the chart: {CHART_TITLES.get(args.get('name'), 'chart').lower()}"
    if call["name"] == "make_chart":
        return f"Drawing a chart: {args.get('title', '')}"
    if call["name"] == "what_if_move":
        return f"Working out a move of {args.get('pieces')} {args.get('part')} to {args.get('to_site')}"
    return "Querying the planning tables"


def count(number: int, noun: str) -> str:
    """'1 row', '45 rows'."""
    return f"{number:,} {noun}" if number == 1 else f"{number:,} {noun}s"


def show_table(data: dict, key: str, label: str, expanded: bool) -> None:
    """A tool's table in an expander, with thousands separators, and a note when it is cut short."""
    table = pd.DataFrame(data["data"], columns=data["columns"])
    table = table.rename(columns=readable)
    with st.expander(f"{label} ({count(data['total_rows'], 'row')})", expanded=expanded, icon=":material/table:"):
        st.dataframe(table, column_config=number_columns(table), hide_index=True, key=key,
                     height=min(36 + 35 * len(table), 400))
        if data["total_rows"] > PAGE_ROWS:
            st.caption(f"The first {PAGE_ROWS:,} of {data['total_rows']:,} rows.")


def show_results(results: list) -> None:
    """The charts open, the tables folded away. With no chart, the first long table is the answer, so it opens.

    A short table stays folded: the written answer already names its numbers.
    """
    shown = [result for result in results if result.artifact]
    has_chart = any("figure" in result.artifact for result in shown)
    first_table = True
    for result in shown:
        artifact, key = result.artifact, result.tool_call_id
        if "figure" in artifact:
            st.markdown(f"**{artifact['title']}**")
            st.plotly_chart(plotly.io.from_json(artifact["figure"]), config=NO_TOOLBAR, key=f"chart_{key}")
            if artifact.get("note"):
                st.caption(artifact["note"])
            show_table(artifact["table"], f"table_{key}", "Numbers behind the chart", expanded=False)
        else:
            label = artifact.get("title", "Query result")
            long_table = artifact["table"]["total_rows"] > SHORT_TABLE_ROWS
            show_table(artifact["table"], f"table_{key}", label, expanded=first_table and long_table and not has_chart)
            first_table = first_table and not long_table


def show_steps(calls: list) -> None:
    """How the answer was found: each tool the agent used, with its inputs and the SQL it ran."""
    if not calls:
        return
    with st.expander(f"How I got this ({count(len(calls), 'step')})", icon=":material/manage_search:"):
        for number, call in enumerate(calls, start=1):
            args = dict(call["args"])
            sql = args.pop("query", None)
            inputs = ", ".join(f"{name} = {value}" for name, value in args.items() if value is not None)
            st.markdown(f"{number}. `{call['name']}` {inputs}")
            if sql:
                st.code(sql, language="sql", wrap_lines=True)


def show_turn(turn: dict) -> None:
    """One question and its answer, as chat bubbles."""
    with st.chat_message("user", avatar=AVATARS["user"]):
        st.markdown(turn["question"].content)
    with st.chat_message("assistant", avatar=AVATARS["assistant"]):
        st.markdown(turn["answer"])
        show_results(turn["results"])
        show_steps(turn["calls"])
        note = st.session_state["turn_notes"].get(turn["question"].id)
        if note:
            st.caption(note)


def run_job(job: dict, agent, thread_id: str, engine: Engine) -> None:
    """Run one question to the end in a background thread, writing each step to the shared memory.

    Streamlit stops the page's script when the visitor clicks another page, so the agent can't run inside it:
    an answer would be lost halfway. The page only watches this job, and finds the answer in memory later.
    """
    try:
        for message in ask(agent, job["question"], thread_id, engine):
            for call in getattr(message, "tool_calls", []):
                job["steps"].append(step_label(call))
    except Exception as error:  # the page shows it, a thread has nowhere else to report
        job["error"] = error
    finally:  # always marked done, or the page would wait forever
        job["seconds"] = time.time() - job["started"]
        job["done"] = True


def start_job(agent, question: str, model_name: str, turns_before: int) -> None:
    """Start the agent on a question in the background. The job lives in this visitor's session."""
    job = {"question": question, "model": model_name, "steps": [], "started": time.time(), "done": False,
           "error": None, "turns_before": turns_before}
    st.session_state["job"] = job
    worker = threading.Thread(target=run_job, args=(job, agent, st.session_state["thread_id"], shared_database()),
                              daemon=True)
    worker.start()


def watch_job(job: dict) -> None:
    """The running question with its steps live, until it is done. Leaving the page doesn't stop the job."""
    with st.chat_message("user", avatar=AVATARS["user"]):
        st.markdown(job["question"])
    with st.chat_message("assistant", avatar=AVATARS["assistant"]):
        with st.status("Thinking...", expanded=False) as status:
            shown = 0
            while not job["done"]:
                steps = job["steps"]
                for label in steps[shown:len(steps)]:
                    st.write(label)
                shown = len(steps)
                current = steps[-1] if steps else "Thinking"
                # updating the label each turn also lets Streamlit stop this loop when the visitor leaves
                status.update(label=f"{current} · {time.time() - job['started']:.0f} s")
                time.sleep(0.5)
            status.update(label="Done", state="complete")


def error_message(error: Exception) -> str:
    """A failure in plain words. Anything unexpected is raised, so it shows as a real error."""
    if isinstance(error, openai.RateLimitError):
        return "The AI service is busy or has reached its usage limit. Please try again in a minute."
    if isinstance(error, GraphRecursionError):
        return "That question took too many steps. Try asking it in a narrower way."
    if isinstance(error, openai.APIError):
        return f"The AI service didn't answer ({type(error).__name__}). Please try again."
    raise error


def finish_job(agent, job: dict) -> None:
    """Note the finished turn's model, steps and time, and draw it from memory. A failure starts a new conversation."""
    del st.session_state["job"]
    if job["error"] is None:
        config = {"configurable": {"thread_id": st.session_state["thread_id"]}}
        question_id = turns(agent.get_state(config).values["messages"])[-1]["question"].id
        note = f"{job['model']} · {count(len(job['steps']), 'step')} · {job['seconds']:.0f} s"
        st.session_state["turn_notes"][question_id] = note
        st.rerun()  # draw the finished turn from memory, with its charts and tables
    st.error(error_message(job["error"]))
    new_conversation()  # a half-finished turn would confuse the next question
    st.caption("The next question starts a fresh conversation.")


def sidebar() -> str:
    """The model picker and a fresh start. Changing the model starts a new conversation."""
    names = list(MODELS)
    model_name = st.sidebar.selectbox("Model", names, index=names.index(DEFAULT_MODEL), key="model",
                                      on_change=new_conversation,
                                      help="Changing the model starts a new conversation.")
    st.sidebar.button("New conversation", icon=":material/add_comment:", on_click=new_conversation, width="stretch")
    st.sidebar.divider()
    st.sidebar.caption("The agent reads the same tables as the dashboard. The model picks a tool and writes "
                       "read-only SQL. Every number, table and chart comes from the data and our code, not from "
                       "the model. Open **How I got this** under an answer to see each step.")
    return model_name


def agent_card(with_steps: bool) -> None:
    """The agent's card at the top of the page: who it is and, on the opening screen, how it works."""
    head = (f'<div class="agent-head"><div class="agent-mark">{SPARKLE}</div><div>'
            f'<div class="agent-title">Planning agent<span class="agent-pill">AI agent</span></div>'
            f'<div class="agent-role">{ROLE}</div></div></div>')
    steps = ""
    if with_steps:
        items = "".join(f'<div class="agent-step"><b>{number}</b><span>{step}</span></div>'
                        for number, step in enumerate(STEPS, start=1))
        steps = f'<div class="agent-steps">{items}</div><div class="agent-promises">{PROMISES}</div>'
    st.html(f'{STYLE}<div class="agent-card">{head}{steps}</div>')


def welcome() -> str | None:
    """The opening screen: the agent's card, and example questions to click."""
    agent_card(with_steps=True)
    st.markdown("**Try one of these**")
    picked = None
    with st.container(key="examples"):
        columns = st.columns(2)
        for number, example in enumerate(EXAMPLES):
            # wrap, or a long question is cut off with "..." on a phone
            if columns[number % 2].button(example, key=f"example_{number}", width="stretch", wrap=True):
                picked = example
    return picked


load_secrets()
if not os.environ.get("ASU_CREATEAI_API_KEY"):  # a fresh clone, or Streamlit Cloud before its secrets are set
    agent_card(with_steps=True)
    st.info("The planning agent needs an ASU CreateAI API key: `ASU_CREATEAI_API_KEY` in `.env`, or in the app's "
            "secrets on Streamlit Cloud. The dashboard pages in the sidebar work without it.")
    st.stop()
if "thread_id" not in st.session_state:
    new_conversation()
model_name = sidebar()
agent = agent_for(model_name)
history = agent.get_state({"configurable": {"thread_id": st.session_state["thread_id"]}}).values.get("messages", [])

job = st.session_state.get("job")  # a question still running, maybe from before the visitor left the page

question = None
if history or job:
    agent_card(with_steps=False)
else:
    question = welcome()
past = turns(history)
if job:
    past = past[:job["turns_before"]]  # the running question is drawn live below, not from its half-saved state
for turn in past:
    show_turn(turn)
typed = st.chat_input("Ask the planning agent, for example: which GDC parts need an order?", disabled=job is not None)
question = typed or question
if job:
    watch_job(job)
    finish_job(agent, job)
elif question:
    start_job(agent, question, model_name, len(past))
    st.rerun()  # the next run watches the job, with the input box locked until it is done
