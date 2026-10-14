"""图的共享状态。

drafts 和 verdicts 用"按文件路径覆盖"的合并规则。这一条规则同时解决三件事：
多个文件并发写入不会互相冲掉、重跑某个文件时覆盖旧结果、人工打回时整批重算。
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from .schemas import CommentVerdict, FileChange, FileDraft


def merge_by_file(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """同键覆盖，异键并存。"""
    merged: dict[str, Any] = dict(left or {})
    merged.update(right or {})
    return merged


class ReviewState(TypedDict, total=False):
    # 输入
    source: str
    diff_source: dict[str, str]
    output_dir: str
    path_filter: list[str]
    settings: dict[str, Any]

    # 解析与分类
    diff_text: str
    files: dict[str, FileChange]

    # 审查
    drafts: Annotated[dict[str, FileDraft], merge_by_file]
    verdicts: Annotated[dict[str, list[CommentVerdict]], merge_by_file]
    revision_round: int

    # 报告与人工确认
    report: dict[str, Any]
    decision: str
    human_feedback: str
    output_paths: list[str]

    # 过程记录
    history: Annotated[list[str], operator.add]


def log(message: str) -> dict[str, list[str]]:
    """节点返回这个来追加一条过程记录。"""
    return {"history": [message]}
