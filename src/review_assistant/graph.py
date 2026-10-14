"""把节点组装成一张图。

结构上就三件事：主线顺序、按文件扇出再汇合、两条回到上游的环。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph

from . import schemas
from .nodes import (
    aggregate,
    classify,
    fan_out_files,
    finalize,
    human_gate,
    ingest,
    parse_diff,
    review_file,
    revise,
    route_after_human_gate,
    route_after_verify,
    verify,
)
from .state import ReviewState

# 状态里放的是这些自定义类型，检查点序列化需要显式放行。
CHECKPOINT_TYPES = (
    "Hunk",
    "FileChange",
    "ReviewComment",
    "ReviewCommentDraft",
    "FileDraft",
    "CommentVerdict",
)


def make_serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        allowed_msgpack_modules=[
            (schemas.__name__, name) for name in CHECKPOINT_TYPES
        ]
    )


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> Any:
    builder = StateGraph(ReviewState)

    builder.add_node("ingest", ingest)
    builder.add_node("parse_diff", parse_diff)
    builder.add_node("classify", classify)
    builder.add_node("review_file", review_file)
    builder.add_node("verify", verify)
    builder.add_node("revise", revise)
    builder.add_node("aggregate", aggregate)
    builder.add_node("human_gate", human_gate)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "parse_diff")
    builder.add_edge("parse_diff", "classify")

    # 扇出：每个文件一路，全部跑完才汇合到 verify
    builder.add_conditional_edges("classify", fan_out_files, ["review_file", "aggregate"])
    builder.add_edge("review_file", "verify")

    # 重试环：校验不通过就回到 revise，再由 revise 回到 verify
    builder.add_conditional_edges("verify", route_after_verify, ["revise", "aggregate"])
    builder.add_edge("revise", "verify")

    builder.add_edge("aggregate", "human_gate")

    # 人工确认：通过落盘、打回重做、放弃结束
    builder.add_conditional_edges(
        "human_gate", route_after_human_gate, ["finalize", "revise", END]
    )
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer or make_checkpointer("memory"))


def make_checkpointer(kind: str = "memory", path: str = "checkpoints.db") -> BaseCheckpointSaver:
    if kind == "memory":
        return InMemorySaver(serde=make_serde())
    if kind == "sqlite":
        connection = sqlite3.connect(path, check_same_thread=False)
        from langgraph.checkpoint.sqlite import SqliteSaver

        return SqliteSaver(connection, serde=make_serde())
    raise ValueError(f"不认识的检查点类型：{kind}")
