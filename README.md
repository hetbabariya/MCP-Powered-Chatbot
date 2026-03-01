# Conversational Chatbot (LangGraph + Streamlit + MCP)

A conversational chatbot built with **LangGraph** and **Streamlit**.

It supports:

- Tool calling via LangChain tool binding (`bind_tools`)
- A **remote MCP server** (FastMCP “expense tracker” server)
- **Chat history / short-term memory** via **SQLite checkpointing** (persisted per `thread_id`)

---

## Project Structure

- `chatbot/chatbot_frontend.py`

  Streamlit UI (chat interface + conversation list).

- `chatbot/chatbot_backend_sqlite.py`

  LangGraph backend:

  - Loads tools (DuckDuckGo, stock price, calculator, and MCP tools)
  - Routes tool calls through a `ToolNode`
  - Persists conversation state using `AsyncSqliteSaver` + `chatbot/chatbot.db`

- `chatbot/chatbot.db`

  SQLite database used by LangGraph checkpointer to store conversation checkpoints.

---

## Requirements

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## Environment Variables

Create a `.env` file in the project root.

Minimum required (based on current code):

```bash
# Model provider (Groq)
GROQ_API_KEY=...

# Remote MCP server auth (FastMCP)
MCP_API_KEY=...

# Stock price tool
ALPHAVANTAGE_API_KEY=...

# Optional (if you use them elsewhere)
OPENAI_API_KEY=...
GOOGLE_API_KEY=...
HUGGINGFACEHUB_ACCESS_TOKEN=...
LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=ChatBot-Project
```

---

## Run the App (Streamlit)

From the project root:

```bash
streamlit run chatbot/chatbot_frontend.py
```

---

## Demo Video

<iframe src="https://drive.google.com/file/d/1xgaL-KEHyvn5Tzmh1WkX8DSVCCnbZyXi/preview" width="640" height="480" allow="autoplay"></iframe>

---

## How It Works

### 1) Conversation Threads + History

- The Streamlit UI creates a new `thread_id` (UUID) per chat.
- The sidebar lists existing threads using `retrieve_all_threads()`.
- Clicking a thread loads stored messages using `chatbot.get_state({"configurable": {"thread_id": ...}})`.

Because LangGraph is compiled with a SQLite checkpointer, your conversations persist across app restarts.

### 2) LangGraph Nodes

The backend graph is compiled in `chatbot_backend_sqlite.py`:

- `chat_node`

  Calls the LLM (Groq) and enables tool calling.

- `tools` (ToolNode)

  Executes tools when the model emits tool calls.

### 3) Tools (Tool Binding)

The LLM is configured with tool binding:

- DuckDuckGo search (`DuckDuckGoSearchRun`)
- Stock price (`get_stock_price` via AlphaVantage)
- Calculator (`calculator`)
- Remote MCP tools (loaded via `MultiServerMCPClient`)

Remote MCP server used (from code):

- `https://efficient-purple-snipe.fastmcp.app/mcp`

If MCP tools fail to load (auth/network), the app continues with the fallback tools.

---

## Workflow Diagram (Mermaid)

```mermaid
flowchart TD
  U[User] -->|types message| S[Streamlit UI<br/>chatbot_frontend.py]

  S -->|invokes astream with thread_id| G[LangGraph App<br/>compiled graph]

  G --> CN[chat_node<br/>LLM call (Groq) + bind_tools]

  CN --> RT{tool_calls present?<br/>route_tools()}

  RT -->|No| END[Return assistant message]
  RT -->|Yes| TN[ToolNode<br/>executes tool]

  TN --> T1[DuckDuckGo Search]
  TN --> T2[Stock Price<br/>AlphaVantage]
  TN --> T3[Calculator]
  TN --> T4[MCP Tools<br/>Expense Tracker<br/>remote FastMCP]

  T4 -->|Authorization: Bearer MCP_API_KEY| MCP[(FastMCP Server)]

  TN -->|ToolMessage(s)| CN

  G --> CP[(SQLite Checkpointer<br/>AsyncSqliteSaver<br/>chatbot.db)]
  CN --> CP
  TN --> CP

  END --> S
  S -->|renders streamed tokens| U
```

---

## Notes / Troubleshooting

- If MCP tools do not load, ensure `MCP_API_KEY` is set and the MCP server URL is reachable.
- If AlphaVantage responses fail, check `ALPHAVANTAGE_API_KEY`.
- Chat history is stored in `chatbot/chatbot.db`. Deleting it will remove saved threads.
