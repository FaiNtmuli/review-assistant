"""全流程的数据结构。

数据从 diff 文本一路走到报告，中间经过四种形态：
FileChange（一个文件的改动）→ ReviewComment（一条审查意见）
→ CommentVerdict（对一条意见的校验结论）→ Report（给人看的报告）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

Severity = Literal["high", "medium", "low"]
ChangeType = Literal["added", "modified", "deleted", "renamed"]


class Hunk(BaseModel):
    """diff 里的一个 @@ 片段。"""

    old_start: int = 0
    old_count: int = 0
    new_start: int = 0
    new_count: int = 0
    lines: list[str] = Field(default_factory=list)


class FileChange(BaseModel):
    """一个文件的改动。

    added_lines 是校验环节的唯一依据：键是新文件里的行号，值是这一行去掉前缀后的内容。
    """

    path: str
    old_path: str | None = None
    change_type: ChangeType = "modified"
    lang: str = "other"
    kind: str = "other"
    is_test: bool = False
    is_binary: bool = False
    hunks: list[Hunk] = Field(default_factory=list)
    added_lines: dict[int, str] = Field(default_factory=dict)
    deleted_lines: dict[int, str] = Field(default_factory=dict)

    def render_numbered(self, max_lines: int = 400) -> str:
        """把改动渲染成带行号的文本，行号就是给模型引用用的那个行号。"""
        out: list[str] = []
        shown = 0
        for hunk in self.hunks:
            out.append(f"@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@")
            new_no, old_no = hunk.new_start, hunk.old_start
            for raw in hunk.lines:
                if raw.startswith("+"):
                    out.append(f"{new_no:>5} + {raw[1:]}")
                    new_no += 1
                elif raw.startswith("-"):
                    out.append(f"      - {raw[1:]}")
                    old_no += 1
                else:
                    content = raw[1:] if raw.startswith(" ") else raw
                    out.append(f"{new_no:>5}   {content}")
                    old_no += 1
                    new_no += 1
                shown += 1
                if shown >= max_lines:
                    out.append("（改动过长，已截断）")
                    return "\n".join(out)
        return "\n".join(out)

    def added_line_numbers(self) -> list[int]:
        return sorted(self.added_lines)


class ReviewCommentDraft(BaseModel):
    """模型直接产出的意见。文件路径由模型给出，行号必须是上面带行号文本里的行号。"""

    file_path: str = Field(description="被审查的文件路径，必须与给出的路径一致")
    line_start: int = Field(description="问题所在的新文件起始行号")
    line_end: int = Field(description="问题所在的新文件结束行号，单行时与 line_start 相同")
    severity: Severity = Field(description="严重程度")
    category: str = Field(description="问题类别，如 bug / security / resource / test / perf / style")
    title: str = Field(description="一句话概括问题")
    detail: str = Field(description="问题说明与修改建议")
    evidence: str = Field(description="从改动中逐字复制的原文，至少一行，不加行号不加 + 号")


class ReviewComment(BaseModel):
    """落库的意见：模型给的内容加上一个稳定的 id。"""

    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    file_path: str
    line_start: int
    line_end: int
    severity: Severity
    category: str
    title: str
    detail: str
    evidence: str

    @classmethod
    def from_draft(cls, draft: ReviewCommentDraft) -> "ReviewComment":
        return cls(**draft.model_dump())


class FileDraft(BaseModel):
    """某个文件这一轮的审查结果。"""

    file_path: str
    kind: str = "other"
    comments: list[ReviewComment] = Field(default_factory=list)
    attempt: int = 0
    note: str = ""


class CommentVerdict(BaseModel):
    """对一条意见的校验结论。structural_issues 由代码判定，semantic_* 由模型判定。"""

    comment_id: str
    file_path: str
    passed: bool = False
    structural_issues: list[str] = Field(default_factory=list)
    semantic_ok: bool = False
    semantic_reason: str = ""

    def reason_text(self) -> str:
        parts = list(self.structural_issues)
        if self.semantic_reason:
            parts.append(("语义通过：" if self.semantic_ok else "语义不通过：") + self.semantic_reason)
        return "；".join(parts) if parts else "通过"


class SemanticCheck(BaseModel):
    comment_id: str = Field(description="被判断的意见 id，原样回填")
    supported: bool = Field(description="这条意见是否被所引用的代码支持")
    reason: str = Field(description="判断依据，一句话")


class SemanticCheckList(BaseModel):
    checks: list[SemanticCheck] = Field(default_factory=list)


class CommentOut(BaseModel):
    comments: list[ReviewCommentDraft] = Field(default_factory=list)


class CommentReport(BaseModel):
    id: str
    line_start: int
    line_end: int
    severity: str
    category: str
    title: str
    detail: str
    evidence: str
    verified: bool


class FileReport(BaseModel):
    path: str
    kind: str
    lang: str
    change_type: str
    comments: list[CommentReport] = Field(default_factory=list)


class UnresolvedItem(BaseModel):
    file_path: str
    title: str
    line_start: int
    line_end: int
    issues: list[str] = Field(default_factory=list)


class ReportStats(BaseModel):
    files_reviewed: int = 0
    comments_total: int = 0
    comments_verified: int = 0
    by_severity: dict[str, int] = Field(default_factory=dict)
    rounds: int = 0


class Report(BaseModel):
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"))
    model: str = ""
    source: str = ""
    stats: ReportStats = Field(default_factory=ReportStats)
    files: list[FileReport] = Field(default_factory=list)
    unresolved: list[UnresolvedItem] = Field(default_factory=list)

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# 代码审查报告")
        lines.append("")
        lines.append(f"- 来源：{self.source}")
        lines.append(f"- 模型：{self.model}")
        lines.append(f"- 生成时间：{self.generated_at}")
        lines.append(f"- 审查轮次：{self.stats.rounds}")
        lines.append(
            f"- 意见总数：{self.stats.comments_total}，"
            f"通过校验：{self.stats.comments_verified}，"
            f"待人工判断：{self.stats.comments_total - self.stats.comments_verified}"
        )
        severity_order = ["high", "medium", "low"]
        by_sev = "，".join(
            f"{name} {self.stats.by_severity.get(name, 0)}" for name in severity_order
        )
        lines.append(f"- 严重程度分布：{by_sev}")
        lines.append("")

        for file_report in self.files:
            lines.append(f"## {file_report.path}")
            lines.append("")
            lines.append(
                f"类型：{file_report.kind}（{file_report.lang}），改动：{file_report.change_type}，"
                f"意见 {len(file_report.comments)} 条"
            )
            lines.append("")
            if not file_report.comments:
                lines.append("未发现问题。")
                lines.append("")
                continue
            for index, comment in enumerate(file_report.comments, start=1):
                mark = "" if comment.verified else "（未通过校验）"
                lines.append(f"### {index}. [{comment.severity}] {comment.title}{mark}")
                lines.append("")
                lines.append(f"- 位置：`{file_report.path}:{comment.line_start}-{comment.line_end}`")
                lines.append(f"- 类别：{comment.category}")
                lines.append(f"- 依据：`{comment.evidence.strip()}`")
                lines.append("")
                lines.append(comment.detail)
                lines.append("")

        if self.unresolved:
            lines.append("## 未通过校验的意见")
            lines.append("")
            lines.append("这些意见在给定轮次内没能给出可定位的依据，建议人工确认后再采纳。")
            lines.append("")
            for item in self.unresolved:
                lines.append(f"- `{item.file_path}:{item.line_start}-{item.line_end}` {item.title}")
                for issue in item.issues:
                    lines.append(f"  - {issue}")
            lines.append("")
        return "\n".join(lines)
