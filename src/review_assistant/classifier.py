"""判定文件类型。

类型决定用哪一套检查重点：同一个"变量没初始化"的问题在 Python 和 SQL 里
是完全不同的表述，提示词按类型分开写。
"""

from __future__ import annotations

import re

from .schemas import FileChange

_EXT_KIND = {
    ".py": "python",
    ".pyi": "python",
    ".js": "frontend",
    ".jsx": "frontend",
    ".ts": "frontend",
    ".tsx": "frontend",
    ".vue": "frontend",
    ".svelte": "frontend",
    ".css": "frontend",
    ".scss": "frontend",
    ".less": "frontend",
    ".sql": "sql",
    ".yaml": "config",
    ".yml": "config",
    ".json": "config",
    ".toml": "config",
    ".ini": "config",
    ".cfg": "config",
    ".conf": "config",
    ".properties": "config",
    ".md": "docs",
    ".rst": "docs",
    ".txt": "docs",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".rs": "rust",
    ".sh": "shell",
    ".bash": "shell",
}

_TEST_DIRS = {"test", "tests", "spec", "specs", "__tests__", "testing"}
_TEST_FILE_RE = re.compile(r"(^test_.*|.*_test|.*\.test|.*\.spec)$")


def detect_lang(path: str) -> str:
    lowered = path.lower()
    for ext, lang in _EXT_KIND.items():
        if lowered.endswith(ext):
            return lang
    return "other"


def detect_test(path: str) -> bool:
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if any(p.lower() in _TEST_DIRS for p in parts[:-1]):
        return True
    if not parts:
        return False
    stem = parts[-1]
    for ext in _EXT_KIND:
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    return bool(_TEST_FILE_RE.match(stem.lower()))


def classify_path(path: str) -> tuple[str, str, bool]:
    """返回 (kind, lang, is_test)。kind 是提示词的选型键。"""
    lang = detect_lang(path)
    is_test = detect_test(path)
    kind = "test" if is_test else lang
    return kind, lang, is_test


def classify_file(file_change: FileChange) -> FileChange:
    kind, lang, is_test = classify_path(file_change.path)
    return file_change.model_copy(update={"kind": kind, "lang": lang, "is_test": is_test})


def classify_files(files: dict[str, FileChange]) -> dict[str, FileChange]:
    return {path: classify_file(change) for path, change in files.items()}
