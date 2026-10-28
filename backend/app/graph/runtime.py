"""进程级 HITL 图与 checkpointer 运行时。

图和 AsyncSqliteSaver 绑定到 FastAPI lifespan 的 event loop，不能在每次请求里
重新创建，也不能被 lru_cache 缓存。测试/eval 不初始化 runtime 时，调用方应退回
无 checkpointer 的 build_graph()。
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

_graph: Any = None
_saver: BaseCheckpointSaver | None = None


def init_runtime(saver: BaseCheckpointSaver, graph: Any) -> None:
    global _graph, _saver
    _graph = graph
    _saver = saver


def get_graph() -> Any:
    if _graph is None:
        raise RuntimeError("HITL graph runtime is not initialized")
    return _graph


def get_checkpointer() -> BaseCheckpointSaver:
    if _saver is None:
        raise RuntimeError("HITL checkpointer runtime is not initialized")
    return _saver


def hitl_active() -> bool:
    return _graph is not None and _saver is not None


def clear_runtime() -> None:
    global _graph, _saver
    _graph = None
    _saver = None
