from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.models.market import MarketSnapshot, MarketType
from app.models.opportunity import Opportunity
from app.services.market_labels import is_bitget_rtoken_spot

US_STOCK_TIMEZONE = ZoneInfo("America/New_York")
US_STOCK_OPEN = time(9, 30)
US_STOCK_REGULAR_CLOSE = time(16, 0)
US_STOCK_EARLY_CLOSE = time(13, 0)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    actual = date(year, month, day)
    if actual.weekday() == 5:
        return actual - timedelta(days=1)
    if actual.weekday() == 6:
        return actual + timedelta(days=1)
    return actual


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    days_until_weekday = (weekday - first.weekday()) % 7
    return first + timedelta(days=days_until_weekday + (occurrence - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    # Gregorian computus for the Good Friday exchange holiday.
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _us_stock_holidays(year: int) -> set[date]:
    holidays = {
        _observed_fixed_holiday(year, 1, 1),
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed_fixed_holiday(year, 7, 4),
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving Day
        _observed_fixed_holiday(year, 12, 25),
    }
    if year >= 2022:
        holidays.add(_observed_fixed_holiday(year, 6, 19))  # Juneteenth
    return holidays


def _holiday_dates_around(local_date: date) -> set[date]:
    dates: set[date] = set()
    for year in (local_date.year - 1, local_date.year, local_date.year + 1):
        dates.update(_us_stock_holidays(year))
    return dates


def _previous_trading_day(day: date, holidays: set[date]) -> date:
    candidate = day - timedelta(days=1)
    while candidate.weekday() >= 5 or candidate in holidays:
        candidate -= timedelta(days=1)
    return candidate


def _early_close_dates(local_date: date, holidays: set[date]) -> set[date]:
    thanksgiving = _nth_weekday(local_date.year, 11, 3, 4)
    independence_observed = _observed_fixed_holiday(local_date.year, 7, 4)
    christmas_observed = _observed_fixed_holiday(local_date.year, 12, 25)
    return {
        thanksgiving + timedelta(days=1),  # Black Friday
        _previous_trading_day(independence_observed, holidays),
        _previous_trading_day(christmas_observed, holidays),
    }


def us_stock_session_close(local_date: date) -> time | None:
    holidays = _holiday_dates_around(local_date)
    if local_date.weekday() >= 5 or local_date in holidays:
        return None
    if local_date in _early_close_dates(local_date, holidays):
        return US_STOCK_EARLY_CLOSE
    return US_STOCK_REGULAR_CLOSE


def is_us_stock_market_open(at: datetime | None = None) -> bool:
    current = _as_utc(at or datetime.now(UTC)).astimezone(US_STOCK_TIMEZONE)
    session_close = us_stock_session_close(current.date())
    if session_close is None:
        return False
    current_time = current.time().replace(tzinfo=None)
    return US_STOCK_OPEN <= current_time < session_close


def is_bitget_rtoken_spot_open(
    exchange: str,
    market_type: MarketType | str,
    raw_symbol: str | None,
    canonical_symbol: str | None,
    at: datetime | None = None,
) -> bool:
    if not is_bitget_rtoken_spot(exchange, market_type, raw_symbol, canonical_symbol):
        return True
    return is_us_stock_market_open(at)


def is_market_snapshot_tradable(
    snapshot: MarketSnapshot,
    at: datetime | None = None,
) -> bool:
    return is_bitget_rtoken_spot_open(
        snapshot.exchange,
        snapshot.market_type,
        snapshot.raw_symbol,
        snapshot.symbol,
        at,
    )


def is_opportunity_tradable(
    opportunity: Opportunity,
    at: datetime | None = None,
) -> bool:
    return is_bitget_rtoken_spot_open(
        opportunity.buy_exchange,
        opportunity.buy_market_type,
        opportunity.buy_raw_symbol,
        opportunity.symbol,
        at,
    ) and is_bitget_rtoken_spot_open(
        opportunity.sell_exchange,
        opportunity.sell_market_type,
        opportunity.sell_raw_symbol,
        opportunity.symbol,
        at,
    )
