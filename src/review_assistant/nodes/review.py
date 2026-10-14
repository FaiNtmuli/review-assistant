"""对单个文件生成审查意见。"""

from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI

from ..config import Settings
from ..llm import build_chat_model, structured_call
from ..prompts import REVIEW_SYSTEM, build_review_prompt, build_revise_prompt, REVISE_SYSTEM
from ..schemas import CommentOut, FileChange, FileDraft, ReviewComment
from ..state import ReviewState, log


def generate_comments(
    llm: ChatOpenAI,
    file_change: FileChange,
    settings: Settings,
    previous: list[dict[str, str]] | None = None,
    failures: list[str] | None = None,
    feedback: str = "",
    attempt: int = 0,
) -> FileDraft:
    """首次审查和重写共用这一条路径，区别只在提示词里多带了什么。"""
    if previous:
        system = REVISE_SYSTEM
        user = build_revise_prompt(
            file_change,
            previous,
            failures or [],
            feedback,
            settings.max_comments_per_file,
        )
    else:
        system = REVIEW_SYSTEM
        user = build_review_prompt(file_change, settings.max_comments_per_file)

    result = structured_call(llm, CommentOut, system, user)
    comments = [
        ReviewComment.from_draft(draft)
        for draft in result.comments[: settings.max_comments_per_file]
    ]
    return FileDraft(
        file_path=file_change.path,
        kind=file_change.kind,
        comments=comments,
        attempt=attempt,
    )


def review_file(state: dict[str, Any]) -> dict[str, Any]:
    """图上的节点。经 Send 调用，state 里只有这个文件。"""
    file_change = FileChange.model_validate(state["file"])
    settings = Settings.from_state(state.get("settings"))
    llm = build_chat_model(settings)
    draft = generate_comments(llm, file_change, settings, attempt=0)
    return {
        "drafts": {file_change.path: draft},
        **log(f"review_file：{file_change.path} 生成 {len(draft.comments)} 条意见"),
    }
