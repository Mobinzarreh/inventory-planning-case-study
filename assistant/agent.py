"""The planning assistant: a LangChain agent on ASU CreateAI with the four tools and in-memory chat memory.

The agent is a ReAct loop: the model picks a tool, reads what comes back, and repeats until it can answer.
"""
import os
from collections.abc import Iterator

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import Engine

from assistant.prompt import RULES
from assistant.tools import TOOLS, Context
from src.config import ROOT

CREATEAI_URL = "https://api-main.aiml.asu.edu/v1"  # OpenAI-compatible, one key for many providers
# the three that got every answer right and made nothing up in the model test (ASSUMPTIONS.md 5.6)
MODELS = {"Claude Sonnet 5.5": "aws/claude5_5_sonnet", "Claude Opus 5.5": "aws/claude5_5_opus",
          "GPT-5.5": "openai/gpt5_5"}
DEFAULT_MODEL = "Claude Sonnet 5.5"
STEP_LIMIT = 20  # graph steps per question, about 9 tool calls: enough for a careful answer, not for a loop

load_dotenv(ROOT / ".env")  # locally. On Streamlit Cloud the keys come from its secrets instead


def chat_model(model_name: str) -> ChatOpenAI:
    """One of the tested models through the CreateAI gateway, at temperature 0 so answers repeat."""
    # a call normally takes 2 to 25 s, but the gateway now and then stalls one for over a minute: give up at
    # 45 s and retry, rather than keep the planner waiting
    return ChatOpenAI(model=MODELS[model_name], base_url=CREATEAI_URL, api_key=os.environ["ASU_CREATEAI_API_KEY"],
                      temperature=0, timeout=45, max_retries=2)


def build_agent(model_name: str, memory: InMemorySaver):
    """The agent for one model. The memory holds every conversation, one per thread id."""
    return create_agent(model=chat_model(model_name), tools=TOOLS, system_prompt=RULES, context_schema=Context,
                        checkpointer=memory)


def ask(agent, question: str, thread_id: str, engine: Engine) -> Iterator[BaseMessage]:
    """Ask one question in a conversation, and yield each new message as it comes: tool calls, results, the answer."""
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": STEP_LIMIT}
    updates = agent.stream({"messages": [{"role": "user", "content": question}]}, config=config,
                           context=Context(engine=engine), stream_mode="updates")
    for update in updates:
        for step in update.values():
            yield from (step or {}).get("messages", [])


def message_text(message: BaseMessage) -> str:
    """The text of a message. Some models send a list of content blocks instead of one string."""
    if isinstance(message.content, str):
        return message.content
    parts = [block.get("text", "") for block in message.content if isinstance(block, dict)]
    return "".join(parts)
