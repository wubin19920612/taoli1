from datetime import UTC, date, datetime, timedelta

import pytest
from reference_market_sessions import is_us_stock_market_open as reference_open
from reference_market_sessions import us_stock_session_close as reference_close

from app.services.market_sessions import is_us_stock_market_open, us_stock_session_close


def test_calendar_cache_is_bounded_and_matches_original_across_years():
    us_stock_session_close.cache_clear()
    start = date(2024, 1, 1)
    for offset in range(366 + 365 + 365):
        day = start + timedelta(days=offset)
        assert us_stock_session_close(day) == reference_close(day)
    assert us_stock_session_close.cache_info().currsize <= 128
    # Revisit evicted dates as well as a cached date.
    for day in [start, start + timedelta(days=1000), start]:
        assert us_stock_session_close(day) == reference_close(day)


@pytest.mark.parametrize("day", [
    date(2026, 3, 6), date(2026, 3, 9),  # Both sides of DST start.
    date(2026, 10, 30), date(2026, 11, 2),  # Both sides of DST end.
    date(2026, 7, 3), date(2026, 7, 4),  # Observed holiday / weekend.
    date(2026, 11, 27), date(2026, 12, 24),  # Early closes.
])
def test_cached_calendar_never_freezes_intraday_market_state(day):
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    # Includes exact open/close minutes, in reverse order too, so a previous
    # open/closed result cannot leak across a boundary or a historical query.
    times = [start + timedelta(minutes=minute) for minute in range(24 * 60)]
    for at in times + list(reversed(times)):
        assert is_us_stock_market_open(at) == reference_open(at)
