"""Frozen 00dfa9e loop-stall oracle. Never substitute optimized implementations."""
import asyncio
from copy import copy
from datetime import UTC, datetime, timedelta
from itertools import combinations
from app.models.market_identity import MarketKey
from app.models.settings import RiskSettings
from app.runtime.cooperative import YieldBudget
from app.services.market_validation_review import MarketValidationService
from app.services.collector import MarketCollector, CollectionResult, AdapterPollResult
from app.services.data_filters import filter_markets_async, filter_opportunities, ignored_exchange_set
from app.services.risk_labels import risk_labels_for
from app.services.symbol_aliases import apply_symbol_aliases_async


import hashlib
import json
import math
from datetime import UTC, datetime, timedelta

from app.models.market_identity import (
    MarketKey,
    PairCapabilities,
    PairValidationResult,
    ValidationIssue,
    utc,
)


def quote_fingerprint(a, b):
    values = [
        (
            r.bid,
            r.ask,
            str(r.book_timestamp or r.upstream_timestamp or r.timestamp),
            r.binding_revision,
            r.binding_fingerprint,
            r.quote_conversion_rate,
        )
        for r in (a, b)
    ]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def validate_pair(
    a,
    b,
    *,
    now=None,
    comparison_kind="same_asset",
    stale_after_seconds=30,
    max_leg_time_skew_seconds=10,
    huge_spread_pct=10,
    review_passed=False,
):
    now = utc(now or datetime.now(UTC))
    blockers = []
    warnings = []
    keys = []
    times = []
    valid_until = [now + timedelta(seconds=15)]

    def block(code, message, leg="pair"):
        blockers.append(ValidationIssue(code=code, message=message, leg=leg))

    for leg, row in (("buy", a), ("sell", b)):
        try:
            keys.append(MarketKey.from_snapshot(row))
        except ValueError:
            keys.append(None)
            block("DEX_IDENTITY_CONFLICT", "DEX 与原始市场代码冲突", leg)
        if row.identity_status == "conflict":
            block("ASSET_IDENTITY_CONFLICT", "资产或合约规格与核验记录冲突", leg)
        elif (
            row.identity_status != "verified"
            or not row.asset_id
            or not row.binding_valid_until
            or utc(row.binding_valid_until) <= now
        ):
            block("ASSET_IDENTITY_UNVERIFIED", "资产身份待核验或证据已过期", leg)
        if row.unit_status != "verified":
            block("UNIT_UNVERIFIED", "价格与合约单位待核验", leg)
        if row.binding_valid_until:
            valid_until.append(utc(row.binding_valid_until))
        estimated = {x.lower() for x in row.estimated_fields}
        if row.quote_kind != "book" or estimated.intersection({"bid", "ask"}):
            block("REFERENCE_QUOTE_ONLY", "仅有参考价格，缺少真实买卖报价", leg)
        if (
            not math.isfinite(row.bid)
            or not math.isfinite(row.ask)
            or row.bid <= 0
            or row.ask <= 0
            or row.bid > row.ask
        ):
            block("INVALID_ORDER_BOOK", "盘口价格无效或倒挂", leg)
        if row.contract_kind == "inverse":
            block("INCOMPATIBLE_CONTRACT_SPEC", "反向合约的成交与数量换算尚未核验", leg)
        observed = utc(row.book_timestamp or row.upstream_timestamp or row.timestamp)
        if (row.book_timestamp is not None and row.quote_time_source != "upstream") or (
            row.book_timestamp is None and row.upstream_timestamp is None
        ):
            warnings.append(
                ValidationIssue(
                    code="LOCAL_RECEIVE_TIME",
                    message="来源未提供上游行情时间，时效按本地接收时间计算",
                    leg=leg,
                )
            )
        times.append(observed)
        valid_until.append(observed + timedelta(seconds=stale_after_seconds))
        if (now - observed).total_seconds() > stale_after_seconds or (
            observed - now
        ).total_seconds() > 5:
            block("STALE_LEG_QUOTE", "报价已过期或时间异常", leg)
        currency = row.original_quote_currency or row.quote
        if currency != "USDT":
            if (
                not row.quote_conversion_source
                or not row.quote_conversion_valid_until
                or utc(row.quote_conversion_valid_until) <= now
            ):
                block("QUOTE_CONVERSION_UNVERIFIED", f"{currency} 到 USDT 缺少有效换算", leg)
            else:
                valid_until.append(utc(row.quote_conversion_valid_until))
        if row.volume_24h_usdt is None:
            warnings.append(
                ValidationIssue(code="UNKNOWN_VOLUME", message="本侧 24h 成交额未知", leg=leg)
            )
    statuses = {a.identity_status, b.identity_status}
    relation = "conflict" if "conflict" in statuses else "unresolved"
    if a.asset_id and b.asset_id and a.asset_id != b.asset_id:
        relation = "different"
    elif statuses == {"verified"} and a.asset_id and b.asset_id:
        relation = "same"
    if a.asset_kind and b.asset_kind and a.asset_kind != b.asset_kind:
        relation = "different"
    if relation == "different" and comparison_kind == "same_asset":
        block("DIFFERENT_UNDERLYING_ASSET", "双方属于不同底层资产，不能作为同币配对")
    if (a.contract_kind == "valuation" or b.contract_kind == "valuation") and (
        a.contract_kind != b.contract_kind
        or not a.tracking_definition
        or a.tracking_definition != b.tracking_definition
    ):
        block("INCOMPATIBLE_CONTRACT_SPEC", "估值口径与计价单位不一致，不能直接比较")
    if (
        (a.identity_collision or b.identity_collision)
        and relation != "same"
        and comparison_kind == "same_asset"
    ):
        relation = "conflict"
        block("ASSET_IDENTITY_CONFLICT", "同名代码已确认对应多个资产，须先选择并核验精确市场")
    if abs((times[0] - times[1]).total_seconds()) > max_leg_time_skew_seconds:
        block("LEG_TIME_SKEW", "双方行情时间差过大")
    scale = max(b.bid, a.ask)
    spread = (
        abs(200 * (b.bid / scale - a.ask / scale) / (b.bid / scale + a.ask / scale))
        if math.isfinite(scale) and scale > 0
        else 0
    )
    anomaly = "normal"
    if spread >= huge_spread_pct and comparison_kind == "same_asset":
        anomaly = "passed" if review_passed else "pending_review"
        if not review_passed:
            block("SPREAD_REVIEW_PENDING", "大价差等待双腿盘口与深度复核")
    allowed = not blockers
    same = allowed and relation == "same" and comparison_kind == "same_asset"
    return PairValidationResult(
        comparison_kind=comparison_kind,
        identity_relation=relation,
        unit_status="verified" if a.unit_status == b.unit_status == "verified" else "unverified",
        quote_status="reference"
        if any(x.code == "REFERENCE_QUOTE_ONLY" for x in blockers)
        else "invalid"
        if any(
            x.code
            in {
                "INVALID_ORDER_BOOK",
                "STALE_LEG_QUOTE",
                "LEG_TIME_SKEW",
                "QUOTE_CONVERSION_UNVERIFIED",
            }
            for x in blockers
        )
        else "executable",
        anomaly_status=anomaly,
        buy_market=keys[0],
        sell_market=keys[1],
        buy_asset_id=a.asset_id,
        sell_asset_id=b.asset_id,
        buy_revision=a.binding_revision,
        sell_revision=b.binding_revision,
        buy_spec_fingerprint=a.binding_fingerprint,
        sell_spec_fingerprint=b.binding_fingerprint,
        quote_fingerprint=quote_fingerprint(a, b),
        blockers=blockers,
        warnings=warnings,
        capabilities=PairCapabilities(
            reference_comparison=relation == "same" or comparison_kind == "custom_cross_asset",
            same_asset_opportunity=same,
            strategy_alert=same,
            automatic_card=same,
            manual_card=allowed and (same or comparison_kind == "custom_cross_asset"),
        ),
        validated_at=now,
        expires_at=min(valid_until),
    )


def same_asset_eligible(a, b):
    """Legacy isolated calculations may be unannotated; live rows are annotated."""
    if "conflict" in {a.identity_status, b.identity_status}:
        return False
    if a.asset_kind and b.asset_kind and a.asset_kind != b.asset_kind:
        return False
    if a.asset_id and b.asset_id and a.asset_id != b.asset_id:
        return False
    if (a.identity_collision or b.identity_collision) and not (
        a.identity_status == b.identity_status == "verified"
        and a.asset_id
        and a.asset_id == b.asset_id
    ):
        return False
    if not (a.identity_enforced or b.identity_enforced):
        return True
    return (
        a.identity_status == b.identity_status == "verified"
        and a.asset_id is not None
        and a.asset_id == b.asset_id
        and a.unit_status == b.unit_status == "verified"
    )


class LegacyValidation(MarketValidationService):
    async def prepare(self, markets, risk):
            budget = YieldBudget()
            now = datetime.now(UTC)
            # A registry's bindings are immutable, but replace() changes its reference.
            # Copy the reader so every row in an attempt sees the same mapping/mode.
            for attempt in range(3):
                epoch = self._mutation_epoch
                registry = copy(self.registry)
                cooperative = attempt < 2
                unknown = []
                discovered = self.discovered.copy()
                for row in markets:
                    if cooperative:
                        await budget.maybe_yield()
                    try:
                        key = MarketKey.from_snapshot(row).key
                    except ValueError:
                        continue
                    if key not in discovered:
                        unknown.append(row)
                        discovered.add(key)
                annotated = []
                assets_by_symbol = {}
                kinds_by_symbol = {}
                for row in markets:
                    if cooperative:
                        await budget.maybe_yield()
                    row = self._cached_book(
                        self._apply_fx(registry.annotate(row, now), now),
                        now, risk.stale_after_seconds,
                    )
                    annotated.append(row)
                    if row.asset_id:
                        assets_by_symbol.setdefault(row.symbol, set()).add(row.asset_id)
                    if row.asset_kind:
                        kinds_by_symbol.setdefault(row.symbol, set()).add(row.asset_kind)
                if epoch != self._mutation_epoch:
                    continue
                collision_symbols = set()
                for symbol, assets in assets_by_symbol.items():
                    if cooperative:
                        await budget.maybe_yield()
                    if len(assets) > 1:
                        collision_symbols.add(symbol)
                for symbol, kinds in kinds_by_symbol.items():
                    if cooperative:
                        await budget.maybe_yield()
                    if len(kinds) > 1:
                        collision_symbols.add(symbol)
                for index, row in enumerate(annotated):
                    if cooperative:
                        await budget.maybe_yield()
                    collision = row.symbol in collision_symbols
                    if row.identity_collision != collision:
                        annotated[index] = row.model_copy(update={"identity_collision": collision})
                # Nothing shared or persistent is changed by a discarded attempt.
                # Attempt three has no awaits, so a mapping refresh cannot interrupt it.
                if epoch == self._mutation_epoch:
                    break
            self.reviews.prune(lambda result: result.expires_at <= now)
            self.discovered = discovered
            if unknown:
                await self.repository.discover(unknown)
            # Discovery performs I/O after accepting the snapshot. Fail closed if a
            # refresh ran there; never publish collisions or invalidate from that epoch.
            if epoch != self._mutation_epoch:
                raise ValueError("mapping changed during preparation")
            self.collision_symbols = collision_symbols
            if self.collision_symbols:
                from app.services.spread_engine import opportunity_id, pair_allowed

                invalid = set()
                for symbol in self.collision_symbols:
                    collision_rows = [row for row in annotated if row.symbol == symbol and row.asset_id]
                    for a, b in combinations(collision_rows, 2):
                        if a.asset_id == b.asset_id:
                            continue
                        for first, second in ((a, b), (b, a)):
                            for mode in ("SF", "FF", "SS"):
                                if pair_allowed(mode, first, second):
                                    invalid.add(opportunity_id(mode, symbol, first, second))
                fresh_invalid = invalid - self.invalidated_ids
                if fresh_invalid:
                    await self.repository.invalidate_opportunities(fresh_invalid)
                    self.invalidated_ids.update(fresh_invalid)
            if (
                any(row.original_quote_currency == "USDC" for row in annotated)
                and now >= self.fx_retry_at
                and (not self.fx or (self.fx[3] - now).total_seconds() < 5)
                and (self.fx_task is None or self.fx_task.done())
            ):
                self.fx_task = asyncio.create_task(self._refresh_fx())
            groups = {}
            for row in annotated:
                if row.identity_status == "verified" and row.unit_status == "verified" and row.asset_id:
                    groups.setdefault(row.asset_id, []).append(row)
                elif self.settings.mode == "shadow" and not row.identity_collision:
                    groups.setdefault("unverified:" + row.symbol, []).append(row)
            if self.settings.mode == "shadow":
                for row in annotated:
                    key = "unverified:" + row.symbol
                    if (
                        row.identity_status == "verified"
                        and key in groups
                        and not row.identity_collision
                    ):
                        groups[key].append(row)
            candidates = []
            for legs in groups.values():
                for a, b in combinations(legs, 2):
                    if a.exchange == b.exchange and a.market_type == b.market_type:
                        continue
                    spread = abs(2 * (b.bid - a.ask) / (b.bid + a.ask) * 100)
                    if (
                        a.quote_kind == "reference"
                        or b.quote_kind == "reference"
                        or spread >= risk.huge_spread_pct
                    ):
                        if (
                            a.market_type == b.market_type
                            and a.bid > b.ask
                            or a.market_type == "future"
                            and b.market_type == "spot"
                        ):
                            a, b = b, a
                        candidates.append((spread, a, b))
            # Limit scheduling, not just concurrent network requests.
            candidates.sort(
                key=lambda item: (
                    self.review_attempted.get(self.pair_key(item[1], item[2]), 0),
                    -item[0],
                )
            )
            for _, a, b in candidates:
                if len(self.tasks) >= 12:
                    break
                key = self.pair_key(a, b)
                cached = self.reviews.get(key)
                if cached and cached.expires_at > now:
                    continue
                if self.failures.get(key, now) > now or key in self.tasks:
                    continue
                self._bounded_put(self.review_attempted, key, now.timestamp(), limit=5000)
                task = asyncio.create_task(self._background_review(a, b, risk, key))
                self.tasks[key] = task
            return annotated

    def validate(self, a, b, risk=None, *, comparison_kind="same_asset"):
            risk = risk or RiskSettings()
            now = datetime.now(UTC)
            a, b = self.annotate(a, now), self.annotate(b, now)
            cached = self.reviews.get(self.pair_key(a, b))
            # Cached depth reviews can qualify only that exact sampled quote.
        
            cached_blockers = set(cached.reason_codes) if cached else set()
            if self.settings.mode == "shadow":
                cached_blockers -= {"ASSET_IDENTITY_UNVERIFIED", "UNIT_UNVERIFIED"}
            passed = bool(
                cached
                and cached.expires_at > now
                and cached.quote_fingerprint == quote_fingerprint(a, b)
                and not cached_blockers
            )
            result = validate_pair(
                a,
                b,
                now=now,
                comparison_kind=comparison_kind,
                huge_spread_pct=risk.huge_spread_pct,
                stale_after_seconds=risk.stale_after_seconds,
                max_leg_time_skew_seconds=self.settings.max_leg_time_skew_seconds,
                review_passed=passed,
            )
            result.registry_revision = self._mutation_epoch
            return result

    def _market_index(self):
            result = {}
            for row in self.store.get_all_markets():
                try:
                    key = MarketKey.from_snapshot(row).key
                except ValueError:
                    continue
                previous = result.get(key)
                if previous is None or row.timestamp > previous.timestamp:
                    result[key] = row
            return result

    def filter_opportunities(self, opportunities, risk):
            result = []
            index = None
            now = datetime.now(UTC)
            for item in opportunities:
                if (
                    item.validation is not None
                    and item.validation.registry_revision == self._mutation_epoch
                    and item.validation.expires_at > now
                ):
                    if (
                        not self.should_enforce(item.validation)
                        or item.validation.capabilities.strategy_alert
                    ):
                        result.append(item)
                    continue
                try:
                    if index is None:
                        index = self._market_index()
                    a, b = self._opportunity_legs(item, index)
                    validation = self.validate(a, b, risk, comparison_kind=item.comparison_kind)
                except ValueError:
                    continue
                if self.should_enforce(validation) and not validation.capabilities.strategy_alert:
                    continue
                result.append(
                    item.model_copy(
                        update={
                            "validation": validation,
                            "identity_enforced": self.should_enforce(validation),
                        }
                    )
                )
            return result

class LegacyCollector(MarketCollector):
    async def _store_adapter_result(
        self,
        exchange_name: str,
        result: AdapterPollResult,
        now: datetime,
    ) -> None:
        state = self._state_for(exchange_name)
        state.in_flight = False
        if result.errors:
            if result.markets and not result.should_cool_down:
                self._exchange_snapshots[exchange_name] = pack(result.markets)
            self._exchange_errors[exchange_name] = result.errors
            state.last_error_at = now
            state.consecutive_failures += 1
            if result.should_cool_down:
                cooldown_seconds = result.cooldown_seconds or self._cooldown_seconds(
                    state.consecutive_failures
                )
                state.status = "cooling_down"
                state.cooldown_until = now + timedelta(seconds=cooldown_seconds)
                state.next_due_at = state.cooldown_until
            else:
                state.status = "degraded"
                state.cooldown_until = None
                state.next_due_at = now + timedelta(seconds=self.poll_interval_seconds)
            return

        self._exchange_snapshots[exchange_name] = pack(result.markets)
        self._exchange_errors.pop(exchange_name, None)
        state.status = "healthy"
        state.last_success_at = now
        state.consecutive_failures = 0
        state.cooldown_until = None
        state.next_due_at = now + timedelta(seconds=self.poll_interval_seconds)

    async def _collect_once(self, sample: dict[str, float]) -> CollectionResult:
            if self.risk_settings_loader is not None:
                try:
                    self.risk_settings = await asyncio.wait_for(
                        self.risk_settings_loader(), timeout=self.risk_settings_timeout_seconds,
                    )
                except Exception as exc:
                    if not self._risk_settings_loaded:
                        raise
                    self._risk_settings_fallbacks += 1
                    logger.warning("risk settings read failed (%s); reusing last successful settings", type(exc).__name__)
                else:
                    self._risk_settings_loaded = True

            risk = self.risk_settings
            ignored_exchanges = ignored_exchange_set(risk)
            active_adapters = [
                adapter for adapter in self.adapters if adapter.name.lower() not in ignored_exchanges
            ]
            if not active_adapters:
                with self.stage_timings.measure(sample, "publish"):
                    self.store.set_markets([])
                    self.store.set_opportunities([])
                    self.store.set_exchange_errors({})
                    self.store.mark_published(self._now_fn())
                    self._publish_to_slots([], [])
                return CollectionResult(markets=[], opportunities=[], exchange_errors={})

            with self.stage_timings.measure(sample, "poll"):
                self._seed_exchange_snapshots_from_store()
                now = self._now_fn()
                due_adapters = await self._claim_due_adapters(active_adapters, now)
                if due_adapters:
                    results = await asyncio.gather(
                        *(self._poll_adapter(adapter) for adapter in due_adapters),
                        return_exceptions=True,
                    )
                    await self._store_poll_results(due_adapters, results, now)
                    # Do not retain the consumed poll batch across post-processing yields.
                    del results

            with self.stage_timings.measure(sample, "prepare"):
                validation_epoch = getattr(self.market_validation, "_mutation_epoch", None)
                budget = YieldBudget()
                markets = await self._combined_exchange_markets(active_adapters, budget)
                errors = self._combined_exchange_errors(active_adapters)

                aliased_markets = await apply_symbol_aliases_async(
                    markets, risk.symbol_aliases, budget=budget,
                )
                # Do not retain the consumed unpacked batch across later yields.
                del markets
                if self.market_validation is not None:
                    aliased_markets = await self.market_validation.prepare(aliased_markets, risk)
                filtered_markets = await filter_markets_async(
                    aliased_markets, risk, budget=budget,
                )

            with self.stage_timings.measure(sample, "build"):
                opportunities = await self._build_labeled_opportunities_async(
                    filtered_markets, now=now, budget=budget, risk=risk,
                )
                if self.market_validation is not None:
                    by_key = {
                        (m.exchange, m.market_type, m.raw_symbol): m for m in filtered_markets
                    }
                    accepted = []
                    for item in opportunities:
                        await budget.maybe_yield()
                        a = by_key.get((item.buy_exchange, item.buy_market_type, item.buy_raw_symbol))
                        b = by_key.get((item.sell_exchange, item.sell_market_type, item.sell_raw_symbol))
                        if a is None or b is None:
                            continue
                        item.validation = self.market_validation.validate(a, b, risk)
                        if self.market_validation.should_enforce(item.validation) and not item.validation.capabilities.same_asset_opportunity:
                            continue
                        item.risk_labels = risk_labels_for(item, risk, now)
                        accepted.append(item)
                    opportunities = accepted
                filtered_opportunities = filter_opportunities(
                    opportunities,
                    risk,
                    now=now,
                )
            with self.stage_timings.measure(sample, "publish"):
                # A binding update can revoke capabilities while post-processing yields.
                # Keep its refreshed store intact and retry on the next collector round.
                if validation_epoch != getattr(self.market_validation, "_mutation_epoch", None):
                    raise ValueError("mapping changed during collection")
                self.store.set_all_markets(aliased_markets)
                self.store.set_markets(filtered_markets)
                self.store.set_opportunities(filtered_opportunities)
                self.store.set_exchange_errors(errors)
                self.store.mark_published(self._now_fn())
                self._publish_to_slots(filtered_markets, filtered_opportunities)
            if self.snapshot_slots is None:
                with self.stage_timings.measure(sample, "history"):
                    if self.history_recorder is not None:
                        # Preserve the recorder's own clock in the rollback path.
                        await self.history_recorder.record(filtered_opportunities)
                with self.stage_timings.measure(sample, "index"):
                    await self.process_index_components(filtered_markets)
            return CollectionResult(
                markets=filtered_markets,
                opportunities=filtered_opportunities,
                exchange_errors=errors,
            )


# Frozen synchronous CPU loops from the same baseline (support types are unchanged).
from collections import defaultdict
from collections.abc import Sequence
from app.models.market import MarketSnapshot, MarketType
from app.models.premium_index import SUPPORTED_PREMIUM_INDEX_EXCHANGES
from app.services.data_filters import filter_markets
from app.services.multi_exchange_premium import _EligibilityScan
from app.services.compact_markets import CompactMarkets, _FIELDS, _FIELD_BITS, _CONTAINERS, _copy_mutable


def pack(markets: Sequence[MarketSnapshot]) -> CompactMarkets:
    rows = []
    mutable_fields = set()
    for market in markets:
        values = []
        for name in _FIELDS:
            value = getattr(market, name)
            if isinstance(value, _CONTAINERS):
                mutable_fields.add(name)
                value = _copy_mutable(value)
            values.append(value)
        bitmap = sum(bit for name, bit in _FIELD_BITS if name in market.model_fields_set)
        rows.append((*values, bitmap))
    return CompactMarkets(
        rows=tuple(rows),
        mutable_fields=tuple(name for name in _FIELDS if name in mutable_fields),
    )


def _scan_eligibility(markets: list[MarketSnapshot], settings: RiskSettings):
    groups = defaultdict(list)
    by_market = {}
    has_duplicates = False
    for position, market in enumerate(filter_markets(markets, settings)):
        if (
            market.market_type != MarketType.FUTURE
            or market.exchange not in SUPPORTED_PREMIUM_INDEX_EXCHANGES
            or market.identity_status != "verified"
            or market.unit_status != "verified"
            or not market.asset_id
            or not market.binding_valid_until
        ):
            continue
        try:
            key = MarketKey.from_snapshot(market).key
        except ValueError:
            continue
        # Valuation contracts with different tracking definitions are distinct instruments.
        group = (
            market.asset_id,
            market.asset_kind or "",
            market.contract_kind,
            market.tracking_definition if market.contract_kind == "valuation" else "",
        )
        groups[group].append((position, key, market))
        has_duplicates |= key in by_market
        by_market[key] = (group, market)
    return _EligibilityScan(dict(groups), by_market, has_duplicates)
