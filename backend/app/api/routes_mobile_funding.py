import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api import routes_funding_arbitrage as web
from app.api.mobile_query_utils import MobileQueryLimits
from app.core.security import require_session
from app.exchanges.base import utc_now
from app.models.funding_arbitrage import FundingArbitrageDecision
from app.models.mobile import MobileFundingCandidate, MobileFundingPreview, MobileFundingSettings

router = APIRouter(prefix="/funding-arbitrage", dependencies=[Depends(require_session)])
logger = logging.getLogger(__name__)


def _limits(request: Request) -> MobileQueryLimits:
    state = request.app.state
    limits = getattr(state, "mobile_funding_limits", None)
    if limits is None:
        limits = state.mobile_funding_limits = MobileQueryLimits(
            set(), logger=logger, label="资金费率查询", log_upstream=logger.warning,
        )
    return limits


@router.get("/preview", response_model=MobileFundingPreview)
async def get_mobile_funding_preview(
    request: Request,
    decision: FundingArbitrageDecision | None = None,
    candidate_type: Annotated[Literal["SF", "FF"] | None, Query(alias="type")] = None,
    symbol: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> MobileFundingPreview:
    with _limits(request).query(request.state.auth_session_hash):
        try:
            preview = await web.get_funding_arbitrage_preview(request)
            needle = (symbol or "").casefold()
            filtered = [
                item for item in preview.candidates
                if (decision is None or item.decision == decision)
                and (candidate_type is None or item.type == candidate_type)
                and needle in item.symbol.casefold()
            ]
            return MobileFundingPreview(
                observed_at=utc_now(),
                settings=MobileFundingSettings.model_validate(preview.settings, from_attributes=True),
                total_pairs_evaluated=preview.total_pairs_evaluated,
                displayed_candidates=preview.displayed_candidates,
                blocked_missing_funding=preview.blocked_missing_funding,
                blocked_liquidity=preview.blocked_liquidity,
                blocked_adl_risk=preview.blocked_adl_risk,
                blocked_expected_pnl=preview.blocked_expected_pnl,
                enter_count=preview.enter_count,
                hold_count=preview.hold_count,
                exit_count=preview.exit_count,
                blocked_count=preview.blocked_count,
                filtered_total=len(filtered),
                candidates=[
                    MobileFundingCandidate.model_validate(item, from_attributes=True)
                    for item in filtered[:limit]
                ],
            )
        except Exception as exc:  # noqa: BLE001 - mask internal failures consistently.
            logger.warning("Mobile funding preview failed (%s)", type(exc).__name__)
            raise HTTPException(503, "资金费率数据暂不可用") from None
