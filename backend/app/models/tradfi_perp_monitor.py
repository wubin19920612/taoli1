from datetime import datetime
from typing import Literal

from pydantic import BaseModel


TradfiPerpDirection = Literal["LONG_HL_SHORT_BINANCE", "LONG_BINANCE_SHORT_HL"]


class TradfiPerpLeg(BaseModel):
    exchange: str
    symbol: str
    raw_symbol: str
    base_asset: str
    dex: str | None = None
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    mark_price: float | None = None
    index_price: float | None = None
    funding_rate_pct: float | None = None
    funding_rate_hourly_pct: float | None = None
    funding_interval_hours: float | None = None
    funding_next_time: datetime | None = None
    volume_24h_usdt: float | None = None
    open_interest: float | None = None
    timestamp: datetime


class TradfiPerpMonitorRow(BaseModel):
    id: str
    asset: str
    binance_base_asset: str
    binance_symbol: str
    hl_dex: str
    hl_symbol: str
    hl_raw_symbol: str
    hl: TradfiPerpLeg
    binance: TradfiPerpLeg
    mid_spread_pct: float | None = None
    mark_spread_pct: float | None = None
    index_spread_pct: float | None = None
    open_long_hl_short_binance_pct: float | None = None
    open_long_binance_short_hl_pct: float | None = None
    best_price_direction: TradfiPerpDirection | None = None
    best_open_edge_pct: float | None = None
    funding_edge_long_hl_short_binance_hourly_pct: float | None = None
    funding_edge_long_binance_short_hl_hourly_pct: float | None = None
    best_funding_direction: TradfiPerpDirection | None = None
    best_funding_edge_hourly_pct: float | None = None
    best_funding_edge_daily_pct: float | None = None
    min_volume_24h_usdt: float | None = None
    risk_labels: list[str]
    observed_at: datetime


class TradfiPerpUnmatchedAsset(BaseModel):
    source: Literal["hyperliquid", "binance"]
    asset: str
    raw_symbol: str | None = None
    dex: str | None = None
    suggested_alias: str | None = None


class TradfiPerpMonitorPreview(BaseModel):
    observed_at: datetime
    matched_count: int
    hyperliquid_asset_count: int
    binance_symbol_count: int
    rows: list[TradfiPerpMonitorRow]
    unmatched_hyperliquid: list[TradfiPerpUnmatchedAsset]
    unmatched_binance: list[TradfiPerpUnmatchedAsset]
