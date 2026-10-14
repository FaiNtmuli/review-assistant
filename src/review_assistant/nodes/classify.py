"""给每个文件打类型标签。"""

from __future__ import annotations

from typing import Any

from ..classifier import classify_files
from ..state import ReviewState, log


def classify(state: ReviewState) -> dict[str, Any]:
    files = state.get("files") or {}
    updated = classify_files(files)
    summary = "，".join(f"{change.path}→{change.kind}" for change in updated.values())
    return {
        "files": updated,
        **log(f"classify：{summary or '没有文件'}"),
    }
