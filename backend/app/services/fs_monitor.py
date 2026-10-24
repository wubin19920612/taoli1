import asyncio
import time
from datetime import UTC, datetime, timedelta

from app.models.fs_monitor import FsCandidate, FsReport
from app.models.market import MarketType
from app.runtime.supervisor import beat
from app.services.fs_bybit import BybitFsProvider, FsDataError, number, timestamp
from app.services.fs_economics import evaluate
from app.services.fs_public import PublicFsProvider, public_borrow_details, route_key
from app.services.pair_validation import validate_pair


def identity_check(row, markets, spot_book, future_book, now):
    row.identity_details = []
    legs = []
    for kind, book in ((MarketType.SPOT, spot_book), (MarketType.FUTURE, future_book)):
        matches = [
            m
            for m in markets
            if m.exchange == row.exchange
            and m.market_type == kind
            and m.raw_symbol
            == (
                (row.spot_raw_symbol if kind == MarketType.SPOT else row.future_raw_symbol)
                or row.symbol
            )
            and not m.dex
        ]
        if len(matches) != 1:
            return "unverified", None, []
        m = matches[0]
        row.identity_details.append(
            {
                "market_type": kind.value,
                "raw_symbol": m.raw_symbol,
                "identity_status": m.identity_status,
                "unit_status": m.unit_status,
                "asset_id": m.asset_id,
                "revision": m.binding_revision,
                "fingerprint": m.binding_fingerprint,
            }
        )
        # Metadata and verified binding must agree on the original contract size.
        # Books supplied here have already been converted into base-coin units.
        if (
            m.symbol_alias_price_multiplier != 1
            or (m.contract_size_multiplier or 1)
            != (row.contract_multiplier if kind == MarketType.FUTURE else 1)
            or m.quote != "USDT"
            or m.contract_kind not in {"linear", "spot"}
        ):
            return "unverified", None, ["unsupported_multiplier"]
        legs.append(
            m.model_copy(
                update={
                    "bid": float(book["b"][0][0]),
                    "ask": float(book["a"][0][0]),
                    "book_timestamp": timestamp(book.get("ts")),
                    "quote_time_source": "upstream",
                    "quote_kind": "book",
                    "estimated_fields": [],
                }
            )
        )
    result = validate_pair(legs[1], legs[0], now=now)
    if result.blockers:
        row.identity_details[-1]["validation_issues"] = ",".join(x.code for x in result.blockers)
    if result.capabilities.same_asset_opportunity:
        row.valid_until = result.expires_at
        return "verified", legs[0].asset_id, []
    conflict = result.identity_relation in {"different", "conflict"}
    return "conflict" if conflict else "unverified", None, ["identity_conflict"] if conflict else []


class FsMonitor:
    def __init__(
        self,
        repository,
        accounts,
        store,
        provider=None,
        astro=None,
        identities=None,
        providers=None,
    ):
        self.repository, self.accounts, self.store = repository, accounts, store
        self.provider = provider or BybitFsProvider()
        self.providers = providers or (
            {"bybit": provider}
            if provider
            else {
                "bybit": self.provider,
                **{ex: PublicFsProvider(ex, store) for ex in ("binance", "okx", "bitget", "gate")},
            }
        )
        self.report = FsReport()
        self.scanning = False
        self.wake = asyncio.Event()
        self.generation = 0
        self._states = {}
        self.astro = astro
        self.identities = identities
        self._last_duration = 0.0

    def markets(self, symbol, exchange="bybit"):
        from app.services.fs_public import raw_symbols

        raw = raw_symbols(exchange, symbol[:-4])
        rows = [
            m
            for m in self.store.get_markets()
            if m.exchange == exchange and m.raw_symbol in raw and not m.dex
        ]
        return [self.identities.annotate(m) for m in rows] if self.identities else rows

    async def aclose(self):
        await asyncio.gather(*(p.aclose() for p in self.providers.values()))
        await self.repository.aclose()

    async def update_settings(self, settings):
        if settings.account_id:
            await self.credentials(settings.account_id)
        await self.repository.save_settings(settings)
        self.generation += 1
        self.report = FsReport(settings=settings)
        self._states = {}
        self.wake.set()
        return settings

    async def credentials(self, account_id):
        if not account_id:
            return None, None
        service = self.accounts
        if not service or not service.storage_ready:
            raise FsDataError("account_storage_unavailable")
        connection = await service.repository.get(account_id)
        if (
            connection is None
            or connection.exchange != "bybit"
            or not connection.enabled
            or not connection.include_spot
            or not connection.include_futures
        ):
            raise FsDataError("account_unavailable")
        try:
            creds = service.cipher.decrypt(connection.encrypted_credentials)
        except Exception:  # noqa: BLE001 - redact vendor/credential errors and keep monitoring alive.
            raise FsDataError("account_credentials_unavailable") from None
        return creds, connection.updated_at

    async def current(self):
        settings = await self.repository.settings()
        result = self.report.model_copy(deep=True)
        result.settings = settings
        result.scanning = self.scanning
        now = datetime.now(UTC)
        account_invalid = False
        if settings.account_id:
            try:
                _, stamp = await self.credentials(settings.account_id)
                account_invalid = stamp != getattr(self, "_account_stamp", None)
            except FsDataError:
                account_invalid = True
        for row in result.candidates:
            identity_changed = False
            if row.identity_state == "verified" and row.identity_details:
                for detail in row.identity_details:
                    matches = [
                        m
                        for m in self.markets(row.symbol, row.exchange)
                        if m.exchange == row.exchange
                        and m.market_type.value == detail["market_type"]
                        and not m.dex
                    ]
                    if len(matches) != 1 or (
                        matches[0].identity_status != "verified"
                        or matches[0].unit_status != "verified"
                        or matches[0].binding_revision != detail["revision"]
                        or matches[0].binding_fingerprint != detail["fingerprint"]
                        or not matches[0].binding_valid_until
                        or matches[0].binding_valid_until <= now
                    ):
                        identity_changed = True
                        break
            if (
                (account_invalid and row.exchange == "bybit")
                or identity_changed
                or not settings.enabled
                or (row.valid_until is not None and row.valid_until <= now)
            ):
                row.stage = "stale"
                row.signal_ready = False
                reason = (
                    "account_changed"
                    if account_invalid and row.exchange == "bybit"
                    else "identity_changed"
                    if identity_changed
                    else "observation_expired"
                )
                row.signal_blockers = list(dict.fromkeys(row.signal_blockers + [reason]))
                row.blockers = list(dict.fromkeys(row.blockers + [reason]))
        result.stale = (
            result.observed_at is None
            or (now - result.observed_at).total_seconds() > 90
            or account_invalid
            or not settings.enabled
            or bool(result.error)
            or bool(result.candidates and all(r.stage == "stale" for r in result.candidates))
        )
        return result

    async def scan(self):
        if self.scanning:
            return
        self.scanning = True
        started = time.monotonic()
        generation = self.generation
        settings = self.report.settings
        try:
            settings = await self.repository.settings()
            async with asyncio.timeout(50):
                await self._scan(settings, generation)
        except Exception as exc:  # noqa: BLE001 - bounded scan isolates failures without leaking secrets.
            if generation == self.generation:
                self.report = FsReport(
                    settings=settings,
                    exchange_status=self.report.exchange_status,
                    observed_at=datetime.now(UTC),
                    error=str(exc) if isinstance(exc, FsDataError) else "scan_failed",
                )
        finally:
            self._last_duration = time.monotonic() - started
            self.report.scan_duration_seconds = round(self._last_duration, 3)
            self.scanning = False

    async def _scan(self, settings, generation):
        enabled = list(dict.fromkeys(ex for ex in settings.exchanges if ex in self.providers))
        snapshots, statuses = {}, {}

        async def load(ex):
            try:
                async with asyncio.timeout(12):
                    snapshots[ex] = await self.providers[ex].public_snapshot()
                statuses[ex] = "ok" if snapshots[ex]["future_tickers"] else "no_market_data"
            except Exception as exc:  # noqa: BLE001 - isolate venue failures and redact vendor text.
                statuses[ex] = str(exc) if isinstance(exc, FsDataError) else "public_query_failed"

        await asyncio.gather(*(load(ex) for ex in enabled))
        if generation != self.generation:
            return
        self.report.exchange_status = statuses
        if not snapshots:
            raise FsDataError(next(iter(statuses.values()), "public_query_failed"))
        credentials, account_stamp, account_context, account_at, account_error = (
            None,
            None,
            {},
            None,
            None,
        )
        if settings.account_id and "bybit" in enabled:
            try:
                credentials, account_stamp = await self.credentials(settings.account_id)
                account_context, account_at = await self.provider.account_context(credentials)
            except FsDataError as exc:
                account_error = str(exc)
                credentials = None
        pools = {}
        for ex, snapshot in snapshots.items():
            negatives = [
                x
                for x in snapshot["future_tickers"].values()
                if number(x.get("fundingRate")) is not None
                and float(x["fundingRate"]) < 0
                and snapshot["futures"].get(x["symbol"], {}).get("quoteCoin") == "USDT"
            ]
            negatives.sort(
                key=lambda x: (
                    x["symbol"] not in snapshot["spots"],
                    snapshot["rates"]
                    .get(snapshot["spots"].get(x["symbol"], {}).get("baseCoin"), {})
                    .get("borrowable")
                    is not True,
                    float(x["fundingRate"])
                    / (number(x.get("fundingIntervalHour"), nonnegative=True) or 1),
                    x["symbol"],
                )
            )
            pools[ex] = negatives
        negative_count = sum(len(v) for v in pools.values())
        # Fair round-robin prevents the largest venue from consuming every slot.
        chosen = []
        for index in range(settings.max_candidates):
            for ex in enabled:
                if index < len(pools.get(ex, [])):
                    chosen.append((ex, pools[ex][index]))
                    if len(chosen) == settings.max_candidates:
                        break
            if len(chosen) == settings.max_candidates:
                break
        selected = [t["symbol"] if ex == "bybit" else ex + ":" + t["symbol"] for ex, t in chosen]
        self.report.exchange_status = statuses
        self.report.candidates = [r for r in self.report.candidates if route_key(r) in selected]
        self.report.negative_count = negative_count
        semaphore = asyncio.Semaphore(5)

        async def build(ex, ticker):
            async with semaphore:
                try:
                    async with asyncio.timeout(12):
                        row = await self._candidate(
                            ticker,
                            snapshots[ex],
                            settings,
                            credentials if ex == "bybit" else None,
                            account_context if ex == "bybit" else {},
                            account_at if ex == "bybit" else None,
                            account_error if ex == "bybit" else None,
                            exchange=ex,
                        )
                except TimeoutError:
                    row = FsCandidate(
                        symbol=ticker["symbol"],
                        base=ticker["symbol"][:-4],
                        exchange=ex,
                        observed_at=datetime.now(UTC),
                        valid_until=datetime.now(UTC) + timedelta(seconds=15),
                        stage="blocked",
                        blockers=["query_timeout"],
                        signal_blockers=["query_timeout"],
                    )
                attempt = await self.repository.astro_attempt(route_key(row))
                if attempt:
                    row.astro_state = "uncertain" if attempt == "pending" else "recorded"
                if generation == self.generation:
                    rows = {
                        route_key(r): r for r in self.report.candidates if route_key(r) in selected
                    }
                    rows[route_key(row)] = row
                    self.report.candidates = [rows[s] for s in selected if s in rows]
                    self.report.observed_at = datetime.now(UTC)
                    self.report.error = None
                return row

        candidates = await asyncio.gather(*(build(ex, ticker) for ex, ticker in chosen))
        if generation != self.generation:
            return
        if settings.account_id and credentials:
            _, current_stamp = await self.credentials(settings.account_id)
            if current_stamp != account_stamp:
                raise FsDataError("account_changed")
        self._account_stamp = account_stamp
        now = datetime.now(UTC)
        for row in candidates:
            if row.valid_until and row.valid_until <= now:
                row.stage = "stale"
                row.signal_ready = False
                row.blockers = list(dict.fromkeys([*row.blockers, "observation_expired"]))
                row.signal_blockers = list(
                    dict.fromkeys([*row.signal_blockers, "observation_expired"])
                )
        report = FsReport(
            settings=settings,
            observed_at=datetime.now(UTC),
            stale=False,
            negative_count=negative_count,
            exchange_status=statuses,
            candidates=candidates,
            sources=[
                "https://www.okx.com/api/v5/public/interest-rate-loan-quota",
                "https://api.bitget.com/api/v2/margin/currencies",
                "https://api.gateio.ws/api/v4/margin/uni/currency_pairs",
                "https://api.binance.com/api/v3/exchangeInfo",
            ]
            + [
                "https://api.bybit.com" + path
                for path in (
                    "/v5/market/instruments-info",
                    "/v5/market/tickers",
                    "/v5/market/orderbook",
                    "/v5/spot-margin-trade/data",
                    "/v5/account/collateral-info",
                    "/v5/spot-margin-trade/max-borrowable",
                    "/v5/spot-margin-trade/flexible-available-inventory",
                    "/v5/account/fee-rate",
                )
            ],
        )
        states = {
            route_key(r): ("signal_ready" if r.signal_ready else r.stage, tuple(sorted(r.blockers)))
            for r in candidates
        }
        events = [
            (s, stage, list(reasons))
            for s, (stage, reasons) in states.items()
            if self._states.get(s) != (stage, reasons)
        ]
        events.extend(
            (s, "left_pool", ["no_longer_in_selected_candidates"])
            for s in self._states
            if s not in states
        )
        if generation != self.generation:
            return
        await self.repository.save(report, events)
        if generation != self.generation:
            return
        self._states = states
        self.report = report
        if self.astro and settings.astro_auto_create:
            for row in candidates:
                if not row.signal_ready or generation != self.generation:
                    continue
                row.astro_state = await self.astro.process(
                    row,
                    lambda symbol=row.symbol, ex=row.exchange: self.recheck(
                        symbol, settings, generation, ex
                    ),
                    lambda: generation == self.generation,
                )
                if row.astro_state == "signal_changed":
                    row.signal_ready = False
                    row.signal_blockers.append("signal_changed")
                if row.astro_state in {"created", "uncertain"}:
                    # Astro's add action may restart its core; only one new write
                    # per scan, and no following write after an ambiguous result.
                    break

    async def recheck(self, symbol, settings, generation, exchange="bybit"):
        """Fresh, complete public/account sample before an external card write."""
        data = await self.providers[exchange].public_snapshot()
        ticker = data["future_tickers"].get(symbol)
        if ticker is None or generation != self.generation:
            raise FsDataError("signal_changed")
        credentials, context, account_at, error = None, {}, None, None
        if settings.account_id and exchange == "bybit":
            credentials, stamp = await self.credentials(settings.account_id)
            if stamp != self._account_stamp:
                raise FsDataError("account_changed")
            context, account_at = await self.provider.account_context(credentials)
        row = await self._candidate(
            ticker, data, settings, credentials, context, account_at, error, exchange=exchange
        )
        if generation != self.generation or not row.signal_ready:
            raise FsDataError("signal_changed")
        return row

    async def _candidate(
        self,
        ticker,
        data,
        settings,
        credentials,
        account_context,
        account_at,
        account_error,
        *,
        exchange="bybit",
    ):
        provider = self.providers[exchange]
        symbol = ticker["symbol"]
        fm, sm = data["futures"].get(symbol, {}), data["spots"].get(symbol, {})
        base = fm.get("baseCoin", "")
        rate = data["rates"].get(base, {})
        st = data["spot_tickers"].get(symbol, {})
        row = FsCandidate(
            symbol=symbol,
            base=base,
            exchange=exchange,
            spot_raw_symbol=sm.get("symbol", symbol),
            future_raw_symbol=fm.get("symbol", symbol),
            observed_at=datetime.now(UTC),
            valid_until=datetime.now(UTC) + timedelta(seconds=15),
            funding_pct=float(ticker["fundingRate"]) * 100,
            # Ticker's current interval supersedes cached instrument metadata.
            interval_hours=number(ticker.get("fundingIntervalHour"), nonnegative=True),
            product_borrowable=(
                rate.get("borrowable") if isinstance(rate.get("borrowable"), bool) else None
            ),
            # Keep the original Bybit-only field for historical snapshots/API clients.
            public_vip_limit=(
                number(rate.get("maxBorrowingAmount"), nonnegative=True)
                if exchange == "bybit"
                else None
            ),
            public_borrow=public_borrow_details(
                exchange, rate, data["margin_at"], failed=bool(data.get("borrow_error"))
            ),
            spot_volume_usdt=number(st.get("turnover24h"), nonnegative=True),
            future_volume_usdt=number(ticker.get("turnover24h"), nonnegative=True),
            spot_fee_pct=settings.spot_fee_pct,
            future_fee_pct=settings.future_fee_pct,
            source_times={
                "spot_ticker": st.get("source_at", data["spot_at"]),
                "future_ticker": ticker.get("source_at", data["future_at"]),
                "public_borrow": data["margin_at"],
            },
        )
        try:
            if exchange != "bybit":
                ticker = await provider.refresh_funding(ticker)
                row.funding_pct = float(ticker["fundingRate"]) * 100
                row.interval_hours = number(ticker.get("fundingIntervalHour"), nonnegative=True)
                row.source_times["funding"] = ticker["source_at"]
                if exchange in {"binance", "bitget", "gate"}:
                    row.blockers.append("borrow_rate_requires_account")
                if data.get("borrow_error"):
                    row.blockers.append(data["borrow_error"])
            row.next_funding_at = timestamp(ticker.get("nextFundingTime"))
            interest = number(rate.get("hourlyBorrowRate"), nonnegative=True)
            row.hourly_borrow_pct = interest * 100 if interest is not None else None
            row.borrow_rate_source = "public_no_vip" if interest is not None else "unknown"
            if not sm:
                raise FsDataError("spot_market_missing")
            (sm, sm_at), (fm, fm_at) = await provider.current_instruments(symbol)
            row.source_times.update(spot_specs=sm_at, future_specs=fm_at)
            if sm.get("marginBorrowable") is False:
                row.product_borrowable = False
            from app.services.fs_identity import TARGETS

            row.contract_multiplier = float(fm.get("baseMultiplier", 1))
            if (
                exchange == "bybit"
                and symbol in TARGETS
                and fm.get("fullName") != TARGETS[symbol][1]
            ):
                raise FsDataError("identity_conflict")
            if exchange == "bybit" and sm.get("marginTrading") not in {"utaOnly", "both"}:
                row.product_borrowable = False
            if account_error:
                row.account_state = account_error
            if credentials:
                try:
                    coin = account_context.get(base, {})
                    user_rate = number(coin.get("hourlyBorrowRate"), nonnegative=True)
                    if user_rate is None or coin.get("borrowable") is not True:
                        raise FsDataError("account_borrow_unavailable")
                    quote = await provider.account_quote(symbol, base, credentials)
                    row.hourly_borrow_pct = user_rate * 100
                    row.borrow_rate_source = "account"
                    row.account_state = "checked"
                    row.fee_source = "account"
                    for key in (
                        "account_max_loan",
                        "available_inventory",
                        "inventory_at",
                        "spot_fee_pct",
                        "future_fee_pct",
                    ):
                        setattr(row, key, quote[key])
                    row.source_times.update(
                        account=quote["account_at"],
                        account_interest=account_at,
                        inventory=quote["inventory_at"],
                    )
                except FsDataError as exc:
                    row.account_state = str(exc)
            (spot_book, _), (future_book, _) = await provider.books(symbol)
            now = datetime.now(UTC)
            row.identity_state, row.asset_id, reasons = identity_check(
                row,
                self.markets(row.symbol, row.exchange),
                spot_book,
                future_book,
                now,
            )
            row.blockers.extend(reasons)
            if row.identity_state != "verified":
                row.blockers.append("identity_unverified")
            return evaluate(
                row,
                settings,
                spot_meta=sm,
                future_meta=fm,
                spot_book=spot_book,
                future_book=future_book,
                mark=number(ticker.get("markPrice")),
                now=now,
            )
        except Exception as exc:  # noqa: BLE001 - bounded scan isolates failures without leaking secrets.
            row.stage = "blocked"
            row.blockers.append(
                str(exc) if isinstance(exc, FsDataError) else "candidate_query_failed"
            )
            row.signal_blockers = list(row.blockers)
            return row

    def refresh_delay(self, settings):
        # Start-to-start cadence, bounded by the existing source validity. Busy or
        # rate-limited scans never overlap; overdue rows still expire normally.
        interval = (
            min(settings.scan_interval_seconds, 10)
            if self.report.candidates
            else settings.scan_interval_seconds
        )
        if self.report.error:
            interval = max(interval, 30)
        return max(1.0, interval - self._last_duration)

    async def run(self, stop):
        while not stop.is_set():
            self.wake.clear()
            iteration_completed = False
            try:
                settings = await self.repository.settings()
                if settings.enabled:
                    await self.scan()
                delay = (
                    self.refresh_delay(settings)
                    if settings.enabled
                    else settings.scan_interval_seconds
                )
                self.report.next_refresh_at = datetime.now(UTC) + timedelta(seconds=delay)
                iteration_completed = True
                beat("fs-monitor", ok=True)
                await asyncio.wait_for(self.wake.wait(), timeout=delay)
            except TimeoutError:
                if not iteration_completed:
                    beat("fs-monitor", ok=False)
            except Exception:  # noqa: BLE001 - redact vendor/credential errors and keep monitoring alive.
                # A repository outage must not terminate all future monitoring.
                beat("fs-monitor", ok=False)
                self.report.error = "monitor_unavailable"
                await asyncio.sleep(30)
