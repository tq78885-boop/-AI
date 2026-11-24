import ast
import math
import operator
from contextvars import ContextVar
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_tavily import TavilySearch

from database import search_memory, save_memory
from rag import retrieve_from_rag

load_dotenv(Path(__file__).resolve().parent / ".env")

_CURRENT_THREAD_ID: ContextVar[str] = ContextVar("current_thread_id", default="default")


def set_current_thread_id(thread_id: str) -> None:
    """Set a fallback thread id for the current request context."""
    _CURRENT_THREAD_ID.set(str(thread_id))


def _resolve_thread_id(config: RunnableConfig | None = None) -> str:
    """Prefer the thread_id carried by LangGraph's RunnableConfig."""
    if isinstance(config, dict):
        configurable = config.get("configurable") or {}
        if isinstance(configurable, dict):
            thread_id = configurable.get("thread_id")
            if thread_id:
                return str(thread_id)
    return _CURRENT_THREAD_ID.get()


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_MATH_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
    "pow": math.pow,
}
_CONSTANTS = {"pi": math.pi, "e": math.e}
_MAX_EXPRESSION_LENGTH = 300
_MAX_POWER = 1000
_MAX_RESULT = 1e15


def _check_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("only numbers are allowed")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("result is not finite")
    if abs(value) > _MAX_RESULT:
        raise ValueError("result is too large")
    return value


def _eval_math_node(node: ast.AST):
    if isinstance(node, ast.Expression):
        return _eval_math_node(node.body)

    if isinstance(node, ast.Constant):
        return _check_number(node.value)

    if isinstance(node, ast.BinOp):
        op = _BINARY_OPERATORS.get(type(node.op))
        if op is None:
            raise ValueError("unsupported operator")
        left = _eval_math_node(node.left)
        right = _eval_math_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POWER:
            raise ValueError("exponent is too large")
        return _check_number(op(left, right))

    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPERATORS.get(type(node.op))
        if op is None:
            raise ValueError("unsupported unary operator")
        return _check_number(op(_eval_math_node(node.operand)))

    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise ValueError(f"unknown name: {node.id}")

    if isinstance(node, ast.Attribute):
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == "math"
            and node.attr in _CONSTANTS
        ):
            return _CONSTANTS[node.attr]
        raise ValueError("unsupported attribute")

    if isinstance(node, ast.Call):
        if node.keywords:
            raise ValueError("keyword arguments are not supported")
        func = None
        if isinstance(node.func, ast.Name):
            func = _MATH_FUNCTIONS.get(node.func.id)
        elif (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "math"
        ):
            func = _MATH_FUNCTIONS.get(node.func.attr)
        if func is None:
            raise ValueError("unsupported function")
        args = [_eval_math_node(arg) for arg in node.args]
        return _check_number(func(*args))

    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval_math_node(item) for item in node.elts]

    raise ValueError("unsupported expression")


@tool
def calculator(expression: str) -> str:
    """计算数学表达式。支持 + - * / // % **、括号、abs/round/min/max/sum 及 math 常用函数。

    输入必须是纯数学表达式，例如：2 + 2、math.sqrt(16)、sum([1, 2, 3])。
    """
    expression = (expression or "").strip()
    if not expression:
        return "Calculation error: empty expression"
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        return "Calculation error: expression is too long"
    try:
        tree = ast.parse(expression, mode="eval")
        return str(_eval_math_node(tree))
    except Exception as exc:
        return f"Calculation error: {exc}"


web_search = TavilySearch(
    max_results=5,
    topic="general",
    search_depth="advanced",
)


@tool
def search_uploaded_documents(query: str, config: RunnableConfig) -> str:
    """检索当前对话已上传文档中的相关信息。

    当用户询问已上传的 PDF、DOCX、TXT、Markdown、Python 或 CSV 文件时使用本工具。
    """
    return retrieve_from_rag(query=query, thread_id=_resolve_thread_id(config))


@tool
def remember_this(memory: str, config: RunnableConfig) -> str:
    """将重要的用户偏好或事实保存到当前对话的长期记忆中。"""
    return save_memory(thread_id=_resolve_thread_id(config), memory=memory)


@tool
def recall_memory(query: str, config: RunnableConfig) -> str:
    """检索当前对话已保存的长期记忆，用于回答关于用户偏好或历史事实的问题。"""
    return search_memory(thread_id=_resolve_thread_id(config), query=query)


tools = [
    calculator,
    search_uploaded_documents,
    remember_this,
    recall_memory,
    web_search,
]
