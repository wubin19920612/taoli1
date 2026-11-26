import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api import routes_instruments as web
from app.api.mobile_query_utils import MobileQueryLimits
from app.core.security import require_session
from app.models.instrument import InstrumentMarketCapResult, InstrumentStatisticsResult
from app.models.mobile import (
    MobileInstrumentAssetGroup,
    MobileInstrumentLookup,
    MobileInstrumentMarket,
    MobileInstrumentSpread,
)

router = APIRouter(dependencies=[Depends(require_session)])
logger = logging.getLogger(__name__)
MAX_SPREADS = 200
MARKET_ERROR = "该交易所数据异常"
STATS_ERROR = "历史数据获取失败"


def _blank_to_none(value: str | None) -> str | None:
    return (value.strip() or None) if value is not None else None


def _mask_errors(items: dict, text: str) -> dict:
    return {key: item.model_copy(update={"error": text}) if item.error else item for key, item in items.items()}


@contextmanager
def _instrument_query(request: Request, *, upstream: bool = False) -> Iterator[None]:
    state = request.app.state
    key = "mobile_instrument_upstream_limits" if upstream else "mobile_instrument_lookup_limits"
    limits = getattr(state, key, None)
    if limits is None:
        limits = MobileQueryLimits(
            set(), logger=logger, label="标的查询", log_upstream=logger.warning,
            min_start_interval_seconds=5 if upstream else 2,
        )
        setattr(state, key, limits)

    with limits.query(request.state.auth_session_hash):
        try:
            yield
        except Exception as exc:
            if isinstance(exc, HTTPException) and exc.status_code in (422, 409):
                raise
            logger.warning("Mobile instrument query failed (%s)", type(exc).__name__)
            raise HTTPException(503, "标的数据暂不可用") from None


@router.get("/instruments/{symbol}", response_model=MobileInstrumentLookup)
async def lookup_mobile_instrument(
    symbol: str,
    request: Request,
    dex: str | None = None,
    asset_id: str | None = None,
) -> MobileInstrumentLookup:
    dex, asset_id = _blank_to_none(dex), _blank_to_none(asset_id)
    with _instrument_query(request):
        result = await web._lookup_instrument(
            symbol, request, dex, include_astro_routes=False, asset_id=asset_id,
        )
        return MobileInstrumentLookup(
            query=result.query,
            symbol=result.symbol,
            base=result.base,
            observed_at=result.observed_at,
            exchange_count=result.exchange_count,
            market_count=result.market_count,
            identity_ambiguous=result.identity_ambiguous,
            asset_groups=[
                MobileInstrumentAssetGroup(
                    asset_id=cast(str | None, group["asset_id"]),
                    display_name=cast(str, group["display_name"]),
                    market_count=len(cast(list[str], group["market_ids"])),
                )
                for group in result.asset_groups
            ],
            markets=[
                MobileInstrumentMarket.model_validate(market, from_attributes=True).model_copy(
                    update={"error": MARKET_ERROR if market.error else None},
                )
                for market in result.markets
            ],
            spreads=[
                MobileInstrumentSpread.model_validate(spread, from_attributes=True)
                for spread in result.spreads[:MAX_SPREADS]
            ],
            spread_total=len(result.spreads),
        )


@router.get("/instruments/{symbol}/statistics", response_model=InstrumentStatisticsResult)
async def mobile_instrument_statistics(
    symbol: str,
    request: Request,
    dex: str | None = None,
    asset_id: str | None = None,
) -> InstrumentStatisticsResult:
    dex, asset_id = _blank_to_none(dex), _blank_to_none(asset_id)
    with _instrument_query(request, upstream=True):
        result = await web.instrument_statistics(symbol, request, dex=dex, asset_id=asset_id)
        return result.model_copy(update={
            "markets": _mask_errors(result.markets, STATS_ERROR),
            "spreads": _mask_errors(result.spreads, STATS_ERROR),
        })


@router.get("/instrument-market-cap/{base}", response_model=InstrumentMarketCapResult)
async def mobile_instrument_market_cap(
    base: str,
    request: Request,
    coin_id: str | None = None,
) -> InstrumentMarketCapResult:
    with _instrument_query(request, upstream=True):
        return await web.instrument_market_cap(base, request, coin_id=_blank_to_none(coin_id))
