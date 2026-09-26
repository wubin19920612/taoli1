from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median

from .models import HOUR, HourCandle, PositionSample, WatchFeatures


def _latest_as_of(
    samples: list[PositionSample], market_key: str, target: datetime, decision_at: datetime
) -> PositionSample | None:
    eligible = [
        sample for sample in samples
        if sample.market_key == market_key
        and sample.event_time <= target
        and sample.available_at <= decision_at
    ]
    return max(eligible, key=lambda sample: sample.event_time, default=None)


def _latest_ratio_as_of(
    samples: list[PositionSample], market_key: str, target: datetime, decision_at: datetime
) -> PositionSample | None:
    eligible = [
        sample for sample in samples
        if sample.market_key == market_key
        and sample.account_ratio is not None
        and sample.account_ratio_event_time is not None
        and sample.account_ratio_event_time <= target
        and sample.available_at <= decision_at
    ]
    return max(eligible, key=lambda sample: sample.account_ratio_event_time, default=None)


def calculate_watch_features(
    market_key: str,
    candles: list[HourCandle],
    positioning: list[PositionSample],
    *,
    bucket_at: datetime,
    decision_at: datetime,
    sampling_period: timedelta = HOUR,
) -> WatchFeatures:
    reasons: list[str] = []
    by_hour = {
        row.event_time: row
        for row in candles
        if row.market_key == market_key
        and row.event_time <= bucket_at
        and row.available_at <= decision_at
        and row.event_time <= decision_at
    }
    hours = [bucket_at - offset * HOUR for offset in range(171, -1, -1)]
    if any(hour not in by_hour for hour in hours):
        reasons.append("hourly_candle_gap_or_unavailable")
    if reasons:
        return WatchFeatures(market_key, bucket_at, decision_at, "insufficient_data", tuple(reasons))

    ordered = [by_hour[hour] for hour in hours]
    if any(row.close <= 0 or row.quote_volume < 0 for row in ordered):
        return WatchFeatures(
            market_key, bucket_at, decision_at, "insufficient_data", ("invalid_candle",)
        )
    baseline = median(row.quote_volume for row in ordered[:168])
    if baseline <= 0:
        return WatchFeatures(
            market_key, bucket_at, decision_at, "insufficient_data", ("zero_volume_baseline",)
        )
    current = _latest_as_of(positioning, market_key, bucket_at, decision_at)
    reference_at = bucket_at - 24 * HOUR
    reference = _latest_as_of(positioning, market_key, reference_at, decision_at)
    current_ratio = _latest_ratio_as_of(positioning, market_key, bucket_at, decision_at)
    reference_ratio = _latest_ratio_as_of(positioning, market_key, reference_at, decision_at)
    if current is None or reference is None:
        reasons.append("positioning_endpoint_missing")
    elif (bucket_at - current.event_time > sampling_period or
          reference_at - reference.event_time > sampling_period):
        reasons.append("positioning_endpoint_stale")
    if current is not None and reference is not None:
        if current.raw_unit != reference.raw_unit or current.source != reference.source:
            reasons.append("open_interest_unit_or_source_changed")
        if reference.raw_open_interest <= 0:
            reasons.append("positioning_reference_invalid")
    if current_ratio is None or reference_ratio is None:
        reasons.append("account_ratio_missing")
    elif (
        current_ratio.account_ratio <= 0
        or reference_ratio.account_ratio <= 0
    ):
        reasons.append("account_ratio_invalid")
    elif (
        bucket_at - current_ratio.account_ratio_event_time > sampling_period
        or reference_at - reference_ratio.account_ratio_event_time > sampling_period
    ):
        reasons.append("account_ratio_stale")
    if reasons:
        status = (
            "stale_data"
            if "positioning_endpoint_stale" in reasons or "account_ratio_stale" in reasons
            else "insufficient_data"
        )
        return WatchFeatures(market_key, bucket_at, decision_at, status, tuple(reasons))

    assert current is not None and reference is not None
    assert current_ratio is not None and reference_ratio is not None
    window = [
        row for row in positioning
        if row.market_key == market_key
        and reference.event_time <= row.event_time <= current.event_time
        and row.available_at <= decision_at
        and row.raw_unit == current.raw_unit
        and row.source == current.source
    ]
    observed_hours = {row.event_time.replace(minute=0, second=0, microsecond=0) for row in window}
    first_hour = reference.event_time.replace(minute=0, second=0, microsecond=0)
    last_hour = current.event_time.replace(minute=0, second=0, microsecond=0)
    expected_hours = {
        first_hour + offset * HOUR
        for offset in range(int((last_hour - first_hour) / HOUR) + 1)
    }
    if not expected_hours <= observed_hours:
        return WatchFeatures(
            market_key, bucket_at, decision_at, "insufficient_data", ("open_interest_window_gap",)
        )
    peak = max(row.raw_open_interest for row in window)
    if peak <= 0:
        return WatchFeatures(
            market_key, bucket_at, decision_at, "insufficient_data", ("open_interest_invalid",)
        )
    return WatchFeatures(
        market_key=market_key,
        bucket_at=bucket_at,
        calculated_at=decision_at,
        status="ready",
        return_4h=ordered[-1].close / ordered[-5].close - 1,
        return_24h=ordered[-1].close / ordered[-25].close - 1,
        volume_ratio=sum(row.quote_volume for row in ordered[-4:]) / 4 / baseline,
        account_ratio=current_ratio.account_ratio,
        account_ratio_change=current_ratio.account_ratio / reference_ratio.account_ratio - 1,
        oi_current_growth=current.raw_open_interest / reference.raw_open_interest - 1,
        oi_peak_growth=peak / reference.raw_open_interest - 1,
        oi_drawdown=current.raw_open_interest / peak - 1,
        oi_age_seconds=(bucket_at - current.event_time).total_seconds(),
        account_age_seconds=(bucket_at - current_ratio.account_ratio_event_time).total_seconds(),
    )
