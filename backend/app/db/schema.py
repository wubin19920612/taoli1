import json

import aiosqlite


async def _migrate_alert_rule_excluded_labels(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("SELECT id, payload FROM alert_rules")
    rows = await cursor.fetchall()
    for row in rows:
        payload = json.loads(row["payload"])
        labels = payload.get("excluded_risk_labels")
        if not isinstance(labels, list) or "MARK_INDEX_DEVIATION" not in labels:
            continue
        payload["excluded_risk_labels"] = [
            label for label in labels if label != "MARK_INDEX_DEVIATION"
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
        """
    )
    await _ensure_opportunity_history_columns(db)
    await _migrate_alert_rule_excluded_labels(db)
    await db.commit()
