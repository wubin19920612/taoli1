import asyncio
from datetime import UTC, datetime

from app.models.fs_monitor import FsCandidate, FsReport
from app.models.market import MarketType
from app.services.fs_bybit import BybitFsProvider, FsDataError, number, timestamp
from app.services.fs_economics import evaluate
from app.services.pair_validation import validate_pair


def identity_check(row, markets, spot_book, future_book, now):
    legs = []
    for kind, book in ((MarketType.SPOT, spot_book), (MarketType.FUTURE, future_book)):
        matches = [
            m
            for m in markets
            if m.exchange == "bybit"
            and m.market_type == kind
            and m.raw_symbol == row.symbol
            and not m.dex
        ]
        if len(matches) != 1:
            return "unverified", None, []
        m = matches[0]
        # Bybit order quantities are base-coin units; its legacy ticker adapter leaves
        # contract_size_multiplier unset. The verified identity + official linear metadata
        # supply the unit evidence; an explicit non-unit multiplier is still rejected.
        if (
            m.symbol_alias_price_multiplier != 1
            or m.contract_size_multiplier not in {None, 1}
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
    if result.capabilities.same_asset_opportunity:
        row.valid_until = result.expires_at
        return "verified", legs[0].asset_id, []
    conflict = result.identity_relation in {"different", "conflict"}
    return "conflict" if conflict else "unverified", None, ["identity_conflict"] if conflict else []


class FsMonitor:
    def __init__(self, repository, accounts, store, provider=None):
        self.repository, self.accounts, self.store = repository, accounts, store
        self.provider = provider or BybitFsProvider()
        self.report = FsReport()
        self.scanning = False
        self.wake = asyncio.Event()
        self.generation = 0
        self._states = {}

    async def aclose(self):
        await self.provider.aclose()
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
            if (
                account_invalid
                or not settings.enabled
                or (row.valid_until is not None and row.valid_until <= now)
            ):
                row.stage = "stale"
                row.blockers = list(
                    dict.fromkeys(
                        row.blockers
                        + ["account_changed" if account_invalid else "observation_expired"]
                    )
                )
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
                    observed_at=datetime.now(UTC),
                    error=str(exc) if isinstance(exc, FsDataError) else "scan_failed",
                )
        finally:
            self.scanning = False

    async def _scan(self, settings, generation):
        snapshot = await self.provider.public_snapshot()
        credentials, account_stamp, account_context, account_at, account_error = (
            None,
            None,
            {},
            None,
            None,
        )
        if settings.account_id:
            try:
                credentials, account_stamp = await self.credentials(settings.account_id)
                account_context, account_at = await self.provider.account_context(credentials)
            except FsDataError as exc:
                account_error = str(exc)
                credentials = None
        negatives = [
            x
            for x in snapshot["future_tickers"].values()
            if number(x.get("fundingRate")) is not None
            and float(x["fundingRate"]) < 0
            and snapshot["futures"].get(x["symbol"], {}).get("quoteCoin") == "USDT"
        ]

        def score(ticker):
            interval = number(ticker.get("fundingIntervalHour"), nonnegative=True)
            return float(ticker["fundingRate"]) / (interval if interval else 1)

        # Prioritize supported spot pairs; unborrowable/no-spot observations remain visible afterward.
        negatives.sort(
            key=lambda x: (
                snapshot["rates"]
                .get(snapshot["spots"].get(x["symbol"], {}).get("baseCoin"), {})
                .get("borrowable")
                is not True,
                score(x),
                x["symbol"],
            )
        )
        semaphore = asyncio.Semaphore(3)

        async def build(ticker):
            async with semaphore:
                return await self._candidate(
                    ticker,
                    snapshot,
                    settings,
                    credentials,
                    account_context,
                    account_at,
                    account_error,
                )

        candidates = await asyncio.gather(*[build(x) for x in negatives[: settings.max_candidates]])
        if generation != self.generation:
            return
        if settings.account_id and credentials:
            _, current_stamp = await self.credentials(settings.account_id)
            if current_stamp != account_stamp:
                raise FsDataError("account_changed")
        self._account_stamp = account_stamp
        report = FsReport(
            settings=settings,
            observed_at=datetime.now(UTC),
            stale=False,
            negative_count=len(negatives),
            candidates=candidates,
            sources=[
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
        states = {r.symbol: (r.stage, tuple(sorted(r.blockers))) for r in candidates}
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

    async def _candidate(
        self, ticker, data, settings, credentials, account_context, account_at, account_error
    ):
        symbol = ticker["symbol"]
        fm, sm = data["futures"].get(symbol, {}), data["spots"].get(symbol, {})
        base = fm.get("baseCoin", "")
        rate = data["rates"].get(base, {})
        st = data["spot_tickers"].get(symbol, {})
        row = FsCandidate(
            symbol=symbol,
            base=base,
            observed_at=datetime.now(UTC),
            funding_pct=float(ticker["fundingRate"]) * 100,
            # Ticker's current interval supersedes cached instrument metadata.
            interval_hours=number(ticker.get("fundingIntervalHour"), nonnegative=True),
            product_borrowable=(
                rate.get("borrowable") if isinstance(rate.get("borrowable"), bool) else None
            ),
            public_vip_limit=number(rate.get("maxBorrowingAmount"), nonnegative=True),
            spot_volume_usdt=number(st.get("turnover24h"), nonnegative=True),
            future_volume_usdt=number(ticker.get("turnover24h"), nonnegative=True),
            spot_fee_pct=settings.spot_fee_pct,
            future_fee_pct=settings.future_fee_pct,
            source_times={
                "spot_ticker": data["spot_at"],
                "future_ticker": data["future_at"],
                "public_borrow": data["margin_at"],
            },
        )
        try:
            row.next_funding_at = timestamp(ticker.get("nextFundingTime"))
            interest = number(rate.get("hourlyBorrowRate"), nonnegative=True)
            row.hourly_borrow_pct = interest * 100 if interest is not None else None
            row.borrow_rate_source = "public_no_vip" if interest is not None else "unknown"
            if not sm:
                raise FsDataError("spot_market_missing")
            (sm, sm_at), (fm, fm_at) = await self.provider.current_instruments(symbol)
            row.source_times.update(spot_specs=sm_at, future_specs=fm_at)
            if sm.get("marginTrading") not in {"utaOnly", "both"}:
                row.product_borrowable = False
            if account_error:
                row.account_state = account_error
            if credentials:
                try:
                    coin = account_context.get(base, {})
                    user_rate = number(coin.get("hourlyBorrowRate"), nonnegative=True)
                    if user_rate is None or coin.get("borrowable") is not True:
                        raise FsDataError("account_borrow_unavailable")
                    quote = await self.provider.account_quote(symbol, base, credentials)
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
            (spot_book, _), (future_book, _) = await self.provider.books(symbol)
            now = datetime.now(UTC)
            row.identity_state, row.asset_id, reasons = identity_check(
                row,
                self.store.get_markets(),
                spot_book,
                future_book,
                now,
            )
            row.blockers.extend(reasons)
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
            return row

    async def run(self, stop):
        while not stop.is_set():
            self.wake.clear()
            try:
                settings = await self.repository.settings()
                if settings.enabled:
                    await self.scan()
                await asyncio.wait_for(self.wake.wait(), timeout=settings.scan_interval_seconds)
            except TimeoutError:
                pass
            except Exception:  # noqa: BLE001 - redact vendor/credential errors and keep monitoring alive.
                # A repository outage must not terminate all future monitoring.
                self.report.error = "monitor_unavailable"
                await asyncio.sleep(30)
