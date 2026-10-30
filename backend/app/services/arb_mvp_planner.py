from datetime import UTC, datetime
from math import isfinite

from app.models.astro import AstroPairPlan
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import AstroCardSettings
from app.services.astro_planner import AstroPairPlanner, AstroPlannerConfig, _base_name, _fr_pair_bases


class ArbMvpPlanner:
    def __init__(self, settings: AstroCardSettings):
        self.settings = settings

    def plan(self, opportunity: Opportunity, target: str) -> AstroPairPlan:
        blockers = []
        allowed = {"hyperliquid": "hyperliquid", "lighter": "lighter"}
        label = "live" if target == "arb_live" else "模拟盘"
        if target == "arb_paper":
            allowed["rh-lighter"] = "lighter-rh"
        if target not in {"arb_paper", "arb_live"}:
            blockers.append("未知 arb-mvp 建卡目标")
        if (opportunity.type != OpportunityType.FF
                or opportunity.buy_market_type != MarketType.FUTURE
                or opportunity.sell_market_type != MarketType.FUTURE):
            blockers.append("arb-mvp 只支持永续-永续（FF），不支持 SF/FS")
        if _fr_pair_bases(opportunity) is not None:
            blockers.append("arb-mvp 不支持两腿基础币不同的 FR 卡片")
        legs = []
        for exchange, raw in ((opportunity.buy_exchange, opportunity.buy_raw_symbol),
                              (opportunity.sell_exchange, opportunity.sell_raw_symbol)):
            if exchange not in allowed:
                blockers.append(f"arb-mvp {label} 不支持 {exchange}")
            if not raw or not raw.strip():
                blockers.append(f"{exchange} 缺少 raw symbol，不能建卡")
            leg = {"venue": allowed.get(exchange, exchange), "symbol": (raw or "").split(":", 1)[-1],
                   "leverage": self.settings.leverage}
            if exchange == "hyperliquid" and raw and ":" in raw:
                leg["dex"] = raw.split(":", 1)[0]
            legs.append(leg)
        settings = self.settings
        sizes = (settings.min_notional, settings.max_notional, settings.max_trade_usdt)
        if (not all(isfinite(value) for value in sizes)
                or not 10 <= sizes[0] <= sizes[1] <= sizes[2]):
            blockers.append("尺寸必须满足 10 <= minOrderUsd <= maxOrderUsd <= maxPositionUsd")
        if (not isinstance(settings.leverage, int) or isinstance(settings.leverage, bool)
                or not 1 <= settings.leverage <= 100):
            blockers.append("杠杆必须为 1–100 的整数")
        if opportunity.identity_enforced:
            validation = opportunity.validation
            if validation is None:
                blockers.append("市场身份尚未完成核验，不能建卡。")
            elif not validation.capabilities.manual_card or validation.expires_at <= datetime.now(UTC):
                blockers.extend(issue.message for issue in validation.blockers)
                if not validation.blockers:
                    blockers.append("市场核验已过期，请刷新报价。")
        pair = None
        if not blockers:
            open_position, close_position, _, _, _ = AstroPairPlanner(
                AstroPlannerConfig.from_card_settings(settings)
            ).position_thresholds(opportunity, allow_negative=True)
            pair = {"name": _base_name(opportunity.symbol), "type": "FF", "legA": legs[0], "legB": legs[1],
                    "openSpread": open_position, "closeSpread": close_position,
                    "maxPositionUsd": settings.max_trade_usdt,
                    "minOrderUsd": settings.min_notional, "maxOrderUsd": settings.max_notional,
                    "disableOpen": True, "disableClose": False, "maxSlippage": "0.002"}
        return AstroPairPlan(opportunity_id=opportunity.id, symbol=opportunity.symbol,
                             source_open_spread_pct=opportunity.open_spread_pct,
                             quoted_at=opportunity.last_seen_at, can_submit=not blockers,
                             pair=pair, sdk_payload={"action": "add", "pair": pair} if pair else None,
                             blockers=list(dict.fromkeys(blockers)))
