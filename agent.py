import os
import sqlite3
from pathlib import Path

import certifi
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
load_dotenv(BASE_DIR / ".env")

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage 
from langgraph.graph import StateGraph, START, MessagesState
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.sqlite import SqliteSaver
from tools import tools

DATA_DIR.mkdir(exist_ok=True)

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

ALLOWED_MODELS = {
    "gpt-5.4",
    "gpt-5.6",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-4o-mini",
    "chat-latest",
}


SYSTEM_PROMPT = """
You are 砸扣AI (Zakou AI), a helpful agentic assistant.

You can:
1. Answer normal questions.
2. Call tools when they are needed.
3. Search uploaded documents with search_uploaded_documents.
4. Search the web with Tavily for the latest or time-sensitive information.
5. Save important user preferences with remember_this.
6. Recall saved preferences with recall_memory.
7. Calculate math with calculator.

Rules:
- If the user asks about an uploaded document, call search_uploaded_documents before answering.
- If the user asks you to remember something, call remember_this.
- If the user asks about previous preferences or saved facts, call recall_memory.
- If the user asks about latest news or current information, use web search and mention that the answer is based on web search results.
- Use calculator for arithmetic when precision matters.
- Be clear, helpful, and concise. Reply in the user's language.
"""

def normalize_model_name(model_name: str |None) ->str:
    """
    Validate selected model from frontend.
    If model is missing or not allowed, fallback toDEFAULT_MODEL.
    """


    if  not model_name:
        return DEFAULT_MODEL
    model_name = model_name.strip()
    if model_name not in ALLOWED_MODELS:
        return DEFAULT_MODEL
    return model_name



def build_agent(model_name: str):
    selected_model = normalize_model_name(model_name)
    llm = ChatOpenAI(
        model=selected_model,
        temperature=0.3,
        streaming=True,
        timeout=120,
        max_retries=2,
    )
    
    llm_with_tools = llm.bind_tools(tools)

    def chatbot_node(state: MessagesState):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
        
        response = llm_with_tools.invoke(messages)
        return{
            "messages": [response]
        }
    tool_node = ToolNode(tools)

    workflow = StateGraph(MessagesState)

    workflow.add_node("chatbot_node", chatbot_node)
    workflow.add_node("tools", tool_node)

    workflow.add_edge(START, "chatbot_node")
    workflow.add_conditional_edges("chatbot_node", tools_condition)

    workflow.add_edge("tools", "chatbot_node")

    conn = sqlite3.connect(
        str(DATA_DIR / "langgraph_checkpoints.sqlite"),
        check_same_thread=False,
    )

    checkpointer = SqliteSaver(conn)

    return workflow.compile(checkpointer=checkpointer)

_AGENT_CACHE = {}
def get_agent(model_name:str|None = None):
    """
    返回所选模式对应的已缓存LangGraph智能体。
    若尚未创建，则仅创建一次并复用。
    """
    selected_model = normalize_model_name(model_name)
    if selected_model not in _AGENT_CACHE:
        _AGENT_CACHE[selected_model] = build_agent(selected_model)
    return _AGENT_CACHE[selected_model]