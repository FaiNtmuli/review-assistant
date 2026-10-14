"""意见的"有没有依据"校验，纯代码部分。

这一层不联网，答案唯一：文件名在不在本次改动里、行号是不是新增行、
引用的原文能不能在改动原文里逐字找到。跑不过这里的意见不会进入语义判断，
直接带上失败原因回到重写环节。
"""

from __future__ import annotations

import re

from .schemas import FileChange, ReviewComment

# 一条意见最多允许覆盖的连续行数。跨得太大说明位置不具体。
MAX_SPAN_LINES = 30


def normalize(text: str) -> str:
    """压掉空白差异，只比较代码内容本身。"""
    return re.sub(r"\s+", " ", text).strip()


def hunk_line_index(file_change: FileChange) -> set[str]:
    """这个文件改动原文里出现过的所有行（含上下文行和被删行），归一化后放一起。"""
    index: set[str] = set()
    for hunk in file_change.hunks:
        for raw in hunk.lines:
            content = raw[1:] if raw[:1] in {"+", "-", " "} else raw
            key = normalize(content)
            if key:
                index.add(key)
    for content in file_change.added_lines.values():
        key = normalize(content)
        if key:
            index.add(key)
    return index


def added_line_index(file_change: FileChange) -> dict[str, list[int]]:
    """归一化后的新增行内容 → 出现它的行号。"""
    index: dict[str, list[int]] = {}
    for number, content in file_change.added_lines.items():
        key = normalize(content)
        if key:
            index.setdefault(key, []).append(number)
    return index


def _format_numbers(numbers: list[int], limit: int = 12) -> str:
    if not numbers:
        return "无"
    shown = ", ".join(str(n) for n in numbers[:limit])
    if len(numbers) > limit:
        shown += " ..."
    return shown


def check_comment(file_change: FileChange, comment: ReviewComment) -> list[str]:
    """返回失败原因列表。空列表表示位置、行号、引用原文三项都成立。"""
    issues: list[str] = []

    if comment.file_path != file_change.path:
        issues.append(
            f"文件路径不对：意见写的是 {comment.file_path}，这个文件在改动里叫 {file_change.path}"
        )

    if comment.line_start < 1 or comment.line_end < comment.line_start:
        issues.append(f"行号区间不合法：{comment.line_start}-{comment.line_end}")
    elif comment.line_end - comment.line_start > MAX_SPAN_LINES:
        issues.append(
            f"行号区间跨了 {comment.line_end - comment.line_start} 行，位置不具体，"
            f"请收窄到 {MAX_SPAN_LINES} 行以内"
        )

    window = set(range(comment.line_start, comment.line_end + 1))
    added_numbers = set(file_change.added_lines)
    hit = sorted(window & added_numbers)
    if not added_numbers:
        issues.append("这个文件没有新增行，无法引用")
    elif not hit:
        issues.append(
            f"行号 {comment.line_start}-{comment.line_end} 里没有本次新增的行；"
            f"本文件的新增行号是 {_format_numbers(sorted(added_numbers))}"
        )

    evidence_lines = [normalize(line) for line in comment.evidence.splitlines()]
    evidence_lines = [line for line in evidence_lines if line]
    if not evidence_lines:
        issues.append("evidence 是空的，必须逐字引用改动原文")
        return issues

    known = hunk_line_index(file_change)
    missing = [line for line in evidence_lines if line not in known]
    if missing:
        issues.append(
            f"引用的原文在这个文件的改动里找不到（原样复制，不要改写、不要加行号）："
            f"{missing[0][:80]}"
        )
        return issues

    added_index = added_line_index(file_change)
    anchored = False
    for line in evidence_lines:
        for number in added_index.get(line, []):
            if number in window:
                anchored = True
                break
        if anchored:
            break
    if not anchored:
        issues.append(
            "引用的原文和给出的行号对不上：引用内容没有落在 "
            f"{comment.line_start}-{comment.line_end} 这些新增行上"
        )

    return issues


def split_by_structural_check(
    file_change: FileChange, comments: list[ReviewComment]
) -> tuple[dict[str, list[str]], list[ReviewComment]]:
    """把意见分成"位置就不过关"和"位置过关、待语义判断"两组。"""
    failures: dict[str, list[str]] = {}
    pending: list[ReviewComment] = []
    for comment in comments:
        issues = check_comment(file_change, comment)
        if issues:
            failures[comment.id] = issues
        else:
            pending.append(comment)
    return failures, pending
