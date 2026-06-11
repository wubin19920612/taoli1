from datetime import UTC, datetime

from app.models.market import MarketSnapshot, MarketType
from app.services.tradfi_perp_monitor import build_tradfi_perp_monitor_preview


def _future(
    *,
    exchange: str,
    raw_symbol: str,
    base: str,
    bid: float,
    ask: float,
    funding: float,
    volume: float = 1_000_000,
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=f"{base}USDT",
        base=base,
        exchange=exchange,
        market_type=MarketType.FUTURE,
        bid=bid,
        ask=ask,
        volume_24h_usdt=volume,
        funding_rate_pct=funding,
        funding_interval_hours=8 if exchange == "binance" else 1,
        mark_price=(bid + ask) / 2,
        index_price=(bid + ask) / 2,
        timestamp=datetime(2026, 6, 10, tzinfo=UTC),
        raw_symbol=raw_symbol,
    )


def test_tradfi_monitor_maps_hyperliquid_aliases_to_binance_symbols() -> None:
    markets = [
        _future(
            exchange="hyperliquid",
            raw_symbol="xyz:SKHX",
            base="SKHX",
            bid=1328.0,
            ask=1329.0,
            funding=-0.10,
        ),
        _future(
            exchange="hyperliquid",
            raw_symbol="xyz:SMSN",
            base="SMSN",
            bid=198.0,
            ask=198.2,
            funding=-0.09,
        ),
        _future(
            exchange="binance",
            raw_symbol="SKHYNIXUSDT",
            base="SKHYNIX",
            bid=1327.0,
            ask=1328.0,
            funding=-0.72,
        ),
        _future(
            exchange="binance",
            raw_symbol="SAMSUNGUSDT",
            base="SAMSUNG",
            bid=197.9,
            ask=198.0,
            funding=-0.59,
        ),
    ]

    preview = build_tradfi_perp_monitor_preview(
        markets,
        tradfi_bases={"SKHYNIX", "SAMSUNG"},
        now=datetime(2026, 6, 10, tzinfo=UTC),
    )

    assert preview.matched_count == 2
    by_asset = {row.asset: row for row in preview.rows}
    assert by_asset["SKHX"].binance_symbol == "SKHYNIXUSDT"
    assert by_asset["SKHX"].binance_base_asset == "SKHYNIX"
    assert by_asset["SMSN"].binance_symbol == "SAMSUNGUSDT"
    assert by_asset["SKHX"].best_funding_direction == "LONG_HL_SHORT_BINANCE"


def test_tradfi_monitor_keeps_multiple_hyperliquid_dex_rows() -> None:
    markets = [
        _future(
            exchange="hyperliquid",
            raw_symbol="xyz:NVDA",
            base="NVDA",
            bid=200.0,
            ask=200.2,
            funding=0.01,
        ),
        _future(
            exchange="hyperliquid",
            raw_symbol="km:NVDA",
            base="NVDA",
            bid=201.0,
            ask=201.2,
            funding=0.02,
        ),
        _future(
            exchange="binance",
            raw_symbol="NVDAUSDT",
            base="NVDA",
            bid=200.5,
            ask=200.7,
            funding=0.12,
        ),
    ]

    preview = build_tradfi_perp_monitor_preview(
        markets,
        tradfi_bases={"NVDA"},
        now=datetime(2026, 6, 10, tzinfo=UTC),
    )

    assert preview.matched_count == 2
    assert {row.hl_raw_symbol for row in preview.rows} == {"xyz:NVDA", "km:NVDA"}
