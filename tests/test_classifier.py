import pytest

from review_assistant.classifier import classify_path


@pytest.mark.parametrize(
    ("path", "kind", "lang", "is_test"),
    [
        ("app/service.py", "python", "python", False),
        ("tests/test_service.py", "test", "python", True),
        ("app/service_test.py", "test", "python", True),
        ("web/Button.tsx", "frontend", "frontend", False),
        ("web/Button.test.tsx", "test", "frontend", True),
        ("db/schema.sql", "sql", "sql", False),
        ("config/prod.yaml", "config", "config", False),
        ("docs/guide.md", "docs", "docs", False),
        ("Makefile", "other", "other", False),
        ("__tests__/helper.js", "test", "frontend", True),
        ("spec/models/user.rb", "test", "other", True),
    ],
)
def test_classify(path, kind, lang, is_test):
    assert classify_path(path) == (kind, lang, is_test)
