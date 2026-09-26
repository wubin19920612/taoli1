from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx

from .paper_engine import (
    advance_paper_trade,
    apply_funding_settlement,
    create_paper_trade,
    decode_route_inputs,
)
from .paper_funding import PublicFundingHistory
from .paper_models import PaperAccount, PaperRun, PaperSettings, PaperTrade
from .paper_repository import SqueezePaperRepository
from .route_engine import Route, lsk_research_route
from .route_repository import SqueezeRouteRepository

logger = logging.getLogger(__name__)


class SqueezePaperMonitor:
    def __init__(
        self, repo: SqueezePaperRepository, route_repo: SqueezeRouteRepository,
        settings: PaperSettings | None = None,
        funding: PublicFundingHistory | None = None,
    ) -> None:
        self.repo = repo
        self.route_repo = route_repo
        self.settings = settings or PaperSettings()
        self.funding = funding or PublicFundingHistory()
        self.route_id = lsk_research_route().route_id
        self.running = False
        self.last_error: str | None = None

    async def aclose(self) -> None:
        await self.funding.aclose()

    async def status(self) -> dict:
        run = await self.repo.load_run()
        accounts = await self.repo.load_accounts()
        counts = await self.repo.status_counts()
        return {
            "enabled": self.running,
            "source_capability": "research_only_public_rest_paper",
            "rule_version": run.settings.rule_version if run else self.settings.rule_version,
            "started_at": run.started_at.isoformat() if run else None,
            "continuous_started_at": run.continuous_started_at.isoformat()
            if run and run.continuous_started_at else None,
            "coverage_gap_count": run.coverage_gap_count if run else 0,
            "coverage_gap_open": run.coverage_gap_open if run else False,
            "last_processed_at": run.last_processed_at.isoformat()
            if run and run.last_processed_at else None,
            "last_success_at": run.last_success_at.isoformat()
            if run and run.last_success_at else None,
            "last_error": self.last_error or (run.last_error if run else None),
            "trade_counts": counts,
            "unresolved_exposure_count": counts.get("unresolved", 0),
            "accounts": [account.model_dump(mode="json") for account in accounts.values()],
        }

    async def _settle_due(
        self, trade: PaperTrade, accounts: dict[str, PaperAccount],
        route: Route, observed_at: datetime,
    ) -> None:
        if trade.opened_at is None or (
            trade.expensive_open_quantity == 0 and trade.cheap_open_quantity == 0
            and trade.closed_at is None
        ):
            return
        if trade.funding_retry_at and observed_at < trade.funding_retry_at:
            return
        through = min(observed_at, trade.closed_at) if trade.closed_at else observed_at
        for leg, due_field, interval_field in (
            (route.expensive, "next_expensive_funding_at", "expensive_funding_interval_hours"),
            (route.cheap, "next_cheap_funding_at", "cheap_funding_interval_hours"),
        ):
            due = getattr(trade, due_field)
            if due is None or due > through:
                continue
            interval = getattr(trade, interval_field)
            if interval is None or interval <= 0:
                trade.last_error = "paper_funding_interval_missing"
                trade.funding_gap_at = due
                trade.funding_retry_at = observed_at + timedelta(seconds=60)
                return
            previous = max(
                (flow.occurred_at for flow in trade.cashflows
                 if flow.kind == "funding" and flow.leg_key == leg.key),
                default=trade.opened_at,
            )
            try:
                settlements = await self.funding.settlements(leg, previous, through)
            except (httpx.HTTPError, ValueError, RuntimeError, TimeoutError) as exc:
                logger.warning("paper funding history unavailable for %s: %s", leg.key, exc)
                trade.last_error = f"funding_history_unavailable:{type(exc).__name__}"[:500]
                trade.funding_gap_at = due
                trade.funding_retry_at = observed_at + timedelta(seconds=60)
                return
            expected_times = (
                due + timedelta(hours=interval * index)
                for index in range(len(settlements))
            )
            if not settlements or any(
                settlement.settled_at != expected
                for settlement, expected in zip(settlements, expected_times)
            ):
                trade.last_error = "paper_funding_settlement_pending"
                trade.funding_gap_at = due
                trade.funding_retry_at = observed_at + timedelta(seconds=60)
                return
            for settlement in settlements:
                apply_funding_settlement(trade, accounts, settlement)
            next_due = settlements[-1].settled_at + timedelta(hours=interval)
            setattr(trade, due_field, next_due)
            if next_due <= through:
                trade.last_error = "paper_funding_settlement_pending"
                trade.funding_gap_at = next_due
                trade.funding_retry_at = observed_at + timedelta(seconds=60)
                return
        trade.funding_retry_at = None
        trade.funding_gap_at = None

    async def scan_once(self, now: datetime | None = None) -> None:
        observed_at = (now or datetime.now(UTC)).astimezone(UTC)
        route_seed = lsk_research_route()
        run = await self.repo.get_or_create_run(
            self.settings, observed_at,
            (route_seed.expensive.exchange, route_seed.cheap.exchange),
        )
        gap_open = run.coverage_gap_open
        if run.last_success_at and (
            observed_at - run.last_success_at > timedelta(seconds=15)
        ) and not gap_open:
            await self.repo.mark_coverage_gap(observed_at)
            gap_open = True
        latest = await self.route_repo.latest_for_paper(self.route_id)
        if latest is None:
            if not gap_open:
                await self.repo.mark_coverage_gap(observed_at)
            self.last_error = "paper_route_snapshot_missing"
            await self._settle_without_book(run, route_seed, observed_at)
            return
        snapshot_at = datetime.fromisoformat(latest["evaluated_at"]).astimezone(UTC)
        if observed_at - snapshot_at > timedelta(seconds=15):
            if not gap_open:
                await self.repo.mark_coverage_gap(observed_at)
            self.last_error = "paper_route_snapshot_stale"
            await self._settle_without_book(run, route_seed, observed_at)
            return
        if gap_open:
            await self.repo.close_coverage_gap(observed_at)
        if run.last_processed_at and snapshot_at <= run.last_processed_at:
            return
        route, expensive, cheap = decode_route_inputs(latest["inputs"])
        if route.route_id != self.route_id:
            raise ValueError("paper route identity mismatch")
        accounts = await self.repo.load_accounts()
        trades = await self.repo.list_worker_trades()
        before = {trade.id: trade.model_dump_json() for trade in trades}
        known = {trade.event_id for trade in trades}
        event_at, event_id = run.last_event_at, run.last_event_id
        while True:
            events = await self.route_repo.confirmed_events_after(event_at, event_id)
            eligible = [
                event for event in events
                if datetime.fromisoformat(event["occurred_at"]) <= snapshot_at
            ]
            for event in eligible:
                if event["route_id"] == route.route_id and event["id"] not in known:
                    trade = create_paper_trade(event, run.settings)
                    active = [
                        item for item in trades
                        if item.status not in {"closed", "blocked", "unfilled"}
                    ]
                    if len(active) >= run.settings.max_simultaneous_positions or any(
                        item.asset_id == trade.asset_id for item in active
                    ):
                        trade.status = "blocked"
                        trade.last_error = "paper_position_limit_or_same_asset"
                    trades.append(trade)
                    known.add(trade.event_id)
                event_at = datetime.fromisoformat(event["occurred_at"])
                event_id = event["id"]
            if len(eligible) < 100:
                break
        for trade in trades:
            if trade.route_id != route.route_id:
                continue
            await self._settle_due(trade, accounts, route, snapshot_at)
            advance_paper_trade(
                trade, accounts, route, expensive, cheap, snapshot_at, run.settings
            )
        updated = PaperRun(
            started_at=run.started_at, settings=run.settings,
            last_processed_at=snapshot_at, last_event_at=event_at,
            last_event_id=event_id, last_success_at=observed_at,
            last_error=None,
        )
        changed = [
            trade for trade in trades
            if before.get(trade.id) != trade.model_dump_json()
        ]
        if await self.repo.save_snapshot(updated, accounts, changed):
            self.last_error = None

    async def _settle_without_book(
        self, run: PaperRun, route: Route, observed_at: datetime,
    ) -> None:
        accounts = await self.repo.load_accounts()
        trades = await self.repo.list_worker_trades()
        before = {trade.id: trade.model_dump_json() for trade in trades}
        for trade in trades:
            if trade.route_id != route.route_id:
                continue
            await self._settle_due(trade, accounts, route, observed_at)
            if trade.status == "pending_entry" and (
                observed_at - trade.signal_at > timedelta(seconds=60)
            ):
                trade.status = "unfilled"
                trade.last_error = "entry_book_unavailable_after_delay"
            elif trade.opened_at and trade.status in {
                "recovering", "open", "pending_exit"
            } and observed_at - trade.opened_at >= timedelta(
                seconds=run.settings.max_holding_seconds
            ):
                trade.status = "unresolved"
                trade.last_error = "exit_book_unavailable_at_max_holding"
        changed = [
            trade for trade in trades
            if before[trade.id] != trade.model_dump_json()
        ]
        if changed:
            await self.repo.save_snapshot(
                run, accounts, changed, funding_only=True, ledger_at=observed_at
            )

    async def run(self, stop_event: asyncio.Event) -> None:
        self.running = True
        try:
            while not stop_event.is_set():
                try:
                    await self.scan_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}:{exc}"[:500]
                    logger.exception("squeeze paper monitor failed")
                    try:
                        await self.repo.record_error(self.last_error)
                    except Exception:
                        logger.exception("squeeze paper error state could not be persisted")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=2)
                except TimeoutError:
                    pass
        finally:
            self.running = False
