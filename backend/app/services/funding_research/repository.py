from __future__ import annotations

import json
from datetime import datetime

import aiosqlite

from app.models.market import MarketSnapshot
from app.services.funding_research.models import (
    FundingResearchCandidate,
    FundingResearchCandidateSnapshot,
    FundingResearchPaperTrade,
)


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class FundingResearchRepository:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def create_market_snapshot(self, snapshot: MarketSnapshot) -> int:
        cursor = await self.db.execute(
            """
            INSERT INTO funding_research_market_snapshots (
              observed_at, exchange, symbol, market_type, bid, ask, bid_size, ask_size,
              volume_24h_usdt, funding_rate_pct, funding_next_rate_pct,
              funding_interval_hours, funding_next_time, mark_price, index_price,
              raw_symbol, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.timestamp.isoformat(),
                snapshot.exchange,
                snapshot.symbol,
                snapshot.market_type.value,
                snapshot.bid,
                snapshot.ask,
                snapshot.bid_size,
                snapshot.ask_size,
                snapshot.volume_24h_usdt,
                snapshot.funding_rate_pct,
                snapshot.funding_next_rate_pct,
                snapshot.funding_interval_hours,
                _dt(snapshot.funding_next_time),
                snapshot.mark_price,
                snapshot.index_price,
                snapshot.raw_symbol,
                snapshot.model_dump_json(),
            ),
        )
        await self.db.commit()
        return int(cursor.lastrowid)

    async def create_market_snapshots(self, snapshots: list[MarketSnapshot]) -> int:
        if not snapshots:
            return 0
        await self.db.executemany(
            """
            INSERT INTO funding_research_market_snapshots (
              observed_at, exchange, symbol, market_type, bid, ask, bid_size, ask_size,
              volume_24h_usdt, funding_rate_pct, funding_next_rate_pct,
              funding_interval_hours, funding_next_time, mark_price, index_price,
              raw_symbol, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.timestamp.isoformat(),
                    item.exchange,
                    item.symbol,
                    item.market_type.value,
                    item.bid,
                    item.ask,
                    item.bid_size,
                    item.ask_size,
                    item.volume_24h_usdt,
                    item.funding_rate_pct,
                    item.funding_next_rate_pct,
                    item.funding_interval_hours,
                    _dt(item.funding_next_time),
                    item.mark_price,
                    item.index_price,
                    item.raw_symbol,
                    item.model_dump_json(),
                )
                for item in snapshots
            ],
        )
        await self.db.commit()
        return len(snapshots)

    async def create_candidate_snapshot(
        self,
        candidate: FundingResearchCandidate,
        *,
        observed_at: datetime,
    ) -> int:
        cursor = await self.db.execute(
            """
            INSERT INTO funding_research_opportunity_snapshots (
              observed_at, symbol, long_exchange, short_exchange,
              expected_net_funding_pct, expected_basis_change_pct, estimated_cost_pct,
              risk_buffer_pct, ev_pct, score, decision, basis_alignment,
              basis_diff_pct, long_basis_pct, short_basis_pct, funding_window_hours,
              next_settlement_time, minutes_to_settlement, funding_source,
              risk_labels_json, reasons_json, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observed_at.isoformat(),
                candidate.symbol,
                candidate.long_exchange,
                candidate.short_exchange,
                candidate.expected_net_funding_pct,
                candidate.expected_basis_change_pct,
                candidate.estimated_cost_pct,
                candidate.risk_buffer_pct,
                candidate.ev_pct,
                candidate.score,
                candidate.decision,
                candidate.basis_alignment,
                candidate.basis_diff_pct,
                candidate.long_basis_pct,
                candidate.short_basis_pct,
                candidate.funding_window_hours,
                _dt(candidate.next_settlement_time),
                candidate.minutes_to_settlement,
                candidate.funding_source,
                json.dumps(candidate.risk_labels, ensure_ascii=False),
                json.dumps(candidate.reasons, ensure_ascii=False),
                candidate.model_dump_json(),
            ),
        )
        await self.db.commit()
        return int(cursor.lastrowid)

    async def create_candidate_snapshots(
        self,
        candidates: list[FundingResearchCandidate],
        *,
        observed_at: datetime,
    ) -> int:
        if not candidates:
            return 0
        await self.db.executemany(
            """
            INSERT INTO funding_research_opportunity_snapshots (
              observed_at, symbol, long_exchange, short_exchange,
              expected_net_funding_pct, expected_basis_change_pct, estimated_cost_pct,
              risk_buffer_pct, ev_pct, score, decision, basis_alignment,
              basis_diff_pct, long_basis_pct, short_basis_pct, funding_window_hours,
              next_settlement_time, minutes_to_settlement, funding_source,
              risk_labels_json, reasons_json, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    observed_at.isoformat(),
                    item.symbol,
                    item.long_exchange,
                    item.short_exchange,
                    item.expected_net_funding_pct,
                    item.expected_basis_change_pct,
                    item.estimated_cost_pct,
                    item.risk_buffer_pct,
                    item.ev_pct,
                    item.score,
                    item.decision,
                    item.basis_alignment,
                    item.basis_diff_pct,
                    item.long_basis_pct,
                    item.short_basis_pct,
                    item.funding_window_hours,
                    _dt(item.next_settlement_time),
                    item.minutes_to_settlement,
                    item.funding_source,
                    json.dumps(item.risk_labels, ensure_ascii=False),
                    json.dumps(item.reasons, ensure_ascii=False),
                    item.model_dump_json(),
                )
                for item in candidates
            ],
        )
        await self.db.commit()
        return len(candidates)

    async def list_recent_candidates(
        self,
        *,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[FundingResearchCandidate]:
        if symbol is None:
            cursor = await self.db.execute(
                """
                SELECT payload FROM funding_research_opportunity_snapshots
                ORDER BY observed_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            )
        else:
            cursor = await self.db.execute(
                """
                SELECT payload FROM funding_research_opportunity_snapshots
                WHERE symbol = ?
                ORDER BY observed_at DESC, id DESC
                LIMIT ?
                """,
                (symbol, limit),
            )
        rows = await cursor.fetchall()
        return [FundingResearchCandidate.model_validate_json(row["payload"]) for row in rows]

    async def list_candidate_snapshots(
        self,
        *,
        symbol: str | None = None,
        long_exchange: str | None = None,
        short_exchange: str | None = None,
        limit: int = 240,
    ) -> list[FundingResearchCandidateSnapshot]:
        clauses: list[str] = []
        params: list[object] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol)
        if long_exchange:
            clauses.append("long_exchange = ?")
            params.append(long_exchange)
        if short_exchange:
            clauses.append("short_exchange = ?")
            params.append(short_exchange)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        cursor = await self.db.execute(
            f"""
            SELECT observed_at, payload FROM funding_research_opportunity_snapshots
            {where}
            ORDER BY observed_at DESC, id DESC
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        snapshots = [
            FundingResearchCandidateSnapshot(
                observed_at=row["observed_at"],
                candidate=FundingResearchCandidate.model_validate_json(row["payload"]),
            )
            for row in rows
        ]
        return list(reversed(snapshots))

    async def prune_snapshots_before(self, cutoff: datetime) -> int:
        market_cursor = await self.db.execute(
            "DELETE FROM funding_research_market_snapshots WHERE observed_at < ?",
            (cutoff.isoformat(),),
        )
        candidate_cursor = await self.db.execute(
            "DELETE FROM funding_research_opportunity_snapshots WHERE observed_at < ?",
            (cutoff.isoformat(),),
        )
        await self.db.commit()
        return (market_cursor.rowcount or 0) + (candidate_cursor.rowcount or 0)

    async def upsert_paper_trade(self, trade: FundingResearchPaperTrade) -> FundingResearchPaperTrade:
        await self.db.execute(
            """
            INSERT INTO funding_research_paper_trades (
              id, status, symbol, long_exchange, short_exchange, opened_at, closed_at,
              open_long_basis_pct, open_short_basis_pct, open_basis_diff_pct,
              close_long_basis_pct, close_short_basis_pct, close_basis_diff_pct,
              expected_net_funding_pct, expected_basis_change_pct, expected_ev_pct,
              score, decision, realized_funding_pct, realized_basis_change_pct,
              estimated_cost_pct, realized_pnl_pct, max_adverse_ev_pct, exit_reason,
              payload, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
              status = excluded.status,
              closed_at = excluded.closed_at,
              close_long_basis_pct = excluded.close_long_basis_pct,
              close_short_basis_pct = excluded.close_short_basis_pct,
              close_basis_diff_pct = excluded.close_basis_diff_pct,
              realized_funding_pct = excluded.realized_funding_pct,
              realized_basis_change_pct = excluded.realized_basis_change_pct,
              realized_pnl_pct = excluded.realized_pnl_pct,
              max_adverse_ev_pct = excluded.max_adverse_ev_pct,
              exit_reason = excluded.exit_reason,
              payload = excluded.payload,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                trade.id,
                trade.status,
                trade.symbol,
                trade.long_exchange,
                trade.short_exchange,
                trade.opened_at.isoformat(),
                _dt(trade.closed_at),
                trade.open_long_basis_pct,
                trade.open_short_basis_pct,
                trade.open_basis_diff_pct,
                trade.close_long_basis_pct,
                trade.close_short_basis_pct,
                trade.close_basis_diff_pct,
                trade.expected_net_funding_pct,
                trade.expected_basis_change_pct,
                trade.expected_ev_pct,
                trade.score,
                trade.decision,
                trade.realized_funding_pct,
                trade.realized_basis_change_pct,
                trade.estimated_cost_pct,
                trade.realized_pnl_pct,
                trade.max_adverse_ev_pct,
                trade.exit_reason,
                trade.model_dump_json(),
            ),
        )
        await self.db.commit()
        return trade

    async def list_paper_trades(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[FundingResearchPaperTrade]:
        if status is None:
            cursor = await self.db.execute(
                """
                SELECT payload FROM funding_research_paper_trades
                ORDER BY opened_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        else:
            cursor = await self.db.execute(
                """
                SELECT payload FROM funding_research_paper_trades
                WHERE status = ?
                ORDER BY opened_at DESC
                LIMIT ?
                """,
                (status, limit),
            )
        rows = await cursor.fetchall()
        return [FundingResearchPaperTrade.model_validate_json(row["payload"]) for row in rows]
