import pytest

from app.services.funding_research import live_run


@pytest.mark.asyncio
async def test_run_loop_respects_max_iterations(monkeypatch) -> None:
    calls: list[tuple[str, bool, float]] = []

    async def fake_run_once(
        *,
        database_path: str,
        manage_paper_trades: bool,
        snapshot_retention_hours: float,
    ) -> None:
        calls.append((database_path, manage_paper_trades, snapshot_retention_hours))

    monkeypatch.setattr(live_run, "run_once", fake_run_once)

    await live_run.run_loop(
        database_path=":memory:",
        manage_paper_trades=True,
        snapshot_retention_hours=12,
        interval_seconds=0,
        max_iterations=3,
    )

    assert calls == [
        (":memory:", True, 12),
        (":memory:", True, 12),
        (":memory:", True, 12),
    ]
