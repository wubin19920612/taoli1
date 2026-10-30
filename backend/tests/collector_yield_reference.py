"""Frozen post-processing from 6b146b26fe248fc02c3d20804cb9ab4da48ac537."""
import asyncio
import logging
from datetime import UTC, datetime
from itertools import combinations

from app.models.market_identity import MarketKey
from app.services.collector import CollectionResult, MarketCollector
from app.services.data_filters import filter_markets, filter_opportunities, ignored_exchange_set
from app.services.market_validation_review import MarketValidationService
from app.services.risk_labels import risk_labels_for
from app.services.symbol_aliases import apply_symbol_aliases

logger = logging.getLogger(__name__)


class BaselineValidation(MarketValidationService):
    async def prepare(self, markets, risk):
        now = datetime.now(UTC)
        self.reviews.prune(lambda result: result.expires_at <= now)
        unknown = []
        for row in markets:
            try:
                key = MarketKey.from_snapshot(row).key
            except ValueError:
                continue
            if key not in self.discovered:
                unknown.append(row)
                self.discovered.add(key)
        if unknown:
            await self.repository.discover(unknown)
        annotated = [
            self._cached_book(self._annotate_base(row, now), now, risk.stale_after_seconds)
            for row in markets
        ]
        assets_by_symbol = {}
        kinds_by_symbol = {}
        for row in annotated:
            if row.asset_id:
                assets_by_symbol.setdefault(row.symbol, set()).add(row.asset_id)
            if row.asset_kind:
                kinds_by_symbol.setdefault(row.symbol, set()).add(row.asset_kind)
        self.collision_symbols = {
            symbol for symbol, assets in assets_by_symbol.items() if len(assets) > 1
        }
        self.collision_symbols.update(
            symbol for symbol, kinds in kinds_by_symbol.items() if len(kinds) > 1
        )
        annotated = [
            row
            if row.identity_collision == (row.symbol in self.collision_symbols)
            else row.model_copy(update={"identity_collision": row.symbol in self.collision_symbols})
            for row in annotated
        ]
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


class BaselineCollector(MarketCollector):
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

        ignored_exchanges = ignored_exchange_set(self.risk_settings)
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

        with self.stage_timings.measure(sample, "prepare"):
            markets = self._combined_exchange_markets(active_adapters)
            errors = self._combined_exchange_errors(active_adapters)

            aliased_markets = apply_symbol_aliases(markets, self.risk_settings.symbol_aliases)
            if self.market_validation is not None:
                aliased_markets = await self.market_validation.prepare(aliased_markets, self.risk_settings)
            filtered_markets = filter_markets(aliased_markets, self.risk_settings)

        with self.stage_timings.measure(sample, "build"):
            opportunities = self._build_labeled_opportunities(filtered_markets, now=now)
            if self.market_validation is not None:
                by_key = {
                    (m.exchange, m.market_type, m.raw_symbol): m for m in filtered_markets
                }
                accepted = []
                for item in opportunities:
                    a = by_key.get((item.buy_exchange, item.buy_market_type, item.buy_raw_symbol))
                    b = by_key.get((item.sell_exchange, item.sell_market_type, item.sell_raw_symbol))
                    if a is None or b is None:
                        continue
                    item.validation = self.market_validation.validate(a, b, self.risk_settings)
                    if self.market_validation.should_enforce(item.validation) and not item.validation.capabilities.same_asset_opportunity:
                        continue
                    item.risk_labels = risk_labels_for(item, self.risk_settings, now)
                    accepted.append(item)
                opportunities = accepted
            filtered_opportunities = filter_opportunities(
                opportunities,
                self.risk_settings,
                now=now,
            )
        with self.stage_timings.measure(sample, "publish"):
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
