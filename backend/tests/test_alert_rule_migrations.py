import json

import pytest

from app.db.database import connect_database
from app.db.schema import initialize_schema


@pytest.mark.asyncio
async def test_alert_rule_migration_allows_large_wide_and_mark_deviation_labels() -> None:
    db = await connect_database(":memory:")
    try:
        await db.executescript(
            """
            CREATE TABLE alert_rules (
              id TEXT PRIMARY KEY,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        payload = {
            "id": "rule-1",
            "name": "legacy",
            "excluded_risk_labels": [
                "LOW_VOLUME",
                "HUGE_SPREAD_VERIFY",
                "WIDE_SPREAD",
                "MARK_INDEX_DEVIATION",
                "MISSING_FUNDING",
            ],
        }
        await db.execute(
            "INSERT INTO alert_rules (id, payload) VALUES (?, ?)",
            ("rule-1", json.dumps(payload)),
        )
        await db.commit()

        await initialize_schema(db)

        row = await (
            await db.execute("SELECT payload FROM alert_rules WHERE id = ?", ("rule-1",))
        ).fetchone()
        labels = json.loads(row["payload"])["excluded_risk_labels"]

        assert labels == ["LOW_VOLUME", "MISSING_FUNDING"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_alert_template_settings_preserve_card_condition_suppression() -> None:
    db = await connect_database(":memory:")
    try:
        await db.executescript(
            """
            CREATE TABLE app_settings (
              key TEXT PRIMARY KEY,
              payload TEXT NOT NULL
            );
            """
        )
        await db.execute(
            "INSERT INTO app_settings (key, payload) VALUES (?, ?)",
            (
                "alert_message_template",
                json.dumps(
                    {
                        "include_trigger_summary": True,
                        "suppress_when_card_conditions_fail": True,
                    }
                ),
            ),
        )
        await db.commit()

        await initialize_schema(db)

        row = await (
            await db.execute("SELECT payload FROM app_settings WHERE key = ?", ("alert_message_template",))
        ).fetchone()
        payload = json.loads(row["payload"])

        assert payload["suppress_when_card_conditions_fail"] is True
    finally:
        await db.close()
