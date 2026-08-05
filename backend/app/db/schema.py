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


async def _ensure_second_level_sample_columns(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA table_info(second_level_market_samples)")
    rows = await cursor.fetchall()
    existing = {row["name"] for row in rows}
    columns: dict[str, str] = {
        "spot_bid_size": "REAL",
        "spot_ask_size": "REAL",
        "future_bid_size": "REAL",
        "future_ask_size": "REAL",
    }
    for name, ddl in columns.items():
        if name in existing:
            continue
        await db.execute(f"ALTER TABLE second_level_market_samples ADD COLUMN {name} {ddl}")


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

        CREATE TABLE IF NOT EXISTS second_level_market_samples (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          observed_at TEXT NOT NULL,
          exchange TEXT NOT NULL,
          symbol TEXT NOT NULL,
          status TEXT NOT NULL,
          spot_bid REAL,
          spot_ask REAL,
          spot_bid_size REAL,
          spot_ask_size REAL,
          spot_mid REAL,
          spot_last REAL,
          future_bid REAL,
          future_ask REAL,
          future_bid_size REAL,
          future_ask_size REAL,
          future_mid REAL,
          future_last REAL,
          mark_price REAL,
          index_price REAL,
          mark_premium_pct REAL,
          mid_premium_pct REAL,
          funding_rate_pct REAL,
          raw_spot_symbol TEXT,
          raw_future_symbol TEXT,
          latency_ms REAL,
          error TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_second_level_samples_symbol_time
          ON second_level_market_samples(symbol, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_second_level_samples_exchange_time
          ON second_level_market_samples(exchange, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_second_level_samples_pair_time
          ON second_level_market_samples(symbol, exchange, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_second_level_samples_time
          ON second_level_market_samples(observed_at DESC);

        CREATE TABLE IF NOT EXISTS second_level_index_component_samples (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          observed_at TEXT NOT NULL,
          target_exchange TEXT NOT NULL,
          symbol TEXT NOT NULL,
          component_source TEXT NOT NULL,
          component_symbol TEXT NOT NULL,
          weight_pct REAL,
          component_price REAL,
          contribution_price REAL,
          official_index_price REAL,
          reconstructed_index_price REAL,
          mark_price REAL,
          future_mid REAL,
          mark_premium_pct REAL,
          funding_rate_pct REAL,
          latency_ms REAL,
          error TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_second_level_component_samples_symbol_time
          ON second_level_index_component_samples(symbol, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_second_level_component_samples_target_time
          ON second_level_index_component_samples(target_exchange, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_second_level_component_samples_component_time
          ON second_level_index_component_samples(
            target_exchange, symbol, component_source, component_symbol, observed_at DESC
          );
        CREATE INDEX IF NOT EXISTS idx_second_level_component_samples_time
          ON second_level_index_component_samples(observed_at DESC);

        CREATE TABLE IF NOT EXISTS new_listing_watchlist (
          id TEXT PRIMARY KEY,
          payload TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS new_listing_spread_samples (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          watch_id TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          symbol TEXT NOT NULL,
          market_type TEXT NOT NULL,
          buy_exchange TEXT NOT NULL,
          sell_exchange TEXT NOT NULL,
          buy_bid REAL,
          buy_ask REAL,
          buy_bid_size REAL,
          buy_ask_size REAL,
          sell_bid REAL,
          sell_ask REAL,
          sell_bid_size REAL,
          sell_ask_size REAL,
          buy_price REAL NOT NULL,
          sell_price REAL NOT NULL,
          raw_spread_pct REAL NOT NULL,
          net_spread_pct REAL NOT NULL,
          executable_notional_usdt REAL,
          buy_latency_ms REAL,
          sell_latency_ms REAL,
          alert_level TEXT NOT NULL,
          alert_triggered INTEGER NOT NULL DEFAULT 0,
          no_alert_reason TEXT,
          risk_labels_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY (watch_id) REFERENCES new_listing_watchlist(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_new_listing_samples_symbol_time
          ON new_listing_spread_samples(symbol, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_new_listing_samples_watch_time
          ON new_listing_spread_samples(watch_id, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_new_listing_samples_pair_time
          ON new_listing_spread_samples(symbol, buy_exchange, sell_exchange, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_new_listing_samples_time
          ON new_listing_spread_samples(observed_at DESC);

        CREATE TABLE IF NOT EXISTS new_listing_alert_events (
          id TEXT PRIMARY KEY,
          watch_id TEXT NOT NULL,
          symbol TEXT NOT NULL,
          market_type TEXT NOT NULL,
          level TEXT NOT NULL,
          buy_exchange TEXT NOT NULL,
          sell_exchange TEXT NOT NULL,
          net_spread_pct REAL NOT NULL,
          raw_spread_pct REAL NOT NULL,
          executable_notional_usdt REAL,
          message TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY (watch_id) REFERENCES new_listing_watchlist(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_new_listing_events_symbol_time
          ON new_listing_alert_events(symbol, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_new_listing_events_watch_time
          ON new_listing_alert_events(watch_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_new_listing_events_time
          ON new_listing_alert_events(created_at DESC);

        CREATE TABLE IF NOT EXISTS pair_spread_funding_watchlist (
          pair_key TEXT PRIMARY KEY,
          leg1_exchange TEXT NOT NULL,
          leg1_market_type TEXT NOT NULL,
          leg1_symbol TEXT NOT NULL,
          leg2_exchange TEXT NOT NULL,
          leg2_market_type TEXT NOT NULL,
          leg2_symbol TEXT NOT NULL,
          leg2_multiplier REAL NOT NULL,
          interval_seconds INTEGER NOT NULL DEFAULT 60,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS pair_spread_funding_samples (
          pair_key TEXT NOT NULL,
          bucket_at TEXT NOT NULL,
          left_rate_pct REAL,
          right_rate_pct REAL,
          net_rate_pct REAL,
          source TEXT NOT NULL DEFAULT 'minute_record',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (pair_key, bucket_at),
          FOREIGN KEY (pair_key) REFERENCES pair_spread_funding_watchlist(pair_key) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_pair_spread_funding_samples_pair_time
          ON pair_spread_funding_samples(pair_key, bucket_at DESC);
        """
    )
    await _ensure_opportunity_history_columns(db)
    await _ensure_exchange_announcement_columns(db)
    await _ensure_second_level_sample_columns(db)
    await _migrate_alert_rule_excluded_labels(db)
    await db.commit()
