"""解析 diff 并按文件扇出。"""

from __future__ import annotations

from typing import Any

from langgraph.types import Send

from ..diff_parser import parse_unified_diff
from ..state import ReviewState, log


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
    if not files:
        return "aggregate"
    payload_settings = state.get("settings") or {}
    return [
        Send("review_file", {"file": change.model_dump(), "settings": payload_settings})
        for change in files.values()
        if not change.is_binary
    ] or "aggregate"
