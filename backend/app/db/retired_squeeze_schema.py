"""Legacy table/column definitions only; no service startup or data copying."""

import aiosqlite


async def initialize_squeeze_schema(db: aiosqlite.Connection) -> None:
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS squeeze_market_identity (
          market_key TEXT PRIMARY KEY, exchange TEXT NOT NULL, market_type TEXT NOT NULL,
          raw_symbol TEXT NOT NULL, dex TEXT NOT NULL DEFAULT '', base_asset TEXT,
          quote_asset TEXT, contract_size REAL, price_multiplier REAL,
          verification_source TEXT NOT NULL, verification_status TEXT NOT NULL,
          verified_at TEXT NOT NULL, metadata_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS squeeze_scan_runs (
          market_key TEXT NOT NULL, bucket_at TEXT NOT NULL, requested_from TEXT NOT NULL,
          requested_to TEXT NOT NULL, received_at TEXT NOT NULL, candle_count INTEGER NOT NULL,
          positioning_count INTEGER NOT NULL, result_status TEXT NOT NULL, error TEXT,
          PRIMARY KEY (market_key, bucket_at)
        );
        CREATE TABLE IF NOT EXISTS squeeze_hourly_candles (
          market_key TEXT NOT NULL, event_time TEXT NOT NULL, close REAL NOT NULL,
          quote_volume REAL NOT NULL, received_at TEXT NOT NULL, available_at TEXT NOT NULL,
          source TEXT NOT NULL, PRIMARY KEY (market_key, event_time)
        );
        CREATE TABLE IF NOT EXISTS squeeze_positioning_samples (
          market_key TEXT NOT NULL, event_time TEXT NOT NULL, raw_open_interest REAL NOT NULL,
          raw_unit TEXT NOT NULL, open_interest_usdt REAL, account_ratio REAL,
          account_ratio_event_time TEXT,
          received_at TEXT NOT NULL, available_at TEXT NOT NULL, source TEXT NOT NULL,
          sampling_period_seconds INTEGER NOT NULL,
          PRIMARY KEY (market_key, source, event_time, sampling_period_seconds)
        );
        CREATE TABLE IF NOT EXISTS squeeze_feature_results (
          market_key TEXT NOT NULL, bucket_at TEXT NOT NULL, calculated_at TEXT NOT NULL,
          status TEXT NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY (market_key, bucket_at)
        );
        CREATE TABLE IF NOT EXISTS squeeze_watch_events (
          id TEXT PRIMARY KEY, market_key TEXT NOT NULL, rule_version TEXT NOT NULL,
          first_bucket_at TEXT NOT NULL, last_bucket_at TEXT NOT NULL, stage TEXT NOT NULL,
          expires_at TEXT NOT NULL, cooldown_until TEXT NOT NULL, features_json TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_squeeze_watch_active
          ON squeeze_watch_events(market_key, expires_at DESC);
        CREATE TABLE IF NOT EXISTS squeeze_liquidation_updates (
          identity TEXT PRIMARY KEY, market_key TEXT NOT NULL, event_time TEXT NOT NULL,
          received_at TEXT NOT NULL, side TEXT NOT NULL, cumulative_filled_qty REAL NOT NULL,
          observed_liquidation_notional REAL NOT NULL, source TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_squeeze_liquidation_time
          ON squeeze_liquidation_updates(market_key, event_time DESC);
        CREATE TABLE IF NOT EXISTS squeeze_liquidation_coverage (
          id INTEGER PRIMARY KEY CHECK (id = 1), state TEXT NOT NULL,
          updated_at TEXT NOT NULL, last_message_at TEXT, gap_started_at TEXT,
          last_error TEXT
        );
        CREATE TABLE IF NOT EXISTS squeeze_liquidation_gaps (
          id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
          ended_at TEXT, reason TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS squeeze_scan_state (
          id INTEGER PRIMARY KEY CHECK (id = 1), last_attempt_at TEXT,
          last_success_at TEXT, last_bucket_at TEXT, last_error TEXT,
          verified_symbols_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS squeeze_discovery_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          bucket_at TEXT NOT NULL, selected_at TEXT NOT NULL, mode TEXT NOT NULL,
          rule_version TEXT NOT NULL, eligible_count INTEGER NOT NULL,
          screened_count INTEGER NOT NULL, selected_json TEXT NOT NULL,
          screened_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_squeeze_discovery_recent
          ON squeeze_discovery_runs(selected_at DESC);
    """)
    cursor = await db.execute("PRAGMA table_info(squeeze_positioning_samples)")
    columns = {row["name"] for row in await cursor.fetchall()}
    if "account_ratio_event_time" not in columns:
        await db.execute(
            "ALTER TABLE squeeze_positioning_samples ADD COLUMN account_ratio_event_time TEXT"
        )
    await initialize_route_schema(db)
    await db.commit()


async def initialize_route_schema(db: aiosqlite.Connection) -> None:
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS squeeze_route_state (
          route_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS squeeze_route_latest (
          route_id TEXT PRIMARY KEY, evaluated_at TEXT NOT NULL,
          evaluation_json TEXT NOT NULL, inputs_json TEXT
        );
        CREATE TABLE IF NOT EXISTS squeeze_route_events (
          id TEXT PRIMARY KEY, route_id TEXT NOT NULL, phase TEXT NOT NULL,
          occurred_at TEXT NOT NULL, rule_version TEXT NOT NULL,
          evaluation_json TEXT NOT NULL, inputs_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_squeeze_route_events_time
          ON squeeze_route_events(occurred_at DESC);
        CREATE TABLE IF NOT EXISTS squeeze_route_worker_state (
          id INTEGER PRIMARY KEY CHECK(id = 1), last_attempt_at TEXT,
          last_success_at TEXT, last_error TEXT, last_route_id TEXT,
          storage_failure_count INTEGER NOT NULL DEFAULT 0,
          last_storage_error_at TEXT, last_storage_error TEXT,
          dropped_scan_count INTEGER NOT NULL DEFAULT 0, last_drop_at TEXT
        );
        CREATE TABLE IF NOT EXISTS squeeze_route_meta (
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
    """)
    cursor = await db.execute("PRAGMA table_info(squeeze_route_worker_state)")
    columns = {row["name"] for row in await cursor.fetchall()}
    additions = {
        "storage_failure_count": "INTEGER NOT NULL DEFAULT 0",
        "last_storage_error_at": "TEXT",
        "last_storage_error": "TEXT",
        "dropped_scan_count": "INTEGER NOT NULL DEFAULT 0",
        "last_drop_at": "TEXT",
    }
    for name, ddl in additions.items():
        if name not in columns:
            await db.execute(f"ALTER TABLE squeeze_route_worker_state ADD COLUMN {name} {ddl}")
    cursor = await db.execute("PRAGMA table_info(squeeze_route_latest)")
    latest_columns = {row["name"] for row in await cursor.fetchall()}
    if "inputs_json" not in latest_columns:
        await db.execute("ALTER TABLE squeeze_route_latest ADD COLUMN inputs_json TEXT")
    await db.commit()


async def initialize_paper_schema(db: aiosqlite.Connection) -> None:
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS squeeze_paper_run (
          id INTEGER PRIMARY KEY CHECK(id = 1),
          started_at TEXT NOT NULL, rule_version TEXT NOT NULL,
          settings_json TEXT NOT NULL, last_processed_at TEXT,
          last_event_at TEXT NOT NULL, last_event_id TEXT NOT NULL DEFAULT '',
          last_success_at TEXT, last_error TEXT,
          continuous_started_at TEXT,
          coverage_gap_count INTEGER NOT NULL DEFAULT 0,
          coverage_gap_open INTEGER NOT NULL DEFAULT 0,
          last_coverage_gap_at TEXT,
          coverage_tracking_version INTEGER NOT NULL DEFAULT 0
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
    cursor = await db.execute("PRAGMA table_info(squeeze_paper_run)")
    run_columns = {row["name"] for row in await cursor.fetchall()}
    for name, definition in {
        "continuous_started_at": "TEXT",
        "coverage_gap_count": "INTEGER NOT NULL DEFAULT 0",
        "coverage_gap_open": "INTEGER NOT NULL DEFAULT 0",
        "last_coverage_gap_at": "TEXT",
        "coverage_tracking_version": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if name not in run_columns:
            await db.execute(f"ALTER TABLE squeeze_paper_run ADD COLUMN {name} {definition}")
    cursor = await db.execute("PRAGMA table_info(squeeze_paper_trades)")
    columns = {row["name"] for row in await cursor.fetchall()}
    if "funding_gap_at" not in columns:
        await db.execute("ALTER TABLE squeeze_paper_trades ADD COLUMN funding_gap_at TEXT")
    await db.commit()
