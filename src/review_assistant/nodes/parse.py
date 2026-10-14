"""解析 diff 并按文件扇出。"""

from __future__ import annotations

from typing import Any

from langgraph.types import Send

from ..diff_parser import parse_unified_diff
from ..state import ReviewState, log


def filter_paths(files: dict[str, Any], patterns: list[str]) -> dict[str, Any]:
    """路径里包含任意一个关键词就保留。不给关键词时不过滤。"""
    if not patterns:
        return files
    return {
        path: change
        for path, change in files.items()
        if any(pattern in path for pattern in patterns)
    }


def parse_diff(state: ReviewState) -> dict[str, Any]:
    changes = parse_unified_diff(state.get("diff_text", ""))
    mapping = {change.path: change for change in changes if change.path}
    added = sum(len(change.added_lines) for change in mapping.values())
    return {
        "files": mapping,
        **log(f"parse_diff：{len(mapping)} 个文件，{added} 行新增"),
    }


def fan_out_files(state: ReviewState) -> str | list[Send]:
    """每个文件一路并发审查。文件之间互不依赖，所以可以直接扇出。"""
    files = state.get("files") or {}
    patterns = state.get("path_filter") or []
    selected = filter_paths(files, patterns)
    if not selected:
        return "aggregate"
    payload_settings = state.get("settings") or {}
    return [
        Send("review_file", {"file": change.model_dump(), "settings": payload_settings})
        for change in selected.values()
        if not change.is_binary
    ] or "aggregate"
