from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.sqlite import SqliteSaver
from langchain_core.prompts import PromptTemplate
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
from langchain_groq import ChatGroq
from typing import TypedDict, Annotated
from langgraph.prebuilt import ToolNode
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.tools import tool , BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import os
from pathlib import Path
from dotenv import load_dotenv
import sqlite3
import requests
import aiosqlite
import requests
import asyncio
import threading
import json
from datetime import datetime

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "chatbot.db"

# FastMCP API Key from environment
MCP_API_KEY = os.getenv("MCP_API_KEY")

# Dedicated async loop for backend tasks
_ASYNC_LOOP = asyncio.new_event_loop()
_ASYNC_THREAD = threading.Thread(target=_ASYNC_LOOP.run_forever, daemon=True)
_ASYNC_THREAD.start()


def _submit_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, _ASYNC_LOOP)


def run_async(coro):
    return _submit_async(coro).result()


def submit_async_task(coro):
    """Schedule a coroutine on the backend event loop."""
    return _submit_async(coro)


os.environ["GROQ_API_KEY"] = os.getenv("GROQ_API_KEY")
ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY")

model = ChatGroq(model="qwen/qwen3-32b")

client = MultiServerMCPClient(
    {
        "expense": {
            "transport": "streamable_http",
            "url": "https://efficient-purple-snipe.fastmcp.app/mcp",
            "headers": {"Authorization": f"Bearer {MCP_API_KEY}"}
        }
    }
)

def load_mcp_tools() -> list[BaseTool]:
    try:

        tools = run_async(client.get_tools())
        return tools
    except Exception as e:
        print(f"⚠️  Could not load MCP tools from FastMCP server")
        print(f"   Error: {type(e).__name__}")
        print(f"   The FastMCP server at https://efficient-purple-snipe.fastmcp.app/mcp may require additional authentication.")
        print(f"   Continuing with fallback tools (Search, Stock Price, Calculator)...\n")
        return []

mcp_tools = load_mcp_tools()
# CONFIG = {"configurable" : {"thread_id": "1"}}
# Tools
search_tool = DuckDuckGoSearchRun(region="us-en")


def _to_json_string(payload):
    """Ensure tool outputs are strings to satisfy Groq tool message schema."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload)
    except Exception:
        return str(payload)


@tool
def calculator(first_num: float, second_num: float, operation: str) -> str:
    """
    Perform a basic arithmetic operation on two numbers.
    Supported operations: add, sub, mul, div
    """
    try:
        if operation == "add":
            result = first_num + second_num
        elif operation == "sub":
            result = first_num - second_num
        elif operation == "mul":
            result = first_num * second_num
        elif operation == "div":
            if second_num == 0:
                return "Division by zero is not allowed"
            result = first_num / second_num
        else:
            return f"Unsupported operation '{operation}'"

        return _to_json_string(
            {
                "first_num": first_num,
                "second_num": second_num,
                "operation": operation,
                "result": result,
            }
        )
    except Exception as e:
        return str(e)


@tool
def get_stock_price(symbol: str) -> str:
    """
    Fetch latest stock price for a given symbol (e.g. 'AAPL', 'TSLA')
    using Alpha Vantage with API key in the URL.
    """
    url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={ALPHAVANTAGE_API_KEY}"
    r = requests.get(url)
    try:
        return _to_json_string(r.json())
    except Exception as e:
        return str(e)


tools = [search_tool, get_stock_price, calculator, *mcp_tools]
model_with_tools = model.bind_tools(tools) if tools else model

class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


import re


def clean_output(text):
    return re.sub(r"<think>[\s\S]*?</think>", "", text).strip()


async def chat_node(state: ChatState):

    def _normalize_tool_msg(msg: BaseMessage) -> BaseMessage:
        if isinstance(msg, ToolMessage):
            content = msg.content
            if isinstance(content, list):
                # join any list parts as strings
                content = " ".join(
                    [c if isinstance(c, str) else json.dumps(c) for c in content if c is not None]
                )
            elif content is None:
                content = "{}"
            elif not isinstance(content, str):
                content = json.dumps(content)
            # Groq requires a non-empty string
            if not content:
                content = "{}"
            return msg.copy(update={"content": content})
        return msg

    messages = [_normalize_tool_msg(m) for m in state["messages"]]

    # System prompt to enable multi-tool usage
    system_prompt = f"""
    You are an intelligent AI assistant with access to multiple tools.

    Today's date: {datetime.now().strftime('%Y-%m-%d')}

    Guidelines for answering:

    1. Always analyze the user's question carefully and break it into sub-tasks if needed.
    2. Identify ALL relevant tools required to fully answer the question.
    3. Always retrieve or search for factual information first before performing calculations or reasoning.
    4. If one tool provides partial information, determine what additional tools are needed and call them.
    5. Execute tool calls in a logical sequence until the answer is complete.
    6. Combine all tool outputs into a clear, structured, and comprehensive final response.
    7. Do not guess when data can be retrieved using tools.
    8. Only stop calling tools when you are confident the answer is complete and accurate."""

    # Insert system message at the beginning if not already there
    messages_with_system = messages
    if not messages or (hasattr(messages[0], "type") and messages[0].type != "system"):
        from langchain_core.messages import SystemMessage

        messages_with_system = [SystemMessage(content=system_prompt)] + messages

    response = await model_with_tools.ainvoke(messages_with_system)

    return {"messages": [response]}


def route_tools(state: ChatState):
    """Route to tools node if last message has tool calls, otherwise end"""
    messages = state.get("messages", [])
    if messages:
        last_message = messages[-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
    return "__end__"



tool_node = ToolNode(tools) if tools else None

async def _init_checkpointer():
    conn = await aiosqlite.connect(database=str(DB_PATH))
    return AsyncSqliteSaver(conn)

saver = run_async(_init_checkpointer())

graph = StateGraph(ChatState)

graph.add_node("chat_node", chat_node)
graph.add_edge(START, "chat_node")

if tool_node:
    graph.add_node("tools", tool_node)
    graph.add_conditional_edges(
        "chat_node", route_tools, {"tools": "tools", "__end__": END}
    )
    graph.add_edge("tools", "chat_node")
else :
    graph.add_edge("chat_node", END)

# conn = sqlite3.connect(
#     "chatbot_conversations.db", check_same_thread=False
# )  # Ensure the database file is created
# saver = SqliteSaver(conn=conn)

chatbot = graph.compile(checkpointer=saver)

async def _alist_threads():
    all_threads = set()
    async for checkpoint in saver.alist(None):
        all_threads.add(checkpoint.config["configurable"]["thread_id"])
    return list(all_threads)


def retrieve_all_threads():
    return run_async(_alist_threads())

# def reterive_all_threads():
#     all_thread = set()
#     for checkpoint in saver.list(None):
#         thread_id = checkpoint[0]["configurable"]["thread_id"]
#         all_thread.add(thread_id)
#     return list(all_thread)


# print(reterive_all_threads())

# result = chatbot.invoke(
#     {"message" : HumanMessage(content="hi")},
#     config=CONFIG,
# )
# print(result)
