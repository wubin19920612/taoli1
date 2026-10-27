from __future__ import annotations

import asyncio
from collections import Counter, OrderedDict
from datetime import UTC, datetime, timedelta
from itertools import combinations

import httpx

from app.models.market_identity import MarketKey, MarketValidationSettings, utc
from app.models.settings import RiskSettings
from app.services.market_identity import MarketIdentityRegistry
from app.services.orderbook_validator import OrderBookDepthValidator
from app.services.pair_validation import validate_pair


class _ExpiryCache(OrderedDict):
    """Discard expired payloads while retaining the old bounded eviction order."""

    def __init__(self):
        super().__init__()
        self._order = OrderedDict()

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._order.setdefault(key, None)

    def clear(self):
        super().clear()
        self._order.clear()

    def put(self, key, value, limit=1000, *, touch=True):
        self[key] = value
        if touch:
            self.move_to_end(key)
            self._order.move_to_end(key)
        while len(self._order) > limit:
            evicted, _ = self._order.popitem(last=False)
            super().pop(evicted, None)

    def prune(self, expired):
        for key, value in list(self.items()):
            if expired(value):
                # Keep only the key until the old capacity policy would evict it.
                super().pop(key)


class _BookAdapter:
    def __init__(self, row, books):
        self.name, self.books = row.exchange, books

    async def fetch_order_book(self, symbol, market_type, raw_symbol, limit=20):
        return self.books[(self.name, str(market_type), raw_symbol)]


class MarketValidationService:
    """Shared live identity authority and bounded, demand-driven book reviews."""

    def __init__(self, repository, store, adapters=()):
        self.repository = repository
        self.store = store
        self.registry = MarketIdentityRegistry(mode="shadow")
        self.settings = MarketValidationSettings()
        self.adapters = {a.name.lower(): a for a in adapters}
        self.books = OrderedDict()
        self.reviews = _ExpiryCache()
        self.tasks = {}
        self.failures = OrderedDict()
        self.discovered = set()
        self.semaphore = asyncio.Semaphore(3)
        self.book_semaphore = asyncio.Semaphore(3)
        self.review_inflight = {}
        self.review_attempted = OrderedDict()
        self.fx = None
        self.fx_task = None
        self.fx_retry_at = datetime.min.replace(tzinfo=UTC)
        self._refresh_lock = asyncio.Lock()
        self._mutation_epoch = 0
        self._last_incident = _ExpiryCache()
        self.collision_symbols = set()
        self.invalidated_ids = set()

    async def initialize(self, *, seed_reviewed=False):
        if seed_reviewed:
            from app.services.market_identity_seeds import reviewed_bindings

            await self.repository.import_catalog(reviewed_bindings())
        await self.refresh()

    async def refresh(self):
        async with self._refresh_lock:
            settings = await self.repository.settings()
            self.registry.mode = settings.mode
            self.registry.replace(await self.repository.list_bindings())
            if settings.review_concurrency != self.settings.review_concurrency:
                self.semaphore = asyncio.Semaphore(settings.review_concurrency)
                self.book_semaphore = asyncio.Semaphore(settings.review_concurrency)
            self.settings = settings
            self._mutation_epoch += 1
            self.reviews.clear()
            self.books.clear()

    def invalidate(self):
        self._mutation_epoch += 1
        self.reviews.clear()
        self.books.clear()

    def set_adapters(self, adapters):
        self.adapters = {a.name.lower(): a for a in adapters}

    def should_enforce(self, result):
        return (
            self.settings.mode == "enforce"
            or result.identity_relation in {"different", "conflict"}
            or bool(set(result.reason_codes) - {"ASSET_IDENTITY_UNVERIFIED", "UNIT_UNVERIFIED"})
        )

    @staticmethod
    def _bounded_put(cache, key, value, limit=1000):
        if isinstance(cache, _ExpiryCache):
            cache.put(key, value, limit)
            return
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)

    def _apply_fx(self, row, now):
        if row.original_quote_currency != "USDC" or not self.fx:
            return row
        bid, ask, _observed, expiry = self.fx
        if now >= expiry:
            return row
        old_bid = row.quote_conversion_bid if row.quote_conversion_source else 1
        old_ask = row.quote_conversion_ask if row.quote_conversion_source else 1
        return row.model_copy(
            update={
                "bid": row.bid / old_bid * bid,
                "ask": row.ask / old_ask * ask,
                "quote_conversion_bid": bid,
                "quote_conversion_ask": ask,
                "quote_conversion_rate": (bid + ask) / 2,
                "quote_conversion_source": "Binance public USDCUSDT bookTicker",
                "quote_conversion_valid_until": expiry,
            }
        )

    async def _refresh_fx(self):
        try:
            async with httpx.AsyncClient(timeout=self.settings.review_timeout_seconds) as client:
                response = await client.get(
                    "https://api.binance.com/api/v3/ticker/bookTicker",
                    params={"symbol": "USDCUSDT"},
                )
                response.raise_for_status()
                data = response.json()
            bid, ask = float(data["bidPrice"]), float(data["askPrice"])
            if not 0.5 < bid <= ask < 1.5:
                raise ValueError("invalid USDCUSDT conversion book")
            now = datetime.now(UTC)
            self.fx = (bid, ask, now, now + timedelta(seconds=15))
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            self.fx_retry_at = datetime.now(UTC) + timedelta(seconds=30)

    def _annotate_base(self, row, now=None):
        now = utc(now or datetime.now(UTC))
        return self._apply_fx(self.registry.annotate(row, now), now)

    def annotate(self, row, now=None):
        result = self._annotate_base(row, now)
        return result.model_copy(
            update={"identity_collision": row.symbol in self.collision_symbols}
        )

    def _cached_book(self, row, now, stale_after):
        try:
            cached = self.books.get(MarketKey.from_snapshot(row).key)
        except ValueError:
            return row
        if not cached:
            return row
        book, revision, fingerprint, fx_bid, fx_ask = cached
        if (
            revision != row.binding_revision
            or fingerprint != row.binding_fingerprint
            or (now - utc(book.timestamp)).total_seconds() > stale_after
        ):
            return row
        if (fx_bid, fx_ask) != (row.quote_conversion_bid, row.quote_conversion_ask):
            return row
        return self._book_row(row, book)

    @staticmethod
    def _book_row(row, book):
        return row.model_copy(
            update={
                "bid": book.bids[0].price,
                "ask": book.asks[0].price,
                "bid_size": book.bids[0].size,
                "ask_size": book.asks[0].size,
                "book_timestamp": book.timestamp,
                "quote_time_source": book.timestamp_source,
                "upstream_timestamp": book.timestamp,
                "quote_kind": "book",
                "is_estimated": False,
                "estimated_fields": [f for f in row.estimated_fields if f not in {"bid", "ask"}],
                "data_source": (row.data_source or row.exchange) + " + exact l2Book",
            }
        )

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

    @staticmethod
    def pair_key(a, b):
        return (
            MarketKey.from_snapshot(a).key,
            MarketKey.from_snapshot(b).key,
            a.binding_revision,
            b.binding_revision,
            a.binding_fingerprint,
            b.binding_fingerprint,
        )

    async def _background_review(self, a, b, risk, key):
        try:
            result, _, _ = await self.review(a, b, risk, twice=True)
            if self.repository is not None:
                incident_key = str(key)
                last = self._last_incident.get(incident_key)
                now = datetime.now(UTC)
                if last is None or (now - last).total_seconds() >= 60:
                    await self.repository.record_incident(
                        incident_key,
                        "SPREAD_REVIEW",
                        {"reasons": result.reason_codes},
                        "open" if result.blockers else "resolved",
                    )
                    self._last_incident.put(incident_key, now, touch=False)
                    self._last_incident.prune(lambda observed: (now - observed).total_seconds() >= 60)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - isolate a failed background review; no trading side effects.
            self._bounded_put(
                self.failures,
                key,
                datetime.now(UTC)
                + timedelta(seconds=self.settings.review_failure_cooldown_seconds),
            )
        finally:
            self.tasks.pop(key, None)

    async def _book(self, row):
        adapter = self.adapters.get(row.exchange)
        if adapter is None:
            raise ValueError("missing book adapter")
        async with self.book_semaphore:
            book = await asyncio.wait_for(
                adapter.fetch_order_book(row.symbol, row.market_type, row.raw_symbol, 20),
                self.settings.review_timeout_seconds,
            )
        if book is None or not book.bids or not book.asks or book.raw_symbol != row.raw_symbol:
            raise ValueError("book unavailable or exact raw market mismatch")
        bids = sorted(book.bids, key=lambda x: x.price, reverse=True)
        asks = sorted(book.asks, key=lambda x: x.price)
        if bids[0].price >= asks[0].price:
            raise ValueError("crossed book")
        # Adapter snapshots are in their public price units; face-value size
        # conversion is performed by the exchange adapter, as before.
        multiplier = row.symbol_alias_price_multiplier
        fx_bid = row.quote_conversion_bid if row.quote_conversion_source else 1
        fx_ask = row.quote_conversion_ask if row.quote_conversion_source else 1
        normalized = book.model_copy(
            update={
                "bids": [
                    x.model_copy(
                        update={"price": x.price * multiplier * fx_bid, "size": x.size / multiplier}
                    )
                    for x in bids
                ],
                "asks": [
                    x.model_copy(
                        update={"price": x.price * multiplier * fx_ask, "size": x.size / multiplier}
                    )
                    for x in asks
                ],
            }
        )
        # Reviews consume full depth; subsequent cached quotes need only the best levels.
        self._bounded_put(
            self.books,
            MarketKey.from_snapshot(row).key,
            (
                normalized.model_copy(
                    update={"bids": normalized.bids[:1], "asks": normalized.asks[:1]}
                ),
                row.binding_revision,
                row.binding_fingerprint,
                row.quote_conversion_bid,
                row.quote_conversion_ask,
            ),
        )
        return normalized

    async def review(self, a, b, risk, *, twice=False, comparison_kind="same_asset"):
        key = (*self.pair_key(a, b), comparison_kind, twice, risk.model_dump_json())
        existing = self.review_inflight.get(key)
        if existing is not None:
            return await asyncio.shield(existing)
        if len(self.review_inflight) >= 24:
            raise ValueError("review queue is full; retry later")

        async def run():
            async with self.semaphore:
                return await self._review(a, b, risk, twice=twice, comparison_kind=comparison_kind)

        task = asyncio.create_task(run())
        self.review_inflight[key] = task

        def forget(completed):
            self.review_inflight.pop(key, None)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(forget)
        return await asyncio.shield(task)

    async def _review(self, a, b, risk, *, twice=False, comparison_kind="same_asset"):
        from app.services.spread_engine import build_directional_opportunity

        epoch = self._mutation_epoch
        initial_revisions = (a.binding_revision, b.binding_revision)
        a, b = self.annotate(a), self.annotate(b)
        initial = validate_pair(
            a,
            b,
            comparison_kind=comparison_kind,
            huge_spread_pct=risk.huge_spread_pct,
            stale_after_seconds=risk.stale_after_seconds,
            max_leg_time_skew_seconds=self.settings.max_leg_time_skew_seconds,
        )
        hard = {
            "ASSET_IDENTITY_UNVERIFIED",
            "ASSET_IDENTITY_CONFLICT",
            "UNIT_UNVERIFIED",
            "DEX_IDENTITY_CONFLICT",
            "DIFFERENT_UNDERLYING_ASSET",
            "QUOTE_CONVERSION_UNVERIFIED",
            "INCOMPATIBLE_CONTRACT_SPEC",
        }
        blocking = hard.intersection(initial.reason_codes)
        if self.settings.mode == "shadow" and initial.identity_relation not in {
            "different",
            "conflict",
        }:
            blocking -= {"ASSET_IDENTITY_UNVERIFIED", "UNIT_UNVERIFIED"}
        if blocking:
            return initial, a, b
        if any(initial_revisions) and initial_revisions != (a.binding_revision, b.binding_revision):
            from app.models.market_identity import ValidationIssue

            initial.blockers.append(
                ValidationIssue(code="MAPPING_CHANGED", message="映射已改变，请重新预览")
            )
            initial.capabilities.manual_card = initial.capabilities.automatic_card = False
            return initial, a, b
        passes = 2 if twice or initial.anomaly_status == "pending_review" else 1
        for index in range(passes):
            if index:
                await asyncio.sleep(1)
            first, second = await asyncio.gather(self._book(a), self._book(b))
            a, b = self._book_row(a, first), self._book_row(b, second)
            result = validate_pair(
                a,
                b,
                comparison_kind=comparison_kind,
                review_passed=True,
                huge_spread_pct=risk.huge_spread_pct,
                stale_after_seconds=risk.stale_after_seconds,
                max_leg_time_skew_seconds=self.settings.max_leg_time_skew_seconds,
            )
            blocking_codes = set(result.reason_codes)
            if self.settings.mode == "shadow" and result.identity_relation not in {
                "different",
                "conflict",
            }:
                blocking_codes -= {"ASSET_IDENTITY_UNVERIFIED", "UNIT_UNVERIFIED"}
            if blocking_codes:
                return result, a, b
            mode = (
                "SS"
                if a.market_type == b.market_type == "spot"
                else "FF"
                if a.market_type == b.market_type == "future"
                else "SF"
            )
            opp = build_directional_opportunity(a, b, mode=mode)
            # Reuse the existing depth fill implementation, with already-fetched
            # normalized books and no extra network requests or side effects.
            books = {
                (a.exchange, str(a.market_type), a.raw_symbol): first,
                (b.exchange, str(b.market_type), b.raw_symbol): second,
            }
            validator = OrderBookDepthValidator([_BookAdapter(a, books), _BookAdapter(b, books)])
            depth = await validator.validate(
                opp, risk.model_copy(update={"min_effective_open_pct": -1e9})
            )
            if not depth.passed:
                from app.models.market_identity import ValidationIssue

                result.blockers.append(
                    ValidationIssue(code="DEPTH_UNCONFIRMED", message="；".join(depth.blockers))
                )
                result.capabilities.same_asset_opportunity = result.capabilities.strategy_alert = (
                    False
                )
                result.capabilities.manual_card = result.capabilities.automatic_card = False
                return result, a, b
        if epoch != self._mutation_epoch:
            raise ValueError("mapping changed during review")
        result.expires_at = min(
            result.expires_at,
            datetime.now(UTC) + timedelta(seconds=self.settings.review_ttl_seconds),
        )
        result.registry_revision = self._mutation_epoch
        self._bounded_put(self.reviews, self.pair_key(a, b), result)
        return result, a, b

    def validate(self, a, b, risk=None, *, comparison_kind="same_asset"):
        risk = risk or RiskSettings()
        now = datetime.now(UTC)
        a, b = self.annotate(a, now), self.annotate(b, now)
        cached = self.reviews.get(self.pair_key(a, b))
        # Cached depth reviews can qualify only that exact sampled quote.
        from app.services.pair_validation import quote_fingerprint

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

    def _opportunity_legs(self, opportunity, index=None):
        legs = []
        index = self._market_index() if index is None else index
        for side in ("buy", "sell"):
            raw = getattr(opportunity, side + "_raw_symbol")
            if not raw:
                raise ValueError("missing exact raw market identity")
            key = MarketKey(
                exchange=getattr(opportunity, side + "_exchange"),
                market_type=getattr(opportunity, side + "_market_type"),
                raw_symbol=raw,
                dex=getattr(opportunity, side + "_dex"),
            )
            row = index.get(key.key)
            if row is None:
                raise ValueError("exact live market is not available")
            legs.append(row)
        return legs

    async def qualify_opportunity(self, opportunity, risk, *, fresh=False):
        a, b = self._opportunity_legs(opportunity)
        if fresh:
            result, a, b = await self.review(
                a, b, risk, comparison_kind=opportunity.comparison_kind
            )
        else:
            result = self.validate(a, b, risk, comparison_kind=opportunity.comparison_kind)
        return result, a, b

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

    def coverage(self, markets=None):
        rows = [
            self.annotate(row)
            for row in (markets if markets is not None else self.store.get_all_markets())
        ]
        states = Counter(row.identity_status for row in rows)
        return {
            "mode": self.settings.mode,
            "markets": len(rows),
            "verified": states["verified"],
            "pending": states["pending"],
            "conflict": states["conflict"],
            "expired": states["expired"],
            "reference_quotes": sum(row.quote_kind != "book" for row in rows),
            "review_jobs": len(self.tasks),
            "book_cache": len(self.books),
            "fx_available": bool(self.fx and self.fx[3] > datetime.now(UTC)),
        }

    def audit_cards(self, cards):
        from app.models.market import MarketType
        from app.models.pair_spread import normalize_pair_spread_symbol
        from app.services.pair_validation import validate_pair

        rows = self.store.get_all_markets()
        results = []
        for card in cards:
            if not isinstance(card, dict):
                continue
            name = str(card.get("name") or "")
            kind = str(card.get("type") or "FF").upper()
            bases = name.split("-", 1) if kind.endswith("R") else [name, name]
            matched, errors = [], []
            for index, side in enumerate(("a", "b")):
                route = str(card.get("buyEx" if index == 0 else "sellEx") or "").removeprefix("gc-")
                exchange = {"hl": "hyperliquid", "bitgetr": "bitget"}.get(route, route)
                market_type = (
                    MarketType.SPOT
                    if index < len(kind) and kind[index] == "S"
                    else MarketType.FUTURE
                )
                dex = card.get(side + "EffectiveHlDex") or card.get(side + "HlDex")
                try:
                    symbol = normalize_pair_spread_symbol(bases[index])
                except (ValueError, IndexError):
                    errors.append("卡片标的无法解析")
                    continue
                candidates = [
                    m
                    for m in rows
                    if m.exchange == exchange
                    and m.market_type == market_type
                    and symbol in {m.symbol, m.symbol_alias_original_symbol}
                    and (exchange != "hyperliquid" or dex is None or (m.dex or "main") == dex)
                ]
                unique = {MarketKey.from_snapshot(m).key: m for m in candidates}
                if len(unique) != 1:
                    errors.append("原始市场存在歧义或行情缺失")
                else:
                    matched.append(self.annotate(next(iter(unique.values()))))
            validation = (
                validate_pair(
                    *matched,
                    comparison_kind="custom_cross_asset" if kind.endswith("R") else "same_asset",
                )
                if len(matched) == 2
                else None
            )
            results.append(
                {
                    "card_id": card.get("id"),
                    "card_name": name,
                    "buy_route": card.get("buyEx"),
                    "sell_route": card.get("sellEx"),
                    "reported_enabled": card.get("status", "unknown"),
                    "position_status": "unknown",
                    "markets": [MarketKey.from_snapshot(m).model_dump() for m in matched],
                    "validation": validation,
                    "errors": errors,
                    "action": "read_only",
                }
            )
        return results

    async def aclose(self):
        pending = (
            list(self.tasks.values())
            + list(self.review_inflight.values())
            + ([self.fx_task] if self.fx_task else [])
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
