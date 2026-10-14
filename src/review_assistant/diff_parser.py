"""把 unified diff 文本解析成 FileChange 列表。

这一步是纯函数，不联网，是整个流程里"行号是否真实存在"的唯一事实来源。
"""

from __future__ import annotations

import re

from .schemas import FileChange, Hunk

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _clean_path(raw: str) -> str:
    """把 diff 头里的路径整理成仓库内相对路径。"""
    text = raw.strip()
    if text == "/dev/null":
        return ""
    text = text.split("\t")[0].strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    if text.startswith("a/") or text.startswith("b/"):
        text = text[2:]
    return text


def parse_unified_diff(text: str) -> list[FileChange]:
    """解析 diff 文本。识别不了的头部行会被忽略，只保留能被引用的改动行。"""
    files: list[FileChange] = []
    current: FileChange | None = None
    hunk: Hunk | None = None
    new_no = 0
    old_no = 0
    old_used = 0
    new_used = 0

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        if not current.path and current.old_path:
            current.path = current.old_path
        if current.path:
            files.append(current)
        current = None

    for line in text.splitlines():
        if line.startswith("diff --git "):
            flush()
            current = FileChange(path="")
            hunk = None
            continue
        if line.startswith("--- ") and (current is None or not current.hunks):
            if current is None:
                current = FileChange(path="")
            hunk = None
            current.old_path = _clean_path(line[4:]) or current.old_path
            continue
        if current is None:
            continue

        if line.startswith("new file mode"):
            current.change_type = "added"
            continue
        if line.startswith("deleted file mode"):
            current.change_type = "deleted"
            continue
        if line.startswith("rename from "):
            current.old_path = _clean_path(line[len("rename from "):])
            current.change_type = "renamed"
            continue
        if line.startswith("rename to "):
            current.path = _clean_path(line[len("rename to "):])
            continue
        if line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            current.is_binary = True
            continue
        if line.startswith("+++ ") and hunk is None:
            path = _clean_path(line[4:])
            if path:
                current.path = path
            continue

        match = _HUNK_RE.match(line)
        if match:
            hunk = Hunk(
                old_start=int(match.group(1)),
                old_count=int(match.group(2) or 1),
                new_start=int(match.group(3)),
                new_count=int(match.group(4) or 1),
            )
            current.hunks.append(hunk)
            old_no, new_no = hunk.old_start, hunk.new_start
            old_used = new_used = 0
            continue

        if hunk is None:
            continue
        if old_used >= hunk.old_count and new_used >= hunk.new_count:
            hunk = None
            continue

        if line.startswith("+"):
            hunk.lines.append(line)
            current.added_lines[new_no] = line[1:]
            new_no += 1
            new_used += 1
        elif line.startswith("-"):
            hunk.lines.append(line)
            current.deleted_lines[old_no] = line[1:]
            old_no += 1
            old_used += 1
        elif line.startswith("\\"):
            continue
        else:
            content = line[1:] if line.startswith(" ") else line
            hunk.lines.append(" " + content)
            old_no += 1
            new_no += 1
            old_used += 1
            new_used += 1

    flush()
    return files


def summarize(files: list[FileChange]) -> str:
    """给命令行用的一行摘要。"""
    total_added = sum(len(f.added_lines) for f in files)
    return f"{len(files)} 个文件，{total_added} 行新增"
