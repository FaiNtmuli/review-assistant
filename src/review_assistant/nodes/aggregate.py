"""汇总成结构化报告。纯计算，不调模型。"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..schemas import (
    CommentReport,
    CommentVerdict,
    FileChange,
    FileDraft,
    FileReport,
    Report,
    ReportStats,
    UnresolvedItem,
)
from ..state import ReviewState, log

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def aggregate(state: ReviewState) -> dict[str, Any]:
    drafts: dict[str, FileDraft] = state.get("drafts") or {}
    verdicts: dict[str, list[CommentVerdict]] = state.get("verdicts") or {}
    files: dict[str, FileChange] = state.get("files") or {}
    settings = Settings.from_state(state.get("settings"))

    file_reports: list[FileReport] = []
    unresolved: list[UnresolvedItem] = []
    by_severity: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    total = 0
    verified = 0

    for path in sorted(drafts):
        draft = drafts[path]
        change = files.get(path)
        by_id = {verdict.comment_id: verdict for verdict in verdicts.get(path, [])}
        comments: list[CommentReport] = []

        for comment in draft.comments:
            verdict = by_id.get(comment.id)
            passed = bool(verdict and verdict.passed)
            total += 1
            by_severity[comment.severity] = by_severity.get(comment.severity, 0) + 1
            if passed:
                verified += 1
            else:
                issues = list(verdict.structural_issues) if verdict else ["没有校验结论"]
                if verdict and verdict.semantic_reason:
                    issues.append(verdict.semantic_reason)
                unresolved.append(
                    UnresolvedItem(
                        file_path=path,
                        title=comment.title,
                        line_start=comment.line_start,
                        line_end=comment.line_end,
                        issues=issues,
                    )
                )
            comments.append(
                CommentReport(
                    id=comment.id,
                    line_start=comment.line_start,
                    line_end=comment.line_end,
                    severity=comment.severity,
                    category=comment.category,
                    title=comment.title,
                    detail=comment.detail,
                    evidence=comment.evidence,
                    verified=passed,
                )
            )

        comments.sort(key=lambda item: (SEVERITY_ORDER.get(item.severity, 9), item.line_start))
        file_reports.append(
            FileReport(
                path=path,
                kind=draft.kind,
                lang=change.lang if change else "other",
                change_type=change.change_type if change else "modified",
                comments=comments,
            )
        )

    report = Report(
        model=settings.model,
        source=state.get("source", ""),
        stats=ReportStats(
            files_reviewed=len(file_reports),
            comments_total=total,
            comments_verified=verified,
            by_severity=by_severity,
            rounds=state.get("revision_round", 0),
        ),
        files=file_reports,
        unresolved=unresolved,
    )
    return {
        "report": report.model_dump(),
        **log(f"aggregate：{total} 条意见，通过校验 {verified} 条，待人工判断 {total - verified} 条"),
    }
