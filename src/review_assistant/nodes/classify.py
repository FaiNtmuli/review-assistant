"""决定审查哪些文件，并给每个文件打类型标签。"""

from __future__ import annotations

from typing import Any

from ..classifier import classify_files
from ..schemas import FileChange
from ..state import ReviewState, log


def filter_paths(
    files: dict[str, FileChange], patterns: list[str]
) -> tuple[dict[str, FileChange], dict[str, FileChange]]:
    """按关键词做子串匹配，返回 (要审的, 被排除的)。不给关键词就全要。"""
    if not patterns:
        return files, {}
    kept: dict[str, FileChange] = {}
    dropped: dict[str, FileChange] = {}
    for path, change in files.items():
        if any(pattern in path for pattern in patterns):
            kept[path] = change
        else:
            dropped[path] = change
    return kept, dropped


def classify(state: ReviewState) -> dict[str, Any]:
    files = state.get("files") or {}
    patterns = state.get("path_filter") or []
    selected, dropped = filter_paths(files, patterns)
    updated = classify_files(selected)
    summary = "，".join(f"{change.path}→{change.kind}" for change in updated.values())

    messages = [f"classify：{summary or '没有文件'}"]
    if dropped:
        messages.append(
            f"classify：按 {patterns} 排除了 {len(dropped)} 个文件：{sorted(dropped)}"
        )
    if patterns and not updated:
        messages.append(f"classify：没有文件路径包含 {patterns}，本次不会审查任何文件")

    return {
        "files": updated,
        "history": messages,
    }
