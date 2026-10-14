from pathlib import Path

import pytest

from review_assistant.diff_parser import parse_unified_diff
from review_assistant.schemas import ReviewComment, ReviewCommentDraft
from review_assistant.verification import check_comment

DEMO = Path(__file__).resolve().parents[1] / "samples" / "demo.diff"


@pytest.fixture
def service():
    files = {change.path: change for change in parse_unified_diff(DEMO.read_text(encoding="utf-8"))}
    return files["app/service.py"]


def make(service, **overrides) -> ReviewComment:
    payload = {
        "file_path": service.path,
        "line_start": 8,
        "line_end": 9,
        "severity": "medium",
        "category": "resource",
        "title": "文件句柄没有关闭",
        "detail": "open 的结果没有用 with 管理，异常路径下句柄不会被释放。",
        "evidence": "handle = open(path)",
    }
    payload.update(overrides)
    return ReviewComment(**payload)


def test_position_and_evidence_agree(service):
    assert check_comment(service, make(service)) == []


def test_multiline_evidence_is_accepted(service):
    comment = make(
        service,
        line_start=8,
        line_end=9,
        evidence="    handle = open(path)\n    return json.load(handle)",
    )
    assert check_comment(service, comment) == []


def test_wrong_file_path_is_rejected(service):
    issues = check_comment(service, make(service, file_path="app/other.py"))
    assert any("文件路径不对" in issue for issue in issues)


def test_line_outside_added_lines_is_rejected(service):
    issues = check_comment(service, make(service, line_start=5, line_end=5, evidence="MAX_PAGE_SIZE = 200"))
    assert any("没有本次新增的行" in issue for issue in issues)


def test_evidence_not_in_diff_is_rejected(service):
    issues = check_comment(service, make(service, evidence="raise ValueError('boom')"))
    assert any("找不到" in issue for issue in issues)


def test_evidence_must_land_on_the_quoted_lines(service):
    issues = check_comment(
        service, make(service, line_start=8, line_end=8, evidence="return json.load(handle)")
    )
    assert any("和给出的行号对不上" in issue for issue in issues)


def test_surrounding_whitespace_is_tolerated(service):
    assert check_comment(service, make(service, evidence="   handle = open(path)   ")) == []


def test_oversized_span_is_rejected(service):
    issues = check_comment(service, make(service, line_start=2, line_end=37))
    assert any("位置不具体" in issue for issue in issues)


def test_empty_evidence_is_rejected(service):
    issues = check_comment(service, make(service, evidence="   "))
    assert any("evidence 是空的" in issue for issue in issues)


def test_comment_draft_converts_to_comment():
    draft = ReviewCommentDraft(
        file_path="a.py",
        line_start=1,
        line_end=1,
        severity="low",
        category="style",
        title="t",
        detail="d",
        evidence="e",
    )
    comment = ReviewComment.from_draft(draft)
    assert comment.file_path == "a.py"
    assert len(comment.id) == 8
