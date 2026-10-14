"""逐条校验意见是否有具体依据。

先用代码判定位置：文件名、行号、引用原文。跑不过的带上失败原因直接退回重写，
不再花模型调用；位置过关的才送去判断语义是否成立。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langchain_openai import ChatOpenAI

from ..config import Settings
from ..llm import build_chat_model, structured_call
from ..prompts import VERIFY_SYSTEM, build_verify_prompt
from ..schemas import (
    CommentVerdict,
    FileChange,
    FileDraft,
    ReviewComment,
    SemanticCheck,
    SemanticCheckList,
)
from ..state import ReviewState, log
from ..verification import split_by_structural_check


def has_failures(state: ReviewState) -> bool:
    verdicts = state.get("verdicts") or {}
    return any(not verdict.passed for group in verdicts.values() for verdict in group)


def semantic_checks(
    llm: ChatOpenAI, change: FileChange, pending: list[ReviewComment]
) -> tuple[dict[str, SemanticCheck], str]:
    """一次调用判断这个文件里所有位置过关的意见。返回 (结论, 错误信息)。"""
    if not pending:
        return {}, ""
    payload = [
        {
            "id": comment.id,
            "line_start": str(comment.line_start),
            "line_end": str(comment.line_end),
            "evidence": comment.evidence,
            "title": comment.title,
            "detail": comment.detail,
        }
        for comment in pending
    ]
    try:
        result = structured_call(
            llm,
            SemanticCheckList,
            VERIFY_SYSTEM,
            build_verify_prompt(change, payload),
        )
    except Exception as exc:  # noqa: BLE001 - 判断失败不该中断整张图
        return {}, f"语义判断调用失败：{exc}"
    return {item.comment_id: item for item in result.checks}, ""


def verify(state: ReviewState) -> dict[str, Any]:
    drafts: dict[str, FileDraft] = state.get("drafts") or {}
    files: dict[str, FileChange] = state.get("files") or {}
    settings = Settings.from_state(state.get("settings"))
    llm = build_chat_model(settings)

    def check_one(path: str) -> tuple[str, list[CommentVerdict]]:
        draft = drafts[path]
        change = files.get(path)
        if change is None or not draft.comments:
            reason = "这个文件不在本次改动里"
            return path, [
                CommentVerdict(
                    comment_id=comment.id,
                    file_path=path,
                    passed=False,
                    structural_issues=[reason],
                    semantic_reason="位置校验未通过，没有做语义判断",
                )
                for comment in draft.comments
            ]

        structural, pending = split_by_structural_check(change, draft.comments)
        semantic, error = semantic_checks(llm, change, pending)

        results: list[CommentVerdict] = []
        for comment in draft.comments:
            issues = structural.get(comment.id)
            if issues:
                verdict = CommentVerdict(
                    comment_id=comment.id,
                    file_path=path,
                    passed=False,
                    structural_issues=issues,
                    semantic_reason="位置校验未通过，没有做语义判断",
                )
            else:
                item = semantic.get(comment.id)
                ok = bool(item and item.supported)
                verdict = CommentVerdict(
                    comment_id=comment.id,
                    file_path=path,
                    passed=ok,
                    semantic_ok=ok,
                    semantic_reason=(
                        item.reason if item else (error or "模型没有返回这一条的判断")
                    ),
                )
            results.append(verdict)
        return path, results

    order = list(drafts)
    verdicts: dict[str, list[CommentVerdict]] = {}
    workers = max(1, settings.max_concurrency)
    if workers == 1 or len(order) <= 1:
        for path in order:
            key, results = check_one(path)
            verdicts[key] = results
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for key, results in pool.map(check_one, order):
                verdicts[key] = results

    checked = sum(len(group) for group in verdicts.values())
    failed = sum(1 for group in verdicts.values() for verdict in group if not verdict.passed)

    round_number = state.get("revision_round", 0)
    return {
        "verdicts": verdicts,
        **log(f"verify：第 {round_number + 1} 轮校验 {checked} 条，未通过 {failed} 条"),
    }


def route_after_verify(state: ReviewState) -> str:
    """还有没通过的、并且没到重试上限，就退回重写；否则进入汇总。"""
    settings = Settings.from_state(state.get("settings"))
    round_number = state.get("revision_round", 0)
    if has_failures(state) and round_number < settings.max_rounds:
        return "revise"
    return "aggregate"
