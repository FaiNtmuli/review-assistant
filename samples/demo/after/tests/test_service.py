from app.service import average, build_report


def test_average():
    result = average([1, 2, 3])
    assert result is not None


def test_build_report():
    assert build_report([], 10) == {}
