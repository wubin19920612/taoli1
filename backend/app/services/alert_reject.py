"""Rate-limited normal-rule diagnostics, independent of notification switches."""

import json
import logging
from collections import OrderedDict
from datetime import datetime
from math import isfinite

from app.services.data_filters import opportunity_is_excluded

logger = logging.getLogger("alert_reject")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())

MAX_REJECT_KEYS = 5000


class AlertRejectLogger:
    def __init__(self):
        self._logged: OrderedDict[str, datetime] = OrderedDict()

    def log_round(self, opportunities, rules, settings, engine, matches, now,
                  *, identity_rejected_ids=frozenset()):
        for key, timestamp in list(self._logged.items()):
            if (now - timestamp).total_seconds() >= 30:
                self._logged.pop(key)
        matched = {(match.rule.id, match.opportunity.id) for match in matches}
        active_rules = [rule for rule in rules if rule.enabled]
        for item in opportunities:
            if (not isfinite(item.open_spread_pct)
                    or item.open_spread_pct < settings.big_spread_alert_pct
                    or item.id in self._logged):
                continue
            reasons = []
            if opportunity_is_excluded(item, settings, now):
                reasons.append({"reasons": ["global_exclusion_or_market_closed"]})
            if item.id in identity_rejected_ids:
                reasons.append({"reasons": ["identity"]})
            for rule in active_rules:
                if (rule.id, item.id) in matched:
                    continue
                detail = engine.rejection_details(rule, item, now, settings)
                if not detail["reasons"]:
                    detail["reasons"] = ["evaluation_limit"]
                reasons.append(detail)
            if not active_rules:
                reasons.append({"reasons": ["no_enabled_rules"]})
            if not reasons:
                continue
            # Never evict a still-throttled key: churn must not bypass 30s limiting.
            if len(self._logged) >= MAX_REJECT_KEYS:
                continue
            logger.info(json.dumps({
                "symbol": item.symbol, "opportunity_id": item.id,
                "buy": item.buy_exchange, "sell": item.sell_exchange,
                "buy_raw_symbol": item.buy_raw_symbol, "sell_raw_symbol": item.sell_raw_symbol,
                "buy_dex": item.buy_dex, "sell_dex": item.sell_dex,
                "open_spread_pct": item.open_spread_pct,
                "buy_ask": item.buy_ask, "sell_bid": item.sell_bid,
                "buy_quote_time": item.buy_timestamp, "sell_quote_time": item.sell_timestamp,
                "buy_source_timestamp": item.buy_source_timestamp,
                "sell_source_timestamp": item.sell_source_timestamp,
                "last_seen_at": item.last_seen_at, "observed_at": now, "reasons": reasons,
            }, ensure_ascii=False, default=str, separators=(",", ":")))
            self._logged[item.id] = now
