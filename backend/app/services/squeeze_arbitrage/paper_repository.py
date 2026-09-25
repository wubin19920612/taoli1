from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime

import aiosqlite

from .paper_models import PaperAccount, PaperRun, PaperSettings, PaperTrade


async def initialize_paper_schema(db: aiosqlite.Connection) -> None:
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS squeeze_paper_run (
          id INTEGER PRIMARY KEY CHECK(id = 1),
          started_at TEXT NOT NULL, rule_version TEXT NOT NULL,
          settings_json TEXT NOT NULL, last_processed_at TEXT,
          last_event_at TEXT NOT NULL, last_event_id TEXT NOT NULL DEFAULT '',
          last_success_at TEXT, last_error TEXT
        );
        CREATE TABLE IF NOT EXISTS squeeze_paper_accounts (
          exchange TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS squeeze_paper_trades (
          id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
          route_id TEXT NOT NULL, status TEXT NOT NULL,
          signal_at TEXT NOT NULL, opened_at TEXT, closed_at TEXT,
          funding_gap_at TEXT, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_squeeze_paper_trades_time
          ON squeeze_paper_trades(signal_at DESC,id DESC);
        CREATE INDEX IF NOT EXISTS idx_squeeze_paper_trades_status
          ON squeeze_paper_trades(status,signal_at DESC);
    """)
    cursor = await db.execute("PRAGMA table_info(squeeze_paper_trades)")
    columns = {row["name"] for row in await cursor.fetchall()}
    if "funding_gap_at" not in columns:
        await db.execute("ALTER TABLE squeeze_paper_trades ADD COLUMN funding_gap_at TEXT")
    await db.commit()


class SqueezePaperRepository:
    def __init__(self, db: aiosqlite.Connection, lock: asyncio.Lock) -> None:
        self.db = db
        self._lock = lock

    async def get_or_create_run(
        self, settings: PaperSettings, now: datetime, exchanges: tuple[str, ...],
    ) -> PaperRun:
        now = now.astimezone(UTC)
        async with self._lock:
            cursor = await self.db.execute("SELECT * FROM squeeze_paper_run WHERE id=1")
            row = await cursor.fetchone()
            if row is None:
                try:
                    await self.db.execute(
                        """INSERT INTO squeeze_paper_run
                           (id,started_at,rule_version,settings_json,last_event_at)
                           VALUES (1,?,?,?,?)""",
                        (now.isoformat(), settings.rule_version,
                         settings.model_dump_json(), now.isoformat()),
                    )
                    for exchange in exchanges:
                        account = PaperAccount(
                            exchange=exchange,
                            initial_balance=settings.initial_balance_per_exchange,
                            cash_balance=settings.initial_balance_per_exchange,
                            updated_at=now,
                        )
                        await self.db.execute(
                            """INSERT INTO squeeze_paper_accounts
                               (exchange,payload_json,updated_at) VALUES (?,?,?)""",
                            (exchange, account.model_dump_json(), now.isoformat()),
                        )
                    await self.db.commit()
                except BaseException:
                    await self.db.rollback()
                    raise
                return PaperRun(started_at=now, settings=settings, last_event_at=now)
            if row["rule_version"] != settings.rule_version:
                raise RuntimeError("paper rule version changed; existing run needs explicit review")
            return self._run_from_row(row)

    @staticmethod
    def _run_from_row(row: aiosqlite.Row) -> PaperRun:
        return PaperRun(
            started_at=datetime.fromisoformat(row["started_at"]),
            settings=PaperSettings.model_validate_json(row["settings_json"]),
            last_processed_at=datetime.fromisoformat(row["last_processed_at"])
            if row["last_processed_at"] else None,
            last_event_at=datetime.fromisoformat(row["last_event_at"]),
            last_event_id=row["last_event_id"],
            last_success_at=datetime.fromisoformat(row["last_success_at"])
            if row["last_success_at"] else None,
            last_error=row["last_error"],
        )

    async def load_run(self) -> PaperRun | None:
        async with self._lock:
            cursor = await self.db.execute("SELECT * FROM squeeze_paper_run WHERE id=1")
            row = await cursor.fetchone()
        return self._run_from_row(row) if row else None

    async def load_accounts(self) -> dict[str, PaperAccount]:
        async with self._lock:
            cursor = await self.db.execute(
                "SELECT payload_json FROM squeeze_paper_accounts ORDER BY exchange"
            )
            rows = await cursor.fetchall()
        accounts = [PaperAccount.model_validate_json(row["payload_json"]) for row in rows]
        return {account.exchange: account for account in accounts}

    async def list_worker_trades(self) -> list[PaperTrade]:
        async with self._lock:
            cursor = await self.db.execute(
                """SELECT payload_json FROM squeeze_paper_trades
                   WHERE status IN ('pending_entry','recovering','open',
                                    'pending_exit','unresolved')
                      OR funding_gap_at IS NOT NULL
                   ORDER BY signal_at,id LIMIT 10001"""
            )
            rows = await cursor.fetchall()
        if len(rows) > 10000:
            raise RuntimeError("paper active trade limit exceeded")
        return [PaperTrade.model_validate_json(row["payload_json"]) for row in rows]

    async def list_report_trades(self) -> list[PaperTrade]:
        async with self._lock:
            cursor = await self.db.execute(
                "SELECT payload_json FROM squeeze_paper_trades ORDER BY signal_at,id"
            )
            rows = await cursor.fetchall()
        return [PaperTrade.model_validate_json(row["payload_json"]) for row in rows]

    async def status_counts(self) -> dict[str, int]:
        async with self._lock:
            cursor = await self.db.execute(
                "SELECT status,COUNT(*) AS count FROM squeeze_paper_trades GROUP BY status"
            )
            rows = await cursor.fetchall()
        return {row["status"]: row["count"] for row in rows}

    async def list_trades(
        self, *, limit: int = 50, offset: int = 0, active_only: bool = False,
    ) -> list[PaperTrade]:
        where = (
            "WHERE status IN ('pending_entry','recovering','open','pending_exit','unresolved')"
            if active_only else ""
        )
        async with self._lock:
            cursor = await self.db.execute(
                f"""SELECT payload_json FROM squeeze_paper_trades {where}
                    ORDER BY signal_at DESC,id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            )
            rows = await cursor.fetchall()
        return [PaperTrade.model_validate_json(row["payload_json"]) for row in rows]

    async def save_snapshot(
        self, run: PaperRun, accounts: dict[str, PaperAccount], trades: list[PaperTrade],
        *, funding_only: bool = False, ledger_at: datetime | None = None,
    ) -> bool:
        if run.last_processed_at is None and not funding_only:
            raise ValueError("paper snapshot time is required")
        if funding_only and ledger_at is None:
            raise ValueError("funding-only snapshot time is required")
        async with self._lock:
            try:
                cursor = await self.db.execute(
                    "SELECT last_processed_at FROM squeeze_paper_run WHERE id=1"
                )
                row = await cursor.fetchone()
                if row is None:
                    raise RuntimeError("paper run has not been initialized")
                if not funding_only and row["last_processed_at"] and (
                    datetime.fromisoformat(row["last_processed_at"]) >= run.last_processed_at
                ):
                    return False
                for account in accounts.values():
                    await self.db.execute(
                        """INSERT INTO squeeze_paper_accounts
                           (exchange,payload_json,updated_at) VALUES (?,?,?)
                           ON CONFLICT(exchange) DO UPDATE SET
                           payload_json=excluded.payload_json,
                           updated_at=excluded.updated_at""",
                        (account.exchange, account.model_dump_json(),
                         account.updated_at.isoformat()),
                    )
                for trade in trades:
                    await self.db.execute(
                        """INSERT INTO squeeze_paper_trades
                           (id,event_id,route_id,status,signal_at,opened_at,closed_at,
                            funding_gap_at,payload_json,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(id) DO UPDATE SET
                           status=excluded.status,opened_at=excluded.opened_at,
                           closed_at=excluded.closed_at,
                           funding_gap_at=excluded.funding_gap_at,
                           payload_json=excluded.payload_json,
                           updated_at=excluded.updated_at""",
                        (trade.id, trade.event_id, trade.route_id, trade.status,
                         trade.signal_at.isoformat(),
                         trade.opened_at.isoformat() if trade.opened_at else None,
                         trade.closed_at.isoformat() if trade.closed_at else None,
                         trade.funding_gap_at.isoformat() if trade.funding_gap_at else None,
                         trade.model_dump_json(),
                         (ledger_at if funding_only else run.last_processed_at).isoformat()),
                    )
                if not funding_only:
                    await self.db.execute(
                        """UPDATE squeeze_paper_run SET last_processed_at=?,last_event_at=?,
                           last_event_id=?,last_success_at=?,last_error=? WHERE id=1""",
                        (run.last_processed_at.isoformat(), run.last_event_at.isoformat(),
                         run.last_event_id,
                         run.last_success_at.isoformat() if run.last_success_at else None,
                         run.last_error),
                    )
                await self.db.commit()
                return True
            except BaseException as exc:
                await self.db.rollback()
                if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower():
                    raise
                raise

    async def record_error(self, error: str) -> None:
        async with self._lock:
            await self.db.execute(
                "UPDATE squeeze_paper_run SET last_error=? WHERE id=1", (error[:500],)
            )
            await self.db.commit()
