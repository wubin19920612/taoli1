import logging
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.api import routes_opportunities as web
from app.api.mobile_query_utils import MobileQueryLimits
from app.core.security import require_session
from app.exchanges.base import utc_now
from app.models.mobile import MobileOpportunityItem, MobileOpportunityList
from app.models.opportunity import Opportunity, OpportunityType

router = APIRouter(dependencies=[Depends(require_session)])
logger = logging.getLogger(__name__)


def _limits(request: Request) -> MobileQueryLimits:
    state = request.app.state
    limits = getattr(state, "mobile_opportunity_limits", None)
    if limits is None:
        limits = state.mobile_opportunity_limits = MobileQueryLimits(
            set(), logger=logger, label="机会查询", log_upstream=logger.warning,
        )
    return limits


def _mobile_item(item: Opportunity) -> MobileOpportunityItem:
    buy_volume, sell_volume = item.buy_volume_24h_usdt, item.sell_volume_24h_usdt
    buy_age, sell_age = item.buy_age_ms, item.sell_age_ms
    return MobileOpportunityItem(
        id=item.id,
        type=item.type,
        symbol=item.symbol,
        buy_exchange=item.buy_exchange,
        buy_market_type=item.buy_market_type,
        buy_raw_symbol=item.buy_raw_symbol,
        buy_dex=item.buy_dex,
        sell_exchange=item.sell_exchange,
        sell_market_type=item.sell_market_type,
        sell_raw_symbol=item.sell_raw_symbol,
        sell_dex=item.sell_dex,
        open_spread_pct=item.open_spread_pct,
        close_spread_pct=item.close_spread_pct,
        fee_adjusted_open_pct=item.fee_adjusted_open_pct,
        min_volume_24h_usdt=(
            min(buy_volume, sell_volume)
            if buy_volume is not None and sell_volume is not None else None
        ),
        net_funding_hourly_pct=item.net_funding_hourly_pct,
        net_funding_next_hourly_pct=item.net_funding_next_hourly_pct,
        risk_labels=item.risk_labels,
        max_age_ms=max(buy_age, sell_age) if buy_age is not None and sell_age is not None else None,
        executable=item.executable,
        fake_flag=item.fake_flag,
    )


@router.get("/opportunities", response_model=MobileOpportunityList)
async def list_mobile_opportunities(
    request: Request,
    opportunity_type: Annotated[OpportunityType | None, Query(alias="type")] = None,
    symbol: str | None = None,
    exchange: str | None = None,
    min_open_spread_pct: Annotated[float | None, Query(allow_inf_nan=False)] = None,
    min_volume_24h_k: Annotated[float | None, Query(ge=0, allow_inf_nan=False)] = None,
    include_risky: bool = False,
    sort_by: web.OpportunitySort = "open_spread_pct",
    sort_order: Literal["asc", "desc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> MobileOpportunityList:
    with _limits(request).query(request.state.auth_session_hash):
        try:
            # The web handler always returns an array with include_metadata=False.
            opportunities = cast(list[Opportunity], await web.list_opportunities(
                request,
                Response(),
                type=opportunity_type,
                exclude_types=None,
                symbol=symbol,
                exchange=exchange,
                min_open_spread_pct=min_open_spread_pct,
                include_risky=include_risky,
                hidden_risk_labels=None,
                min_volume_24h_k=min_volume_24h_k,
                limit=None,
                sort_by=sort_by,
                sort_order=sort_order,
                include_metadata=False,
            ))
            return MobileOpportunityList(
                observed_at=utc_now(),
                total=len(opportunities),
                items=[_mobile_item(item) for item in opportunities[:limit]],
            )
        except Exception as exc:  # noqa: BLE001 - use the same response for internal failures.
            logger.warning("Mobile opportunities failed (%s)", type(exc).__name__)
            raise HTTPException(503, "机会数据暂不可用") from None
