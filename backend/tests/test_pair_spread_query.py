from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

import app.services.pair_spread_query as pair_spread_query_module
from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.models.market import MarketType
from app.models.pair_spread import (
    PairSpreadCurrentLeg,
    PairSpreadFundingRecordRequest,
    PairSpreadFundingPoint,
    PairSpreadKlinePoint,
    PairSpreadLegQuery,
    PairSpreadPriceField,
)
from app.services.pair_spread_query import (
    PairSpreadQueryError,
    PairSpreadQueryService,
    _REALTIME_PAIR_FUNDING_CACHE,
    _REALTIME_PAIR_SPREAD_CACHE,
    _REALTIME_SYMBOL_SPREAD_CACHE,
    _hyperliquid_history_limit_warning,
    build_pair_spread_points,
    build_symbol_spread_points,
)
from app.services.pair_spread_funding_recorder import PairSpreadFundingRecorder, PairSpreadFundingRepository


def kline(minutes: int, close: float) -> PairSpreadKlinePoint:
    return PairSpreadKlinePoint(
        bucket_at=datetime(2026, 7, 10, 12, minutes, tzinfo=UTC),
        close=close,
    )


def kline_at(bucket_at: datetime, close: float) -> PairSpreadKlinePoint:
    return PairSpreadKlinePoint(bucket_at=bucket_at, close=close)


def current_leg(
    exchange: str,
    symbol: str,
    price: float,
    market_type: MarketType = MarketType.FUTURE,
) -> PairSpreadCurrentLeg:
    return PairSpreadCurrentLeg(
        exchange=exchange,
        symbol=symbol,
        market_type=market_type,
        raw_symbol=symbol,
        price=price,
        price_field=PairSpreadPriceField.MARK_PRICE,
        mark_price=price,
        funding_rate_pct=0.01,
        funding_next_time=datetime(2026, 7, 10, 16, 0, tzinfo=UTC),
        timestamp=datetime(2026, 7, 10, 12, 2, tzinfo=UTC),
    )


def test_pair_spread_points_align_by_minute() -> None:
    points = build_pair_spread_points(
        [kline(0, 100), kline(1, 101), kline(2, 102)],
        [kline(1, 103), kline(2, 105), kline(3, 106)],
    )

    assert [point.bucket_at.minute for point in points] == [1, 2]
    assert points[0].spread_abs == 2
    assert points[0].spread_pct == pytest.approx((103 - 101) / ((101 + 103) / 2) * 100)
    assert points[1].spread_abs == 3


def test_pair_spread_points_apply_right_side_multiplier() -> None:
    points = build_pair_spread_points(
        [kline(0, 100)],
        [kline(0, 1050)],
        leg2_multiplier=10,
    )

    assert points[0].leg2_close == 105
    assert points[0].spread_abs == 5
    assert points[0].spread_pct == pytest.approx(5 / ((100 + 105) / 2) * 100)


def test_symbol_spread_points_align_against_base_exchange() -> None:
    points = build_symbol_spread_points(
        [kline(0, 100), kline(1, 101), kline(2, 102)],
        [kline(1, 102), kline(2, 105), kline(3, 108)],
    )

    assert [point.bucket_at.minute for point in points] == [1, 2]
    assert points[0].base_close == 101
    assert points[0].exchange_close == 102
    assert points[0].spread_abs == 1
    assert points[0].spread_pct == pytest.approx(1 / ((101 + 102) / 2) * 100)
    assert points[1].spread_abs == 3


def test_hyperliquid_history_limit_warning_recommends_15_minutes_for_30_days() -> None:
    warning = _hyperliquid_history_limit_warning(
        {"hyperliquid"},
        hours=720,
        interval_minutes=5,
    )

    assert warning is not None
    assert "最近5000根K线" in warning
    assert "需要约8640根" in warning
    assert "最多约17.4天" in warning
    assert "切换到15分钟可覆盖30天" in warning


def test_hyperliquid_history_limit_warning_is_not_needed_for_15_minutes() -> None:
    assert (
        _hyperliquid_history_limit_warning(
            {"hyperliquid"},
            hours=720,
            interval_minutes=15,
        )
        is None
    )


@pytest.mark.asyncio
async def test_pair_spread_query_builds_stats_current_and_funding() -> None:
    _REALTIME_PAIR_FUNDING_CACHE.clear()

    class FakePairSpreadService(PairSpreadQueryService):
        async def _fetch_klines(self, exchange: str, symbol: str, start, end, interval_minutes: int):
            assert interval_minutes == 5
            first_bucket = start + timedelta(minutes=5)
            if exchange == "binance":
                return [
                    kline_at(first_bucket, 100),
                    kline_at(first_bucket + timedelta(minutes=5), 101),
                    kline_at(first_bucket + timedelta(minutes=10), 102),
                ]
            return [
                kline_at(first_bucket, 1010),
                kline_at(first_bucket + timedelta(minutes=5), 1030),
                kline_at(first_bucket + timedelta(minutes=10), 1050),
            ]

        async def _fetch_current_leg(self, exchange: str, symbol: str):
            return current_leg(exchange, symbol, 100 if exchange == "binance" else 1040)

        async def _fetch_funding_history(self, exchange: str, symbol: str, start, end):
            return [
                PairSpreadFundingPoint(
                    exchange=exchange,
                    symbol=symbol,
                    funding_time=datetime(2026, 7, 10, 8, 0, tzinfo=UTC),
                    funding_rate_pct=0.01 if exchange == "binance" else 0.02,
                )
            ]

    service = FakePairSpreadService()
    try:
        result = await service.query(
            PairSpreadLegQuery(exchange="binance", symbol="btc"),
            PairSpreadLegQuery(exchange="okx", symbol="BTC-USDT-SWAP"),
            hours=24,
            interval_minutes=5,
            leg2_multiplier=10,
            now=datetime(2026, 7, 10, 12, 2, 30, tzinfo=UTC),
        )
    finally:
        await service.aclose()

    assert result.leg1.symbol == "BTCUSDT"
    assert result.leg2.symbol == "BTCUSDT"
    assert result.point_count == 3
    assert result.spread_abs.current == 3
    assert result.spread_pct.current == pytest.approx((105 - 102) / ((102 + 105) / 2) * 100)
    assert result.current is not None
    assert result.current.leg2.price == 104
    assert result.current.spread_pct == pytest.approx((104 - 100) / ((100 + 104) / 2) * 100)
    assert result.interval_minutes == 5
    assert result.leg2_multiplier == 10
    assert len(result.funding_history) == 2
    assert len(result.realtime_funding) == 1
    assert result.realtime_funding[0].net_rate_pct == pytest.approx(0)
    assert result.warnings == []
    _REALTIME_PAIR_FUNDING_CACHE.clear()


@pytest.mark.asyncio
async def test_pair_spread_funding_history_fetches_only_funding_points() -> None:
    start = datetime(2026, 7, 10, 0, 0, tzinfo=UTC)
    end = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)

    class FakePairSpreadService(PairSpreadQueryService):
        def __init__(self) -> None:
            super().__init__()
            self.kline_calls = 0
            self.current_calls = 0
            self.funding_windows: list[tuple[str, datetime, datetime]] = []

        async def _fetch_klines(self, exchange: str, symbol: str, start, end, interval_minutes: int):
            self.kline_calls += 1
            return []

        async def _fetch_current_leg(self, exchange: str, symbol: str):
            self.current_calls += 1
            return current_leg(exchange, symbol, 100)

        async def _fetch_funding_history(self, exchange: str, symbol: str, start, end):
            self.funding_windows.append((exchange, start, end))
            inside_time = start + timedelta(hours=8)
            return [
                PairSpreadFundingPoint(
                    exchange=exchange,
                    symbol=symbol,
                    funding_time=inside_time,
                    funding_rate_pct=0.01 if exchange == "binance" else 0.03,
                ),
                PairSpreadFundingPoint(
                    exchange=exchange,
                    symbol=symbol,
                    funding_time=end + timedelta(hours=8),
                    funding_rate_pct=0.99,
                ),
            ]

    service = FakePairSpreadService()
    try:
        result = await service.query_funding_history(
            PairSpreadLegQuery(exchange="binance", symbol="btc"),
            PairSpreadLegQuery(exchange="okx", symbol="btc"),
            start=start,
            end=end,
        )
    finally:
        await service.aclose()

    assert result.start_at == start
    assert result.end_at == end
    assert len(result.funding_history) == 2
    assert {point.exchange for point in result.funding_history} == {"binance", "okx"}
    assert all(start <= point.funding_time <= end for point in result.funding_history)
    assert service.kline_calls == 0
    assert service.current_calls == 0
    assert service.funding_windows == [("binance", start, end), ("okx", start, end)]


@pytest.mark.asyncio
async def test_symbol_spread_query_falls_back_to_available_base_exchange() -> None:
    now = datetime(2026, 7, 10, 12, 2, 30, tzinfo=UTC)

    class FakePairSpreadService(PairSpreadQueryService):
        async def _fetch_klines(self, exchange: str, symbol: str, start, end, interval_minutes: int):
            first_bucket = start + timedelta(minutes=interval_minutes)
            if exchange == "binance":
                return []
            if exchange == "okx":
                return [
                    kline_at(first_bucket, 100),
                    kline_at(first_bucket + timedelta(minutes=interval_minutes), 101),
                ]
            return [
                kline_at(first_bucket, 102),
                kline_at(first_bucket + timedelta(minutes=interval_minutes), 104),
            ]

        async def _fetch_current_leg(self, exchange: str, symbol: str):
            price = {"binance": 99, "okx": 101, "bybit": 104}[exchange]
            return current_leg(exchange, symbol, price)

    service = FakePairSpreadService()
    try:
        result = await service.query_symbol_spreads(
            "btc",
            base_exchange="binance",
            exchanges=["binance", "okx", "bybit"],
            hours=1,
            interval_seconds=60,
            now=now,
        )
    finally:
        await service.aclose()

    assert result.symbol == "BTCUSDT"
    assert result.base_exchange == "okx"
    assert result.exchanges == ["okx", "bybit"]
    assert result.series[0].exchange == "bybit"
    assert result.series[0].point_count == 2
    assert result.series[0].spread_abs.current == 3
    assert result.series[0].current is not None
    assert result.series[0].current.spread_abs == 3
    assert "已改用 okx 做基准" in "；".join(result.warnings)


@pytest.mark.asyncio
async def test_pair_spread_query_historical_compare_skips_current_and_funding() -> None:
    class FakePairSpreadService(PairSpreadQueryService):
        def __init__(self) -> None:
            super().__init__()
            self.kline_windows: list[tuple[str, datetime, datetime, int]] = []
            self.current_calls = 0
            self.funding_calls = 0

        async def _fetch_klines(self, exchange: str, symbol: str, start, end, interval_minutes: int):
            self.kline_windows.append((exchange, start, end, interval_minutes))
            close = 100 if exchange == "binance" else 101
            return [kline_at(end - timedelta(minutes=interval_minutes), close)]

        async def _fetch_current_leg(self, exchange: str, symbol: str):
            self.current_calls += 1
            return current_leg(exchange, symbol, 100)

        async def _fetch_funding_history(self, exchange: str, symbol: str, start, end):
            self.funding_calls += 1
            return []

    service = FakePairSpreadService()
    historical_end = datetime(2026, 7, 9, 12, 2, 45, tzinfo=UTC)
    try:
        result = await service.query(
            PairSpreadLegQuery(exchange="binance", symbol="btc"),
            PairSpreadLegQuery(exchange="okx", symbol="btc"),
            hours=6,
            interval_seconds=60,
            now=historical_end,
            include_current=False,
        )
    finally:
        await service.aclose()

    floored_end = datetime(2026, 7, 9, 12, 2, tzinfo=UTC)
    assert result.observed_at == historical_end
    assert result.first_seen_at == floored_end - timedelta(minutes=1)
    assert result.last_seen_at == floored_end - timedelta(minutes=1)
    assert result.current is None
    assert result.funding_history == []
    assert result.realtime_funding == []
    assert service.current_calls == 0
    assert service.funding_calls == 0
    assert sorted((exchange, end, interval_minutes) for exchange, _, end, interval_minutes in service.kline_windows) == [
        ("binance", floored_end, 1),
        ("okx", floored_end, 1),
    ]


@pytest.mark.asyncio
async def test_pair_spread_query_accumulates_realtime_second_points() -> None:
    _REALTIME_PAIR_SPREAD_CACHE.clear()
    _REALTIME_PAIR_FUNDING_CACHE.clear()

    class FakePairSpreadService(PairSpreadQueryService):
        async def _fetch_current_leg(
            self,
            exchange: str,
            symbol: str,
            market_type: MarketType = MarketType.FUTURE,
        ):
            price = 100 if exchange == "binance" else self.right_price
            leg = current_leg(exchange, symbol, price, market_type)
            rate = 0.01 if exchange == "binance" else self.right_funding
            return leg.model_copy(update={"funding_rate_pct": rate})

        async def _fetch_funding_history(self, exchange: str, symbol: str, start, end):
            return []

    service = FakePairSpreadService()
    service.right_price = 101
    service.right_funding = 0.02
    try:
        first = await service.query(
            PairSpreadLegQuery(exchange="binance", symbol="btc"),
            PairSpreadLegQuery(exchange="okx", symbol="btc"),
            hours=1,
            interval_seconds=5,
            now=datetime(2026, 7, 10, 12, 0, 3, tzinfo=UTC),
        )
        service.right_price = 102
        service.right_funding = 0.03
        updated_same_bucket = await service.query(
            PairSpreadLegQuery(exchange="binance", symbol="btc"),
            PairSpreadLegQuery(exchange="okx", symbol="btc"),
            hours=1,
            interval_seconds=5,
            now=datetime(2026, 7, 10, 12, 0, 4, tzinfo=UTC),
        )
        service.right_price = 103
        service.right_funding = 0.04
        next_bucket = await service.query(
            PairSpreadLegQuery(exchange="binance", symbol="btc"),
            PairSpreadLegQuery(exchange="okx", symbol="btc"),
            hours=1,
            interval_seconds=5,
            now=datetime(2026, 7, 10, 12, 0, 8, tzinfo=UTC),
        )
    finally:
        await service.aclose()
        _REALTIME_PAIR_SPREAD_CACHE.clear()
        _REALTIME_PAIR_FUNDING_CACHE.clear()

    assert first.interval_seconds == 5
    assert first.interval_minutes == 1
    assert first.points[0].bucket_at == datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    assert first.point_count == 1
    assert "实时采样" in first.warnings[0]
    assert updated_same_bucket.point_count == 1
    assert updated_same_bucket.spread_abs.current == 2
    assert updated_same_bucket.realtime_funding[0].net_rate_pct == pytest.approx(0.02)
    assert next_bucket.point_count == 2
    assert [point.bucket_at.second for point in next_bucket.points] == [0, 5]
    assert next_bucket.spread_abs.current == 3
    assert [point.bucket_at.second for point in next_bucket.realtime_funding] == [0, 5]
    assert next_bucket.realtime_funding[-1].net_rate_pct == pytest.approx(0.03)


@pytest.mark.asyncio
async def test_symbol_spread_query_accumulates_realtime_second_points() -> None:
    _REALTIME_SYMBOL_SPREAD_CACHE.clear()

    class FakePairSpreadService(PairSpreadQueryService):
        async def _fetch_current_leg(
            self,
            exchange: str,
            symbol: str,
            market_type: MarketType = MarketType.FUTURE,
        ):
            price = 100 if exchange == "binance" else self.okx_price
            return current_leg(exchange, symbol, price, market_type)

    service = FakePairSpreadService()
    service.okx_price = 101
    try:
        first = await service.query_symbol_spreads(
            "btc",
            base_exchange="binance",
            exchanges=["binance", "okx"],
            hours=1,
            interval_seconds=5,
            now=datetime(2026, 7, 10, 12, 0, 3, tzinfo=UTC),
        )
        service.okx_price = 102
        updated_same_bucket = await service.query_symbol_spreads(
            "btc",
            base_exchange="binance",
            exchanges=["binance", "okx"],
            hours=1,
            interval_seconds=5,
            now=datetime(2026, 7, 10, 12, 0, 4, tzinfo=UTC),
        )
        service.okx_price = 103
        next_bucket = await service.query_symbol_spreads(
            "btc",
            base_exchange="binance",
            exchanges=["binance", "okx"],
            hours=1,
            interval_seconds=5,
            now=datetime(2026, 7, 10, 12, 0, 8, tzinfo=UTC),
        )
    finally:
        await service.aclose()
        _REALTIME_SYMBOL_SPREAD_CACHE.clear()

    assert first.interval_seconds == 5
    assert first.point_count == 1
    assert first.series[0].points[0].bucket_at == datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    assert "实时采样" in first.warnings[0]
    assert updated_same_bucket.point_count == 1
    assert updated_same_bucket.series[0].spread_abs.current == 2
    assert next_bucket.point_count == 2
    assert [point.bucket_at.second for point in next_bucket.series[0].points] == [0, 5]
    assert next_bucket.series[0].spread_abs.current == 3


@pytest.mark.asyncio
async def test_pair_spread_funding_recorder_persists_minute_samples() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    right_funding = {"value": 0.02}

    class FakePairSpreadService(PairSpreadQueryService):
        async def _fetch_current_leg(
            self,
            exchange: str,
            symbol: str,
            market_type: MarketType = MarketType.FUTURE,
        ):
            leg = current_leg(exchange, symbol, 100 if exchange == "binance" else 101, market_type)
            rate = 0.01 if exchange == "binance" else right_funding["value"]
            return leg.model_copy(update={"funding_rate_pct": rate})

    repo = PairSpreadFundingRepository(db)
    recorder = PairSpreadFundingRecorder(repo, service_factory=FakePairSpreadService)
    request = PairSpreadFundingRecordRequest(
        leg1=PairSpreadLegQuery(exchange="binance", symbol="btc"),
        leg2=PairSpreadLegQuery(exchange="okx", symbol="btc"),
        leg2_multiplier=1,
    )

    try:
        first = await recorder.upsert_watch(
            request,
            hours=1,
            now=datetime(2026, 7, 10, 12, 0, 30, tzinfo=UTC),
        )
        right_funding["value"] = 0.04
        await recorder.collect_once(now=datetime(2026, 7, 10, 12, 1, 30, tzinfo=UTC))
        second = await recorder.status_for(
            request,
            hours=1,
            now=datetime(2026, 7, 10, 12, 2, tzinfo=UTC),
        )
        stopped = await recorder.delete_watch(
            request,
            hours=1,
            now=datetime(2026, 7, 10, 12, 2, tzinfo=UTC),
        )
    finally:
        await db.close()

    assert first.watched is True
    assert len(first.samples) == 1
    assert first.samples[0].bucket_at == datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    assert first.samples[0].net_rate_pct == pytest.approx(0.01)
    assert second.watched is True
    assert [sample.bucket_at.minute for sample in second.samples] == [0, 1]
    assert [sample.net_rate_pct for sample in second.samples] == pytest.approx([0.01, 0.03])
    assert second.item is not None
    assert second.item.sample_count == 2
    assert stopped.watched is False


@pytest.mark.asyncio
async def test_pair_spread_query_keeps_spot_and_future_legs_separate() -> None:
    now = datetime(2026, 7, 10, 12, 2, 30, tzinfo=UTC)

    class FakePairSpreadService(PairSpreadQueryService):
        def __init__(self) -> None:
            super().__init__()
            self.kline_market_types: list[MarketType] = []
            self.funding_markets: list[tuple[str, str]] = []

        async def _fetch_klines(
            self,
            exchange: str,
            symbol: str,
            start,
            end,
            interval_minutes: int,
            market_type: MarketType = MarketType.FUTURE,
        ):
            self.kline_market_types.append(market_type)
            first_bucket = start + timedelta(minutes=interval_minutes)
            close = 101 if market_type == MarketType.FUTURE else 100
            return [kline_at(first_bucket, close)]

        async def _fetch_current_leg(
            self,
            exchange: str,
            symbol: str,
            market_type: MarketType = MarketType.FUTURE,
        ):
            return current_leg(exchange, symbol, 101 if market_type == MarketType.FUTURE else 100, market_type)

        async def _fetch_funding_history(self, exchange: str, symbol: str, start, end):
            self.funding_markets.append((exchange, symbol))
            return []

    service = FakePairSpreadService()
    try:
        result = await service.query(
            PairSpreadLegQuery(exchange="bybit", symbol="dexe", market_type=MarketType.FUTURE),
            PairSpreadLegQuery(exchange="bybit", symbol="dexe", market_type=MarketType.SPOT),
            hours=1,
            interval_minutes=1,
            now=now,
        )
    finally:
        await service.aclose()

    assert result.leg1.market_type == MarketType.FUTURE
    assert result.leg2.market_type == MarketType.SPOT
    assert result.point_count == 1
    assert result.current is not None
    assert result.current.leg1.market_type == MarketType.FUTURE
    assert result.current.leg2.market_type == MarketType.SPOT
    assert sorted(service.kline_market_types) == [MarketType.FUTURE, MarketType.SPOT]
    assert service.funding_markets == [("bybit", "DEXEUSDT")]


@pytest.mark.asyncio
async def test_pair_spread_query_falls_back_to_available_window() -> None:
    now = datetime(2026, 7, 10, 12, 30, tzinfo=UTC)
    point_time = now - timedelta(hours=2)

    class FakePairSpreadService(PairSpreadQueryService):
        def __init__(self) -> None:
            super().__init__()
            self.window_hours: list[int] = []

        async def _fetch_klines(self, exchange: str, symbol: str, start, end, interval_minutes: int):
            self.window_hours.append(round((end - start).total_seconds() / 3600))
            if start < end - timedelta(hours=168):
                return []
            close = 100 if exchange == "binance" else 103
            return [kline_at(point_time, close)]

        async def _fetch_current_leg(self, exchange: str, symbol: str):
            return current_leg(exchange, symbol, 100 if exchange == "binance" else 103)

        async def _fetch_funding_history(self, exchange: str, symbol: str, start, end):
            return []

    service = FakePairSpreadService()
    try:
        result = await service.query(
            PairSpreadLegQuery(exchange="binance", symbol="btc"),
            PairSpreadLegQuery(exchange="okx", symbol="btc"),
            hours=720,
            interval_minutes=5,
            now=now,
        )
    finally:
        await service.aclose()

    assert 720 in service.window_hours
    assert 168 in service.window_hours
    assert result.hours == 720
    assert result.point_count == 1
    assert result.first_seen_at == point_time
    assert any("自动改查最近7天" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_pair_spread_query_dedupes_repeated_kline_failures() -> None:
    class FakePairSpreadService(PairSpreadQueryService):
        async def _fetch_klines(self, exchange: str, symbol: str, start, end, interval_minutes: int):
            raise RuntimeError("Hyperliquid 接口返回 HTTP 500，可能是该合约未上线、名称不匹配，或接口临时异常")

    service = FakePairSpreadService()
    try:
        with pytest.raises(PairSpreadQueryError) as exc_info:
            await service.query(
                PairSpreadLegQuery(exchange="hyperliquid", symbol="skhy"),
                PairSpreadLegQuery(exchange="hyperliquid", symbol="skhynix"),
                hours=24,
                interval_minutes=1,
                now=datetime(2026, 7, 10, 12, 30, tzinfo=UTC),
            )
    finally:
        await service.aclose()

    message = str(exc_info.value)
    assert message.count("hyperliquid:合约:SKHYUSDT 分钟K线失败") == 1
    assert message.count("hyperliquid:合约:SKHYNIXUSDT 分钟K线失败") == 1
    assert "developer.mozilla.org" not in message


@pytest.mark.asyncio
async def test_hyperliquid_klines_resolve_prefixed_hip3_coin() -> None:
    start = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    end = start + timedelta(minutes=2)
    bodies: list[dict[str, Any]] = []

    service = PairSpreadQueryService()

    async def fake_post_json(url: str, body: dict[str, Any]):
        bodies.append(body)
        if body.get("type") == "perpDexs":
            return [None, {"name": "xyz"}]
        if body.get("type") == "metaAndAssetCtxs":
            if body.get("dex") == "xyz":
                return [
                    {"universe": [{"name": "xyz:SKHY"}]},
                    [{"markPx": "10"}],
                ]
            return [
                {"universe": [{"name": "BTC"}]},
                [{"markPx": "60000"}],
            ]
        if body.get("type") == "candleSnapshot":
            assert body["req"]["coin"] == "xyz:SKHY"
            return [{"t": int(start.timestamp() * 1000), "c": "10"}]
        raise AssertionError(f"unexpected body: {body}")

    service._post_json = fake_post_json  # type: ignore[method-assign]
    try:
        points = await service._fetch_hyperliquid_klines("SKHYUSDT", start, end, 1)
    finally:
        await service.aclose()

    assert points == [PairSpreadKlinePoint(bucket_at=start, close=10)]
    assert [body.get("type") for body in bodies] == [
        "perpDexs",
        "metaAndAssetCtxs",
        "metaAndAssetCtxs",
        "candleSnapshot",
    ]


@pytest.mark.asyncio
async def test_bitget_klines_continue_after_empty_early_chunk() -> None:
    start = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
    end = start + timedelta(minutes=1001)
    first_data_bucket = start + timedelta(minutes=1000)
    requested_starts: list[int] = []

    service = PairSpreadQueryService()

    async def fake_get_json(url: str):
        query = parse_qs(urlparse(url).query)
        start_ms = int(query["startTime"][0])
        requested_starts.append(start_ms)
        if len(requested_starts) == 1:
            return {"data": []}
        return {"data": [[int(first_data_bucket.timestamp() * 1000), "0", "0", "0", "10"]]}

    service._get_json = fake_get_json  # type: ignore[method-assign]
    try:
        points = await service._fetch_bitget_klines("BTCUSDT", start, end, 1)
    finally:
        await service.aclose()

    assert len(requested_starts) == 2
    assert points == [PairSpreadKlinePoint(bucket_at=first_data_bucket, close=10)]


@pytest.mark.asyncio
async def test_bitget_spot_klines_use_spot_endpoint_and_granularity() -> None:
    start = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
    end = start + timedelta(minutes=1)
    requested_urls: list[str] = []
    service = PairSpreadQueryService()

    async def fake_get_json(url: str):
        requested_urls.append(url)
        query = parse_qs(urlparse(url).query)
        assert "/api/v2/spot/market/candles" in url
        assert query["symbol"] == ["DEXEUSDT"]
        assert query["granularity"] == ["1min"]
        return {"data": [[int(start.timestamp() * 1000), "0", "0", "0", "10"]]}

    service._get_json = fake_get_json  # type: ignore[method-assign]
    try:
        points = await service._fetch_bitget_spot_klines("DEXEUSDT", start, end, 1)
    finally:
        await service.aclose()

    assert requested_urls
    assert points == [PairSpreadKlinePoint(bucket_at=start, close=10)]


@pytest.mark.asyncio
async def test_bybit_spot_current_uses_spot_ticker_without_funding() -> None:
    service = PairSpreadQueryService()
    requested_urls: list[str] = []

    async def fake_get_json(url: str):
        requested_urls.append(url)
        return {
            "result": {
                "list": [
                    {
                        "symbol": "DEXEUSDT",
                        "bid1Price": "10",
                        "ask1Price": "10.2",
                        "lastPrice": "10.1",
                    }
                ]
            }
        }

    service._get_json = fake_get_json  # type: ignore[method-assign]
    try:
        leg = await service._fetch_bybit_spot_current("DEXEUSDT")
    finally:
        await service.aclose()

    assert requested_urls == ["https://api.bybit.com/v5/market/tickers?category=spot&symbol=DEXEUSDT"]
    assert leg.market_type == MarketType.SPOT
    assert leg.price == pytest.approx(10.1)
    assert leg.price_field == PairSpreadPriceField.MID_PRICE
    assert leg.funding_rate_pct is None
    assert leg.funding_next_time is None


@pytest.mark.asyncio
async def test_bybit_current_uses_instruments_info_for_funding_interval_and_limits() -> None:
    service = PairSpreadQueryService()
    requested_urls: list[str] = []

    async def fake_get_json(url: str):
        requested_urls.append(url)
        if "market/tickers" in url:
            return {
                "result": {
                    "list": [
                        {
                            "symbol": "HOMEUSDT",
                            "markPrice": "0.009126",
                            "indexPrice": "0.009291",
                            "bid1Price": "0.00910",
                            "ask1Price": "0.009115",
                            "lastPrice": "0.00912",
                            "fundingRate": "-0.019844",
                            "nextFundingTime": "1784256000000",
                        }
                    ]
                }
            }
        if "market/instruments-info" in url:
            return {
                "result": {
                    "list": [
                        {
                            "symbol": "HOMEUSDT",
                            "fundingInterval": "240",
                            "upperFundingRate": "0.020000",
                            "lowerFundingRate": "-0.020000",
                        }
                    ]
                }
            }
        raise AssertionError(f"unexpected url: {url}")

    service._get_json = fake_get_json  # type: ignore[method-assign]
    try:
        leg = await service._fetch_bybit_current("HOMEUSDT")
    finally:
        await service.aclose()

    assert any("market/tickers" in url for url in requested_urls)
    assert any("market/instruments-info" in url for url in requested_urls)
    assert leg.mid_price == pytest.approx((0.00910 + 0.009115) / 2)
    assert leg.funding_rate_pct == pytest.approx(-1.9844)
    assert leg.funding_interval_hours == pytest.approx(4)
    assert leg.funding_rate_upper_pct == pytest.approx(2)
    assert leg.funding_rate_lower_pct == pytest.approx(-2)


@pytest.mark.asyncio
async def test_binance_like_current_uses_funding_info_for_interval_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 17, 10, 40, tzinfo=UTC)
    monkeypatch.setattr(pair_spread_query_module, "utc_now", lambda: now)
    service = PairSpreadQueryService()
    requested_urls: list[str] = []

    async def fake_get_json(url: str):
        requested_urls.append(url)
        if "premiumIndex" in url:
            return {
                "symbol": "HOMEUSDT",
                "markPrice": "0.009126",
                "indexPrice": "0.009291",
                "lastFundingRate": "-0.019844",
            }
        if "ticker/bookTicker" in url:
            return {
                "symbol": "HOMEUSDT",
                "bidPrice": "0.00910",
                "askPrice": "0.009115",
            }
        if "fundingInfo" in url:
            return [
                {
                    "symbol": "HOMEUSDT",
                    "fundingIntervalHours": "4",
                    "adjustedFundingRateCap": "0.020000",
                    "adjustedFundingRateFloor": "-0.020000",
                }
            ]
        raise AssertionError(f"unexpected url: {url}")

    service._get_json = fake_get_json  # type: ignore[method-assign]
    try:
        leg = await service._fetch_binance_like_current("https://fapi.binance.com", "binance", "HOMEUSDT")
    finally:
        await service.aclose()

    assert any("fapi/v1/fundingInfo" in url for url in requested_urls)
    assert leg.mid_price == pytest.approx((0.00910 + 0.009115) / 2)
    assert leg.funding_rate_pct == pytest.approx(-1.9844)
    assert leg.funding_next_time == datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    assert leg.funding_interval_hours == pytest.approx(4)
    assert leg.funding_rate_upper_pct == pytest.approx(2)
    assert leg.funding_rate_lower_pct == pytest.approx(-2)


@pytest.mark.asyncio
async def test_okx_current_uses_funding_interval_and_limits() -> None:
    funding_time = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
    next_funding_time = funding_time + timedelta(hours=4)
    service = PairSpreadQueryService()
    requested_urls: list[str] = []

    async def fake_get_json(url: str):
        requested_urls.append(url)
        if "market/ticker" in url:
            return {
                "data": [
                    {
                        "bidPx": "100.4",
                        "askPx": "100.6",
                        "last": "100.5",
                    }
                ]
            }
        if "funding-rate" in url:
            return {
                "data": [
                    {
                        "fundingRate": "-0.010000",
                        "nextFundingRate": "-0.005000",
                        "fundingTime": str(int(funding_time.timestamp() * 1000)),
                        "nextFundingTime": str(int(next_funding_time.timestamp() * 1000)),
                        "minFundingRate": "-0.010000",
                        "maxFundingRate": "0.010000",
                    }
                ]
            }
        raise AssertionError(f"unexpected url: {url}")

    service._get_json = fake_get_json  # type: ignore[method-assign]
    try:
        leg = await service._fetch_okx_current("OUSDT")
    finally:
        await service.aclose()

    assert any("market/ticker?instId=O-USDT-SWAP" in url for url in requested_urls)
    assert any("funding-rate?instId=O-USDT-SWAP" in url for url in requested_urls)
    assert leg.raw_symbol == "O-USDT-SWAP"
    assert leg.price == pytest.approx(100.5)
    assert leg.funding_rate_pct == pytest.approx(-1.0)
    assert leg.funding_next_rate_pct == pytest.approx(-0.5)
    assert leg.funding_next_time == next_funding_time
    assert leg.funding_interval_hours == pytest.approx(4)
    assert leg.funding_rate_lower_pct == pytest.approx(-1.0)
    assert leg.funding_rate_upper_pct == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_gate_current_uses_contract_interval_and_limit() -> None:
    next_funding_time = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    service = PairSpreadQueryService()
    requested_urls: list[str] = []

    async def fake_get_json(url: str):
        requested_urls.append(url)
        if "futures/usdt/tickers" in url:
            return [
                {
                    "contract": "MIRA_USDT",
                    "mark_price": "0.04726",
                    "index_price": "0.04761",
                    "highest_bid": "0.04725",
                    "lowest_ask": "0.04727",
                    "last": "0.04726",
                    "funding_rate": "-0.003588",
                    "funding_rate_indicative": "-0.002500",
                }
            ]
        if "futures/usdt/contracts/MIRA_USDT" in url:
            return {
                "name": "MIRA_USDT",
                "funding_interval": 14400,
                "funding_next_apply": int(next_funding_time.timestamp()),
                "funding_rate_limit": "0.020000",
            }
        raise AssertionError(f"unexpected url: {url}")

    service._get_json = fake_get_json  # type: ignore[method-assign]
    try:
        leg = await service._fetch_gate_current("MIRAUSDT")
    finally:
        await service.aclose()

    assert any("futures/usdt/contracts/MIRA_USDT" in url for url in requested_urls)
    assert leg.raw_symbol == "MIRA_USDT"
    assert leg.mid_price == pytest.approx((0.04725 + 0.04727) / 2)
    assert leg.funding_rate_pct == pytest.approx(-0.3588)
    assert leg.funding_next_rate_pct == pytest.approx(-0.25)
    assert leg.funding_next_time == next_funding_time
    assert leg.funding_interval_hours == pytest.approx(4)
    assert leg.funding_rate_upper_pct == pytest.approx(2)
    assert leg.funding_rate_lower_pct == pytest.approx(-2)


@pytest.mark.asyncio
async def test_hyperliquid_current_sets_hourly_interval_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 17, 10, 40, tzinfo=UTC)
    monkeypatch.setattr(pair_spread_query_module, "utc_now", lambda: now)

    class FakePairSpreadService(PairSpreadQueryService):
        async def _resolve_hyperliquid_coin(self, symbol: str):
            assert symbol == "BTCUSDT"
            return "BTC", ""

        async def _fetch_hyperliquid_meta_contexts(self, dex: str = ""):
            assert dex == ""
            return (
                {"universe": [{"name": "BTC"}]},
                [{"markPx": "101", "oraclePx": "100", "midPx": "100.5", "funding": "-0.002500"}],
            )

    service = FakePairSpreadService()
    try:
        leg = await service._fetch_hyperliquid_current("BTCUSDT")
    finally:
        await service.aclose()

    assert leg.raw_symbol == "BTC"
    assert leg.price == pytest.approx(101)
    assert leg.funding_rate_pct == pytest.approx(-0.25)
    assert leg.funding_next_time == datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
    assert leg.funding_interval_hours == pytest.approx(1)
    assert leg.funding_rate_upper_pct == pytest.approx(4)
    assert leg.funding_rate_lower_pct == pytest.approx(-4)


def test_pair_spread_rejects_htx() -> None:
    with pytest.raises(ValidationError):
        PairSpreadLegQuery(exchange="htx", symbol="BTCUSDT")


def test_pair_spread_symbol_normalization() -> None:
    assert PairSpreadLegQuery(exchange="okx", symbol="btc-usdt-swap").symbol == "BTCUSDT"
    assert PairSpreadLegQuery(exchange="gate", symbol="eth").symbol == "ETHUSDT"
    assert PairSpreadLegQuery(exchange="binance", symbol="btc").market_type == MarketType.FUTURE
    assert PairSpreadLegQuery(exchange="binance", symbol="btc", market_type="spot").market_type == MarketType.SPOT
    assert (
        PairSpreadLegQuery(
            exchange="binance_alpha",
            symbol="ALPHA_331USDT",
            market_type=MarketType.SPOT,
        ).symbol
        == "ALPHA_331USDT"
    )
    assert (
        PairSpreadLegQuery(
            exchange="binance_alpha",
            symbol="331",
            market_type=MarketType.SPOT,
        ).symbol
        == "ALPHA_331USDT"
    )


def test_pair_spread_rejects_binance_alpha_future_leg() -> None:
    with pytest.raises(ValidationError):
        PairSpreadLegQuery(exchange="binance_alpha", symbol="ALPHA_331USDT")


@pytest.mark.asyncio
async def test_binance_alpha_spot_klines_unwrap_bapi_payload() -> None:
    start = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)
    end = start + timedelta(minutes=1)
    requested_urls: list[str] = []
    service = PairSpreadQueryService()

    async def fake_get_json(url: str):
        requested_urls.append(url)
        query = parse_qs(urlparse(url).query)
        assert "alpha-trade/klines" in url
        assert query["symbol"] == ["ALPHA_331USDT"]
        assert query["interval"] == ["1m"]
        return {
            "success": True,
            "data": [
                [
                    str(int(start.timestamp() * 1000)),
                    "0.0026",
                    "0.0027",
                    "0.0025",
                    "0.00265",
                    "1000",
                    str(int((start + timedelta(minutes=1)).timestamp() * 1000) - 1),
                ]
            ],
        }

    service._get_json = fake_get_json  # type: ignore[method-assign]
    try:
        points = await service._fetch_binance_alpha_spot_klines("ALPHA_331USDT", start, end, 1)
    finally:
        await service.aclose()

    assert requested_urls
    assert points == [PairSpreadKlinePoint(bucket_at=start, close=0.00265)]


@pytest.mark.asyncio
async def test_binance_alpha_spot_klines_stop_when_start_exceeds_latest_alpha_end() -> None:
    start = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)
    end = start + timedelta(minutes=3)
    service = PairSpreadQueryService()
    requested_urls: list[str] = []

    async def fake_get_json(url: str):
        requested_urls.append(url)
        if len(requested_urls) == 1:
            return {
                "success": True,
                "data": [
                    [
                        str(int(start.timestamp() * 1000)),
                        "0.0026",
                        "0.0027",
                        "0.0025",
                        "0.00265",
                        "1000",
                        str(int((start + timedelta(minutes=1)).timestamp() * 1000) - 1),
                    ]
                ],
            }
        return {
            "success": False,
            "code": "-1023",
            "message": "Start time is greater than end time.",
            "messageDetail": None,
            "data": None,
        }

    service._get_json = fake_get_json  # type: ignore[method-assign]
    try:
        points = await service._fetch_binance_alpha_spot_klines("ALPHA_331USDT", start, end, 1)
    finally:
        await service.aclose()

    assert len(requested_urls) == 2
    assert points == [PairSpreadKlinePoint(bucket_at=start, close=0.00265)]


@pytest.mark.asyncio
async def test_binance_alpha_spot_current_uses_alpha_ticker() -> None:
    service = PairSpreadQueryService()
    requested_urls: list[str] = []

    async def fake_get_json(url: str):
        requested_urls.append(url)
        assert "alpha-trade/ticker" in url
        assert parse_qs(urlparse(url).query)["symbol"] == ["ALPHA_331USDT"]
        return {
            "success": True,
            "data": {
                "symbol": "ALPHA_331USDT",
                "lastPrice": "0.00270610",
            },
        }

    service._get_json = fake_get_json  # type: ignore[method-assign]
    try:
        leg = await service._fetch_binance_alpha_spot_current("331")
    finally:
        await service.aclose()

    assert requested_urls
    assert leg.exchange == "binance_alpha"
    assert leg.symbol == "ALPHA_331USDT"
    assert leg.raw_symbol == "ALPHA_331USDT"
    assert leg.market_type == MarketType.SPOT
    assert leg.price == pytest.approx(0.00270610)
    assert leg.price_field == PairSpreadPriceField.LAST_PRICE
    assert leg.funding_rate_pct is None
