import pytest

from bot.texts import plural


@pytest.mark.parametrize(
    "count,expected",
    [(1, "запись"), (2, "записи"), (4, "записи"), (5, "записей"),
     (11, "записей"), (12, "записей"), (21, "запись"), (22, "записи"), (25, "записей")],
)
def test_plural(count, expected):
    assert plural(count, "запись", "записи", "записей") == expected
