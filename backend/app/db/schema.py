import json

import aiosqlite


async def _migrate_alert_rule_excluded_labels(db: aiosqlite.Connection) -> None:
    labels_to_allow = {"MARK_INDEX_DEVIATION", "HUGE_SPREAD_VERIFY", "WIDE_SPREAD"}
    cursor = await db.execute("SELECT id, payload FROM alert_rules")
    rows = await cursor.fetchall()
    for row in rows:
        payload = json.loads(row["payload"])
        labels = payload.get("excluded_risk_labels")
        if not isinstance(labels, list) or not labels_to_allow.intersection(labels):
            continue
        payload["excluded_risk_labels"] = [
            label for label in labels if label not in labels_to_allow
        ]
        await db.execute(
            "UPDATE alert_rules SET payload = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(payload), row["id"]),
        )


async def _ensure_opportunity_history_columns(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA table_info(opportunity_history)")
    rows = await cursor.fetchall()
    existing = {row["name"] for row in rows}
    columns: dict[str, str] = {
        "funding_next_rate_buy_scaled": "INTEGER",
        "funding_next_rate_sell_scaled": "INTEGER",
        "net_funding_next_scaled": "INTEGER",
        "buy_funding_interval_hours": "INTEGER",
        "sell_funding_interval_hours": "INTEGER",
        "net_funding_hourly_scaled": "INTEGER",
        "net_funding_daily_scaled": "INTEGER",
        "net_funding_next_hourly_scaled": "INTEGER",
        "net_funding_next_daily_scaled": "INTEGER",
        "funding_next_time_buy": "TEXT",
        "funding_next_time_sell": "TEXT",
    }
    for name, ddl in columns.items():
        if name in existing:
            continue
        await db.execute(f"ALTER TABLE opportunity_history ADD COLUMN {name} {ddl}")


async def _ensure_exchange_announcement_columns(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA table_info(exchange_announcements)")
    rows = await cursor.fetchall()
    existing = {row["name"] for row in rows}
    columns: dict[str, str] = {
        "symbols_json": "TEXT NOT NULL DEFAULT '[]'",
        "market_type": "TEXT",
        "event_time": "TEXT",
        "event_schedule_json": "TEXT NOT NULL DEFAULT '[]'",
        "summary": "TEXT",
        "event_reminder_status": "TEXT NOT NULL DEFAULT 'not_applicable'",
        "event_reminder_sent_at": "TEXT",
    }
    for name, ddl in columns.items():
        if name in existing:
            continue
        await db.execute(f"ALTER TABLE exchange_announcements ADD COLUMN {name} {ddl}")


async def initialize_schema(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS alert_rules (
          id TEXT PRIMARY KEY,
          payload TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS alert_events (
          id TEXT PRIMARY KEY,
          rule_id TEXT NOT NULL,
          opportunity_id TEXT NOT NULL,
          symbol TEXT NOT NULL,
          status TEXT NOT NULL,
          message TEXT NOT NULL,
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS phone_price_alert_rules (
          id TEXT PRIMARY KEY,
          payload TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS phone_price_alert_events (
          id TEXT PRIMARY KEY,
          rule_id TEXT NOT NULL,
          symbol TEXT NOT NULL,
          exchange TEXT NOT NULL,
          market_type TEXT NOT NULL,
          price_field TEXT NOT NULL,
          condition TEXT NOT NULL,
          target_price REAL NOT NULL,
          observed_price REAL NOT NULL,
          status TEXT NOT NULL,
          message TEXT NOT NULL,
          created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_phone_price_alert_events_time
          ON phone_price_alert_events(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_phone_price_alert_events_rule_time
          ON phone_price_alert_events(rule_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS app_settings (
          key TEXT PRIMARY KEY,
          payload TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS opportunity_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          observed_at TEXT NOT NULL,
          opportunity_id TEXT NOT NULL,
          type TEXT NOT NULL,
          symbol TEXT NOT NULL,
          buy_exchange TEXT NOT NULL,
          buy_market_type TEXT NOT NULL,
          sell_exchange TEXT NOT NULL,
          sell_market_type TEXT NOT NULL,
          open_spread_scaled INTEGER NOT NULL,
          close_spread_scaled INTEGER NOT NULL,
          fee_adjusted_open_scaled INTEGER NOT NULL,
          spread_width_scaled INTEGER NOT NULL,
          funding_rate_buy_scaled INTEGER,
          funding_rate_sell_scaled INTEGER,
          funding_next_rate_buy_scaled INTEGER,
          funding_next_rate_sell_scaled INTEGER,
          net_funding_scaled INTEGER,
          net_funding_next_scaled INTEGER,
          buy_funding_interval_hours INTEGER,
          sell_funding_interval_hours INTEGER,
          net_funding_hourly_scaled INTEGER,
          net_funding_daily_scaled INTEGER,
          net_funding_next_hourly_scaled INTEGER,
          net_funding_next_daily_scaled INTEGER,
          funding_next_time_buy TEXT,
          funding_next_time_sell TEXT,
          buy_volume_24h_usdt REAL,
          sell_volume_24h_usdt REAL,
          risk_label_mask INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_opportunity_history_symbol_time
          ON opportunity_history(symbol, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_opportunity_history_opp_time
          ON opportunity_history(opportunity_id, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_opportunity_history_type_time
          ON opportunity_history(type, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_opportunity_history_time
          ON opportunity_history(observed_at DESC);

        CREATE TABLE IF NOT EXISTS pair_monitor_rules (
          id TEXT PRIMARY KEY,
          payload TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pair_monitor_points (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          rule_id TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          bucket_at TEXT NOT NULL,
          leg1_price REAL NOT NULL,
          leg2_price REAL NOT NULL,
          spread_abs REAL NOT NULL,
          spread_pct REAL NOT NULL,
          leg1_funding_rate_pct REAL,
          leg2_funding_rate_pct REAL,
          leg1_funding_next_rate_pct REAL,
          leg2_funding_next_rate_pct REAL,
          leg1_funding_next_time TEXT,
          leg2_funding_next_time TEXT,
          leg1_volume_24h_usdt REAL,
          leg2_volume_24h_usdt REAL,
          leg1_price_field TEXT NOT NULL,
          leg2_price_field TEXT NOT NULL,
          leg1_market_timestamp TEXT,
          leg2_market_timestamp TEXT,
          FOREIGN KEY(rule_id) REFERENCES pair_monitor_rules(id) ON DELETE CASCADE,
          UNIQUE(rule_id, bucket_at)
        );

        CREATE INDEX IF NOT EXISTS idx_pair_monitor_points_rule_time
          ON pair_monitor_points(rule_id, bucket_at DESC);
        CREATE INDEX IF NOT EXISTS idx_pair_monitor_points_time
          ON pair_monitor_points(bucket_at DESC);

        CREATE TABLE IF NOT EXISTS index_component_snapshots (
          exchange TEXT NOT NULL,
          symbol TEXT NOT NULL,
          component_hash TEXT NOT NULL,
          components_json TEXT NOT NULL,
          source TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (exchange, symbol)
        );

        CREATE TABLE IF NOT EXISTS index_component_changes (
          id TEXT PRIMARY KEY,
          exchange TEXT NOT NULL,
          symbol TEXT NOT NULL,
          old_hash TEXT NOT NULL,
          new_hash TEXT NOT NULL,
          old_components_json TEXT NOT NULL,
          new_components_json TEXT NOT NULL,
          added_components_json TEXT NOT NULL,
          removed_components_json TEXT NOT NULL,
          changed_components_json TEXT NOT NULL,
          source TEXT NOT NULL,
          alert_status TEXT NOT NULL,
          created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_index_component_changes_symbol_time
          ON index_component_changes(symbol, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_index_component_changes_exchange_time
          ON index_component_changes(exchange, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_index_component_changes_time
          ON index_component_changes(created_at DESC);

        CREATE TABLE IF NOT EXISTS index_component_watchlist (
          id TEXT PRIMARY KEY,
          symbol TEXT NOT NULL UNIQUE,
          note TEXT,
          created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_index_component_watchlist_symbol
          ON index_component_watchlist(symbol);

        CREATE TABLE IF NOT EXISTS exchange_announcements (
          id TEXT PRIMARY KEY,
          exchange TEXT NOT NULL,
          announcement_id TEXT NOT NULL,
          kind TEXT NOT NULL,
          title TEXT NOT NULL,
          url TEXT NOT NULL,
          source TEXT NOT NULL,
          category TEXT,
          symbols_json TEXT NOT NULL DEFAULT '[]',
          market_type TEXT,
          event_time TEXT,
          event_schedule_json TEXT NOT NULL DEFAULT '[]',
          summary TEXT,
          published_at TEXT NOT NULL,
          fetched_at TEXT NOT NULL,
          alert_status TEXT NOT NULL,
          event_reminder_status TEXT NOT NULL DEFAULT 'not_applicable',
          event_reminder_sent_at TEXT,
          UNIQUE(exchange, source, announcement_id)
        );

        CREATE INDEX IF NOT EXISTS idx_exchange_announcements_time
          ON exchange_announcements(published_at DESC);
        CREATE INDEX IF NOT EXISTS idx_exchange_announcements_exchange_time
          ON exchange_announcements(exchange, published_at DESC);
        CREATE INDEX IF NOT EXISTS idx_exchange_announcements_kind_time
          ON exchange_announcements(kind, published_at DESC);

        CREATE TABLE IF NOT EXISTS announcement_provider_state (
          key TEXT PRIMARY KEY,
          payload TEXT NOT NULL,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS funding_research_market_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          observed_at TEXT NOT NULL,
          exchange TEXT NOT NULL,
          symbol TEXT NOT NULL,
          market_type TEXT NOT NULL,
          bid REAL NOT NULL,
          ask REAL NOT NULL,
          bid_size REAL,
          ask_size REAL,
          volume_24h_usdt REAL,
          funding_rate_pct REAL,
          funding_next_rate_pct REAL,
          funding_interval_hours REAL,
          funding_next_time TEXT,
          mark_price REAL,
          index_price REAL,
          raw_symbol TEXT NOT NULL,
          payload TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_funding_research_market_symbol_time
          ON funding_research_market_snapshots(symbol, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_funding_research_market_exchange_time
          ON funding_research_market_snapshots(exchange, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_funding_research_market_time
          ON funding_research_market_snapshots(observed_at DESC);

        CREATE TABLE IF NOT EXISTS funding_research_opportunity_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          observed_at TEXT NOT NULL,
          symbol TEXT NOT NULL,
          long_exchange TEXT NOT NULL,
          short_exchange TEXT NOT NULL,
          expected_net_funding_pct REAL,
          expected_basis_change_pct REAL NOT NULL,
          estimated_cost_pct REAL NOT NULL,
          risk_buffer_pct REAL NOT NULL,
          ev_pct REAL,
          score REAL NOT NULL,
          decision TEXT NOT NULL,
          basis_alignment TEXT NOT NULL,
          basis_diff_pct REAL,
          long_basis_pct REAL,
          short_basis_pct REAL,
          funding_window_hours REAL NOT NULL,
          next_settlement_time TEXT,
          minutes_to_settlement REAL,
          funding_source TEXT NOT NULL,
          risk_labels_json TEXT NOT NULL,
          reasons_json TEXT NOT NULL,
          payload TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_funding_research_opp_symbol_time
          ON funding_research_opportunity_snapshots(symbol, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_funding_research_opp_decision_time
          ON funding_research_opportunity_snapshots(decision, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_funding_research_opp_pair_time
          ON funding_research_opportunity_snapshots(
            symbol, long_exchange, short_exchange, observed_at DESC
          );
        CREATE INDEX IF NOT EXISTS idx_funding_research_opp_time
          ON funding_research_opportunity_snapshots(observed_at DESC);

        CREATE TABLE IF NOT EXISTS funding_research_paper_trades (
          id TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          symbol TEXT NOT NULL,
          long_exchange TEXT NOT NULL,
          short_exchange TEXT NOT NULL,
          opened_at TEXT NOT NULL,
          closed_at TEXT,
          open_long_basis_pct REAL,
          open_short_basis_pct REAL,
          open_basis_diff_pct REAL,
          close_long_basis_pct REAL,
          close_short_basis_pct REAL,
          close_basis_diff_pct REAL,
          expected_net_funding_pct REAL,
          expected_basis_change_pct REAL NOT NULL,
          expected_ev_pct REAL,
          score REAL NOT NULL,
          decision TEXT NOT NULL,
          realized_funding_pct REAL NOT NULL,
          realized_basis_change_pct REAL NOT NULL,
          estimated_cost_pct REAL NOT NULL,
          realized_pnl_pct REAL,
          max_adverse_ev_pct REAL,
          exit_reason TEXT,
          payload TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_funding_research_paper_status_time
          ON funding_research_paper_trades(status, opened_at DESC);
        CREATE INDEX IF NOT EXISTS idx_funding_research_paper_symbol_time
          ON funding_research_paper_trades(symbol, opened_at DESC);
        CREATE INDEX IF NOT EXISTS idx_funding_research_paper_pair_status
          ON funding_research_paper_trades(symbol, long_exchange, short_exchange, status);
        """
    )
    await _ensure_opportunity_history_columns(db)
    await _ensure_exchange_announcement_columns(db)
    await _migrate_alert_rule_excluded_labels(db)
    await db.commit()
