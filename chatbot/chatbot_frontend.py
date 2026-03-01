import streamlit as st
from chatbot_backend_sqlite import chatbot , retrieve_all_threads , submit_async_task
from langchain_core.messages import HumanMessage, AIMessage, AIMessageChunk, ToolMessage
import uuid
import queue
import time




# ------------------ utility function ------------------

def get_thread_id():
    thread_id = uuid.uuid4()
    return thread_id

def reset_chat():
    # Only create a new thread if current chat has messages
    if st.session_state["messages_history"]:
        st.session_state["thread_id"] = get_thread_id()
        add_thread(st.session_state["thread_id"])
    st.session_state["messages_history"] = []

def add_thread(thread_id):
    if thread_id not in st.session_state['chat_thread']:
        st.session_state["chat_thread"].append(thread_id)

def load_conversion(thread_id):
    state = chatbot.get_state({"configurable": {"thread_id": str(thread_id)}})
    return state.values.get("messages", [])

def get_thread_preview(thread_id):
    """Get first 35 characters from the thread for display"""
    messages = load_conversion(thread_id)
    if messages:
        # Get first human message content
        for msg in messages:
            if isinstance(msg, HumanMessage):
                content = msg.content.strip()
                if len(content) > 35:
                    return content[:30] + "..."
                return content if content else "Empty"
    return "Current Chat"

# ------------------ Session State ------------------
if "messages_history" not in st.session_state:
    st.session_state["messages_history"] = []

if "thread_id" not in st.session_state:
    st.session_state["thread_id"] = get_thread_id()

if "chat_thread" not in st.session_state:
    st.session_state["chat_thread"] = retrieve_all_threads()

if "should_scroll" not in st.session_state:
    st.session_state["should_scroll"] = False

add_thread(st.session_state["thread_id"])

# ------------------ sidebar ------------------

st.sidebar.title("LangGraph ChatBot")
if st.sidebar.button("New Chat") :
    reset_chat()
st.sidebar.header("My Conversions")


for thread in st.session_state["chat_thread"][::-1] :

    if st.sidebar.button(get_thread_preview(thread), key=str(thread)) :
        st.session_state["thread_id"] = thread
        messages = load_conversion(thread)

        temp_message = []

        for msg in messages :
            if isinstance(msg, HumanMessage) :
                role = "user"
            else :
                role = "assistant"

            temp_message.append({"role" : role , "content" : msg})
        st.session_state["messages_history"] = temp_message


# ------------------ Render History FIRST ------------------
for message in st.session_state["messages_history"]:
    with st.chat_message(message["role"]):
        st.markdown(message["content"].content)

# ------------------ User Input ------------------
user_input = st.chat_input("Type Here")

if user_input:
    # -------- render + store user message --------
    st.session_state["messages_history"].append(
        {"role": "user", "content": HumanMessage(content=user_input)}
    )

    with st.chat_message("user"):
        st.markdown(user_input)

    # -------- stream assistant --------
    CONFIG = {
        "configurable": {"thread_id": str(st.session_state["thread_id"])},
        "metadata" : {"thread_id": str(st.session_state["thread_id"])},
        "run_name" : "ChatBot-Run"
    }

    with st.chat_message("assistant"):
        status_holder = {"box": None}
        thinking_box = st.status("🤔 Thinking...", expanded=False)
        thinking_placeholder = st.empty()  # show actual <think> text
        tool_placeholder = st.empty()      # show current tool name
        full_response = [""]  # use list as mutable buffer within nested generator
        stream_placeholder = st.empty()
        think_buffer = [""]
        in_think = [False]

        def ai_only_stream():
            """Generator that streams AI responses from the queue"""
            event_queue = queue.Queue()

            async def run_stream():
                try:
                    async for message_chunk, metadata in chatbot.astream(
                        {"messages": [HumanMessage(content=user_input)]},
                        config=CONFIG,
                        stream_mode="messages",
                    ):
                        event_queue.put((message_chunk, metadata))
                except Exception as exc:
                    event_queue.put(("error", exc))
                finally:
                    event_queue.put(None)  # Signal completion

            # Start the async task on backend event loop
            submit_async_task(run_stream())

            # Process queue and yield tokens
            while True:
                try:
                    item = event_queue.get(timeout=0.1)  # short timeout to allow UI refresh
                except queue.Empty:
                    # heartbeat to keep Streamlit flushing; no content yielded
                    yield ""
                    continue

                if item is None:
                    break  # Streaming complete

                message_chunk, metadata = item

                if message_chunk == "error":
                    thinking_box.update(label="⚠️ Error", state="error")
                    raise metadata

                # Handle tool messages (status only; show tool name, no content)
                if isinstance(message_chunk, ToolMessage):
                    tool_name = getattr(message_chunk, "name", "tool")
                    tool_placeholder.markdown(f"🔧 Using `{tool_name}`")
                    if status_holder["box"] is None:
                        status_holder["box"] = st.status(
                            f"🔧 Using `{tool_name}` …", expanded=True
                        )
                    else:
                        status_holder["box"].update(
                            label=f"🔧 Using `{tool_name}` …",
                            state="running",
                            expanded=True,
                        )
                    continue  # skip showing tool content

                # Stream only assistant tokens
                if isinstance(message_chunk, (AIMessageChunk, AIMessage)):
                    content = message_chunk.content

                    # Coerce non-string content
                    if not isinstance(content, str):
                        content = str(content)

                    # Extract and surface <think> content separately
                    if "<think>" in content or in_think[0]:
                        start_idx = content.find("<think>")
                        end_idx = content.find("</think>")

                        if in_think[0]:
                            # already inside thinking; look for closing tag
                            if end_idx != -1:
                                think_buffer[0] += content[:end_idx]
                                thinking_placeholder.markdown(f"**Thinking:** {think_buffer[0]}")
                                in_think[0] = False
                                content = content[end_idx + len("</think>") :]
                            else:
                                think_buffer[0] += content
                                thinking_placeholder.markdown(f"**Thinking:** {think_buffer[0]}")
                                content = ""
                        if start_idx != -1:
                            # new think block begins
                            after_start = content[start_idx + len("<think>") :]
                            end_idx = after_start.find("</think>")
                            if end_idx != -1:
                                think_buffer[0] += after_start[:end_idx]
                                thinking_placeholder.markdown(f"**Thinking:** {think_buffer[0]}")
                                content = content[:start_idx] + after_start[end_idx + len("</think>") :]
                            else:
                                in_think[0] = True
                                think_buffer[0] += after_start
                                thinking_placeholder.markdown(f"**Thinking:** {think_buffer[0]}")
                                content = content[:start_idx]

                    # Strip any remaining think tags before streaming to user
                    content = content.replace("<think>", "").replace("</think>", "")

                    if content:
                        if thinking_box is not None:
                            thinking_box.update(label="🧠 Thinking", state="running")

                        # If we get a chunk, stream as-is; if a full message, stream char-by-char
                        if isinstance(message_chunk, AIMessageChunk):
                            full_response[0] += content
                            yield content
                        else:
                            for ch in content:
                                full_response[0] += ch
                                yield ch

        # Use Streamlit's streaming helper so tokens render incrementally
        ai_message = st.write_stream(ai_only_stream()) or full_response[0]

        if thinking_box is not None:
            thinking_box.update(label="✅ Done", state="complete", expanded=False)

        # Finalize tool box if used
        if status_holder["box"] is not None:
            status_holder["box"].update(
                label="✅ Tool finished", state="complete", expanded=False
            )
    # -------- save assistant message to session state --------
    st.session_state["messages_history"].append(
        {"role": "assistant", "content": AIMessage(content=ai_message)}
    )
