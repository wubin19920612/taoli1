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
MAX_REJECT_LINES_PER_ROUND = 20


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
        rejected = {}
        candidates = [item for item in opportunities
                      if isfinite(item.open_spread_pct)
                      and item.open_spread_pct >= settings.big_spread_alert_pct
                      and item.id not in self._logged]
        for item in sorted(candidates, key=lambda item: item.open_spread_pct, reverse=True):
            if item.id in rejected:
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
            rejected[item.id] = (item, reasons)
        # Reserve a line for the summary whenever details exceed the round budget.
        detail_limit = MAX_REJECT_LINES_PER_ROUND - (len(rejected) > MAX_REJECT_LINES_PER_ROUND)
        # Never evict a still-throttled key: churn must not bypass 30s limiting.
        detail_limit = min(detail_limit, max(0, MAX_REJECT_KEYS - len(self._logged)))
        for item, reasons in list(rejected.values())[:detail_limit]:
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
        suppressed = len(rejected) - min(len(rejected), detail_limit)
        if suppressed:
            logger.info(json.dumps({
                "event": "alert_reject_summary", "suppressed_count": suppressed,
                "observed_at": now,
            }, default=str, separators=(",", ":")))
