from pathlib import Path

import pytest

from review_assistant.diff_parser import parse_unified_diff

DEMO = Path(__file__).resolve().parents[1] / "samples" / "demo.diff"


def demo_files():
    return {change.path: change for change in parse_unified_diff(DEMO.read_text(encoding="utf-8"))}


def test_demo_diff_parses_into_three_files():
    files = demo_files()
    assert set(files) == {"app/service.py", "tests/test_service.py", "config/prod.yaml"}
    assert len(files["app/service.py"].added_lines) == 24
    assert len(files["tests/test_service.py"].added_lines) == 7
    assert len(files["config/prod.yaml"].added_lines) == 4


def test_added_line_content_has_no_prefix():
    service = demo_files()["app/service.py"]
    assert service.added_lines[2] == "import os"
    assert service.added_lines[12] == "def average(values, acc=[]):"
    assert service.added_lines[37] == '    return os.environ["APP_SECRET"]'


@pytest.mark.parametrize("path", ["app/service.py", "tests/test_service.py", "config/prod.yaml"])
def test_hunk_counts_match_the_lines_inside(path):
    for hunk in demo_files()[path].hunks:
        added = sum(1 for line in hunk.lines if line.startswith("+"))
        removed = sum(1 for line in hunk.lines if line.startswith("-"))
        context = sum(1 for line in hunk.lines if line.startswith(" "))
        # hunk 头上的计数把上下文行也算进去了
        assert added + context == hunk.new_count
        assert removed + context == hunk.old_count


def test_new_file_and_deleted_file_are_recognized():
    text = """diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+x = 1
+y = 2
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1,1 +0,0 @@
-old = True
"""
    files = {change.path: change for change in parse_unified_diff(text)}
    assert files["new.py"].change_type == "added"
    assert files["new.py"].added_lines == {1: "x = 1", 2: "y = 2"}
    assert files["gone.py"].change_type == "deleted"
    assert files["gone.py"].added_lines == {}


def test_rename_is_recognized():
    text = """diff --git a/old.py b/new.py
similarity index 100%
rename from old.py
rename to new.py
"""
    files = {change.path: change for change in parse_unified_diff(text)}
    assert files["new.py"].change_type == "renamed"


def test_render_numbered_marks_added_lines():
    rendered = demo_files()["config/prod.yaml"].render_numbered()
    assert "    4 +   debug: true" in rendered
    assert "-   debug: false" in rendered
