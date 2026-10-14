"""只重做没通过校验的意见；人工打回时按反馈整批重做。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..config import Settings
from ..llm import build_chat_model
from ..schemas import CommentVerdict, FileChange, FileDraft
from ..state import ReviewState, log
from .review import generate_comments


def revise(state: ReviewState) -> dict[str, Any]:
    drafts: dict[str, FileDraft] = state.get("drafts") or {}
    verdicts: dict[str, list[CommentVerdict]] = state.get("verdicts") or {}
    files: dict[str, FileChange] = state.get("files") or {}
    feedback = (state.get("human_feedback") or "").strip()
    settings = Settings.from_state(state.get("settings"))
    llm = build_chat_model(settings)
    round_number = state.get("revision_round", 0) + 1

    targets: list[tuple[str, list[str]]] = []
    for path, draft in drafts.items():
        if path not in files:
            continue
        if feedback:
            targets.append((path, [f"人工审查反馈：{feedback}"]))
            continue
        by_id = {verdict.comment_id: verdict for verdict in verdicts.get(path, [])}
        failures = [
            f"{comment.title}（行号 {comment.line_start}-{comment.line_end}）："
            f"{by_id[comment.id].reason_text()}"
            for comment in draft.comments
            if comment.id in by_id and not by_id[comment.id].passed
        ]
        if failures:
            targets.append((path, failures))

    def redo(item: tuple[str, list[str]]) -> tuple[str, FileDraft]:
        path, failures = item
        return path, generate_comments(
            llm,
            files[path],
            settings,
            previous=[comment.model_dump() for comment in drafts[path].comments],
            failures=failures,
            feedback=feedback,
            attempt=round_number,
        )

    updated: dict[str, FileDraft] = {}
    workers = max(1, settings.max_concurrency)
    if workers == 1 or len(targets) <= 1:
        for item in targets:
            key, draft = redo(item)
            updated[key] = draft
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for key, draft in pool.map(redo, targets):
                updated[key] = draft

    mode = "按人工反馈重做" if feedback else "重做未通过的意见"
    return {
        "drafts": updated,
        "revision_round": round_number,
        "human_feedback": "",
        **log(f"revise：第 {round_number} 轮，{mode}，涉及 {len(updated)} 个文件"),
    }
