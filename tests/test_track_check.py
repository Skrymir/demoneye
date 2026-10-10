"""track_check --since takes what journalctl takes for the same window,
instead of crashing on anything but 'YYYY-MM-DD HH:MM'."""
import datetime as dt

import pytest

import track_check as T

NOW = dt.datetime(2026, 10, 10, 15, 30)


@pytest.mark.parametrize("since, want", [
    ("2026-10-06 18:00", dt.datetime(2026, 10, 6, 18, 0)),
    ("2026-10-06 18:00:05", dt.datetime(2026, 10, 6, 18, 0, 5)),
    ("2026-10-06", dt.datetime(2026, 10, 6)),
    ("today", dt.datetime(2026, 10, 10)),
    ("yesterday", dt.datetime(2026, 10, 9)),
    ("-2h", dt.datetime(2026, 10, 10, 13, 30)),
    ("-30m", dt.datetime(2026, 10, 10, 15, 0)),
    ("-1d", dt.datetime(2026, 10, 9, 15, 30)),
])
def test_parse_since(since, want):
    assert T.parse_since(since, NOW) == want


def test_unreadable_since_is_a_clear_error():
    with pytest.raises(ValueError, match="--since"):
        T.parse_since("last tuesday", NOW)
