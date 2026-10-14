"""读入要审查的改动。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..state import ReviewState, log


def git_diff(rev_range: str) -> str:
    command = ["git", "diff", "--no-color", "--unified=3", rev_range]
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git diff 失败：{' '.join(command)}\n{proc.stderr.strip()}")
    return proc.stdout


def ingest(state: ReviewState) -> dict[str, Any]:
    """把这次要审的改动读成一个字符串。"""
    source = state.get("diff_source") or {}
    kind = source.get("type", "git")

    if kind == "git":
        rev_range = source.get("range") or "HEAD~1"
        text = git_diff(rev_range)
        label = f"git {rev_range}"
    elif kind == "patch":
        path = Path(source.get("path", ""))
        if not path.is_file():
            raise FileNotFoundError(f"找不到改动文件：{path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        label = f"patch {path}"
    elif kind == "text":
        text = source.get("text", "")
        label = "内联改动"
    else:
        raise ValueError(f"不认识的改动来源：{kind}")

    if not text.strip():
        raise ValueError(f"{label} 里没有任何改动")

    return {
        "diff_text": text,
        "source": label,
        **log(f"ingest：读入 {label}，{len(text.splitlines())} 行 diff"),
    }
