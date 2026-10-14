from app.service import average


def test_average():
    assert average([1, 2, 3]) == 2
