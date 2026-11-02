-- Pre-retirement DDL snapshot for temporary database compatibility tests.

CREATE TABLE IF NOT EXISTS astro_card_attempts (
          route_key TEXT PRIMARY KEY,
          pair_name TEXT NOT NULL,
          source TEXT NOT NULL CHECK(source IN ('alert','live_pilot','manual','preadd')),
          state TEXT NOT NULL CHECK(state IN ('pending','created','failed','unknown','reconciled')),
          open_enabled INTEGER NOT NULL CHECK(open_enabled IN (0,1)),
          request_json TEXT NOT NULL,
          result_message TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

CREATE TABLE IF NOT EXISTS app_settings (
          key TEXT PRIMARY KEY,
          payload TEXT NOT NULL
        );

CREATE TABLE IF NOT EXISTS oil_news_items (
          id TEXT PRIMARY KEY,
          fingerprint TEXT NOT NULL UNIQUE,
          external_id TEXT NOT NULL,
          source TEXT NOT NULL,
          source_feed TEXT NOT NULL,
          title TEXT NOT NULL,
          title_zh TEXT,
          url TEXT NOT NULL,
          summary TEXT,
          summary_zh TEXT,
          published_at TEXT NOT NULL,
          fetched_at TEXT NOT NULL,
          categories_json TEXT NOT NULL DEFAULT '[]',
          severity TEXT NOT NULL,
          impact_score INTEGER NOT NULL,
          direction TEXT NOT NULL,
          confidence REAL NOT NULL,
          horizon TEXT NOT NULL,
          rationale_json TEXT NOT NULL DEFAULT '[]',
          risk_note TEXT NOT NULL,
          market_symbol TEXT,
          market_price REAL,
          market_change_1h_pct REAL,
          market_observed_at TEXT,
          alert_status TEXT NOT NULL,
          alerted_at TEXT,
          UNIQUE(source_feed, external_id)
        );

CREATE INDEX IF NOT EXISTS idx_oil_news_items_time
          ON oil_news_items(published_at DESC);

CREATE INDEX IF NOT EXISTS idx_oil_news_items_severity_time
          ON oil_news_items(severity, published_at DESC);

CREATE INDEX IF NOT EXISTS idx_oil_news_items_direction_time
          ON oil_news_items(direction, published_at DESC);

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

CREATE TABLE IF NOT EXISTS negative_basis_watchlist (
          id TEXT PRIMARY KEY,
          payload TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS negative_basis_signal_samples (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          watch_id TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          symbol TEXT NOT NULL,
          spot_exchange TEXT NOT NULL,
          future_exchange TEXT NOT NULL,
          signal_level TEXT NOT NULL,
          score REAL NOT NULL,
          spot_premium_pct REAL,
          spot_price REAL,
          future_price REAL,
          spot_volume_24h_usdt REAL,
          future_volume_24h_usdt REAL,
          open_interest_usdt REAL,
          open_interest_change_pct REAL,
          long_account_pct REAL,
          short_account_pct REAL,
          long_short_ratio REAL,
          funding_rate_pct REAL,
          reasons_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY (watch_id) REFERENCES negative_basis_watchlist(id) ON DELETE CASCADE
        );

CREATE INDEX IF NOT EXISTS idx_negative_basis_samples_symbol_time
          ON negative_basis_signal_samples(symbol, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_negative_basis_samples_watch_time
          ON negative_basis_signal_samples(watch_id, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_negative_basis_samples_level_time
          ON negative_basis_signal_samples(signal_level, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_negative_basis_samples_time
          ON negative_basis_signal_samples(observed_at DESC);

CREATE TABLE IF NOT EXISTS negative_basis_alert_events (
          id TEXT PRIMARY KEY,
          watch_id TEXT NOT NULL,
          symbol TEXT NOT NULL,
          spot_exchange TEXT NOT NULL,
          future_exchange TEXT NOT NULL,
          signal_level TEXT NOT NULL,
          score REAL NOT NULL,
          spot_premium_pct REAL,
          message TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY (watch_id) REFERENCES negative_basis_watchlist(id) ON DELETE CASCADE
        );

CREATE INDEX IF NOT EXISTS idx_negative_basis_events_symbol_time
          ON negative_basis_alert_events(symbol, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_negative_basis_events_watch_time
          ON negative_basis_alert_events(watch_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_negative_basis_events_time
          ON negative_basis_alert_events(created_at DESC);

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

CREATE TABLE IF NOT EXISTS fs_settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fs_snapshots (bucket INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, payload TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fs_events (id INTEGER PRIMARY KEY AUTOINCREMENT, observed_at TEXT NOT NULL,
                symbol TEXT NOT NULL, stage TEXT NOT NULL, reasons TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fs_astro_attempts (symbol TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at TEXT NOT NULL);
