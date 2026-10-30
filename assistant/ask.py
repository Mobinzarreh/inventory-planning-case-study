"""Ask the planning assistant from the terminal. Run from the repo root:

    .venv\\Scripts\\python.exe -m assistant.ask "How many pallets of no-demand inventory sit at each location?"

Several questions in one run are one conversation, so a follow-up can say "now only PDC1". Pick a model with
--model="GPT-5.5" (Claude Sonnet 5.5 by default). Tables and charts show in full in the app; here you see the steps.
"""
import sys
import time
import uuid

import streamlit.logger
from langgraph.checkpoint.memory import InMemorySaver

streamlit.logger.set_log_level("error")  # the dashboard's cache warns when it runs outside the app, harmlessly

# imported after that line, or the dashboard's loader prints its warnings first
from assistant.agent import DEFAULT_MODEL, ask, build_agent, message_text  # noqa: E402
from assistant.database import build_database  # noqa: E402


def print_step(message) -> int:
    """Print one step of the agent's work and return the tokens it used."""
    if message.type == "tool":
        print(f"     <- {message_text(message).splitlines()[0][:120]}")
    elif message.tool_calls:
        for call in message.tool_calls:
            args = ", ".join(f"{name}={value!r}" for name, value in call["args"].items())
            print(f"  -> {call['name']}({args[:400]})")
    else:
        print(f"\n{message_text(message)}\n")
    usage = getattr(message, "usage_metadata", None) or {}
    return usage.get("total_tokens", 0)


def main(arguments: list[str]) -> None:
    """Ask each question in turn, in one conversation."""
    model_name = DEFAULT_MODEL
    questions = []
    for argument in arguments:
        if argument.startswith("--model="):
            model_name = argument.removeprefix("--model=")
        else:
            questions.append(argument)
    agent = build_agent(model_name, InMemorySaver())
    engine = build_database()
    thread_id = str(uuid.uuid4())
    for question in questions:
        print("=" * 100, f"\n[{model_name}] {question}")
        start, tokens = time.time(), 0
        for message in ask(agent, question, thread_id, engine):
            tokens += print_step(message)
        print(f"  ({time.time() - start:.1f} s, {tokens:,} tokens)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # the Windows console can't print every character in the answers
    main(sys.argv[1:])
