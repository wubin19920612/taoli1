"""Retired configuration compatibility and the retained Gate guard."""
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app import main
from app.core import config
from app.runtime import cache_census
from app.runtime.memory_probe import MemoryProbe
from app.runtime.supervisor import TaskStatus, TaskSupervisor
from app.services import pair_spread_query
from test_route_permissions import api_routes


def test_configuration_normalizes_validates_and_ignores_retired(monkeypatch):
    monkeypatch.setattr(config, "find_dotenv", lambda **kwargs: "")
    monkeypatch.delenv("DISABLED_FEATURES", raising=False)
    assert config.OPTIONAL_FEATURES == frozenset({"gate_twap"})
    assert config.RETIRED_FEATURES == frozenset({
        "squeeze", "negative_basis", "oil_news", "opportunity_radar", "symbol_spread",
        "fat_finger", "second_level_sampling", "funding_research",
    })
    assert config.get_settings.__wrapped__().disabled_features == frozenset()
    for key in config.RETIRED_FEATURES:
        monkeypatch.setenv("DISABLED_FEATURES", f" {key.upper()} ,, {key} ")
        assert config.get_settings.__wrapped__().disabled_features == frozenset()
        assert config.Settings(disabled_features=frozenset({key})).disabled_features == frozenset()
    monkeypatch.setenv("DISABLED_FEATURES", ",".join(config.RETIRED_FEATURES) + ", GATE_TWAP ")
    assert config.get_settings.__wrapped__().disabled_features == frozenset({"gate_twap"})
    monkeypatch.setenv("DISABLED_FEATURES", "squeze")
    with pytest.raises(ValueError, match="squeze"):
        config.get_settings.__wrapped__()
    with pytest.raises(ValueError, match="unknown"):
        config.Settings(disabled_features=frozenset({"unknown"}))


@pytest.fixture
def startup(monkeypatch):
    started = {}

    def start(self, name, factory, *, restart, critical):
        started[name] = factory
        self.statuses[name] = TaskStatus(state="running", critical=critical)

    monkeypatch.setattr(TaskSupervisor, "start", start)
    monkeypatch.setattr(main, "default_exchange_adapters", list)
    client = Mock(has_credentials=False)
    client.aclose = AsyncMock()
    monkeypatch.setattr(main, "GateTwapClient", Mock(return_value=client))
    monkeypatch.setattr(pair_spread_query, "_REALTIME_SYMBOL_SPREAD_CACHE", {})
    return started, client


@pytest.mark.parametrize("disabled", [frozenset(), frozenset({"gate_twap"}),
    config.RETIRED_FEATURES, config.RETIRED_FEATURES | {"gate_twap"}])
async def test_startup_routes_tasks_and_shared_resources(disabled, startup, monkeypatch, tmp_path):
    started, gate_client = startup
    effective = disabled & config.OPTIONAL_FEATURES
    if "gate_twap" in effective:
        monkeypatch.setattr(main, "GateTwapJobManager", Mock(side_effect=AssertionError("constructed Gate manager")))
    settings = config.Settings(dashboard_auth_disabled=True,
        database_url=f"sqlite:///{tmp_path / 'radar.db'}", disabled_features=disabled,
        memory_probe_enabled=True, memory_census_enabled=True,
    )
    app = main.create_app(settings=settings, start_collector=True)
    async with app.router.lifespan_context(app):
        assert app.state.account_position_service.providers[0].client is gate_client
        assert (app.state.gate_twap_manager is None) == ("gate_twap" in effective)
        assert {"market-collector", "alert-loop", "history-recorder", "astro-preadd-loop",
                "announcement-loop", "announcement-enrichment-loop", "memory-sampler"} <= started.keys()
        assert not {"fs-monitor", "squeeze-monitor", "squeeze-route-monitor", "squeeze-paper-monitor",
                    "negative-basis-monitor", "oil-news-loop", "oil-news-translation-loop",
                    "opportunity-radar-alert-loop", "funding-research-loop", "second-level-sampler"} & started.keys()
        for state in ("fs_monitor", "squeeze_repo", "squeeze_monitor", "squeeze_route_repo",
                      "squeeze_route_monitor", "squeeze_paper_repo", "squeeze_paper_monitor",
                      "negative_basis_monitor", "oil_news_repo", "oil_news_monitor",
                      "opportunity_radar_alert_engine", "funding_research_repo", "second_level_sampler"):
            assert not hasattr(app.state, state), state
        assert not (tmp_path / "radar-squeeze-route.db").exists()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            assert (await client.get("/api/features")).json() == {"disabled": sorted(effective)}
            if "gate_twap" in effective:
                guarded = 0
                for route in api_routes(app):
                    if not route.path.startswith("/api/gate-twap/"):
                        continue
                    path = route.path
                    for param in route.param_convertors:
                        path = path.replace("{" + param + "}", "test")
                    for method in route.methods:
                        guarded += 1
                        response = await client.request(method, path, json={})
                        assert response.status_code == 404, (method, path, response.text)
                        assert response.json() == {"detail": "feature_disabled"}
                assert guarded > 0
            for path in ("/api/health", "/api/health/live", "/api/health/detail",
                         "/api/pair-spread/exchanges", "/api/account-positions", "/api/settings/risk"):
                assert (await client.get(path)).status_code == 200, path
            assert (await client.get("/api/health/ready")).status_code == 503
            app.state.snapshot_store.mark_published()
            assert (await client.get("/api/health/ready")).status_code == 200
        targets = cache_census.build_census_targets(app.state)
        report = cache_census.run_census(targets)
        assert not report.get("census_errors")
        rows = {row["name"]: row for row in report["cache_census"]}
        for prefix in ("fs_monitor.", "squeeze_monitor.", "squeeze_route_monitor.", "negative_basis_monitor.",
                       "oil_news_monitor.", "opportunity_radar_alert_engine."):
            assert not any(name.startswith(prefix) for name in rows)
        if "gate_twap" in effective:
            selected = [row for name, row in rows.items() if name.startswith("gate_twap_manager.")]
            assert selected and all(row.get("missing") for row in selected)
        symbol = next(row for name, row in rows.items() if name.endswith("_REALTIME_SYMBOL_SPREAD_CACHE"))
        assert symbol["entries"] == 0
        MemoryProbe(app.state.snapshot_store, app.state.task_supervisor, census_targets=targets).sample()
        assert pair_spread_query._REALTIME_SYMBOL_SPREAD_CACHE == {}
    gate_client.aclose.assert_awaited_once()


@pytest.mark.parametrize("disabled", [frozenset(), frozenset({"gate_twap"}), config.RETIRED_FEATURES])
async def test_public_features_use_startup_snapshot(disabled, startup, monkeypatch):
    app = main.create_app(settings=config.Settings(
        database_url="sqlite:///:memory:", disabled_features=disabled, dashboard_password="test-password",
    ), start_collector=True)
    async with app.router.lifespan_context(app):
        monkeypatch.setenv("DISABLED_FEATURES", "gate_twap")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            assert (await client.get("/api/features")).json() == {"disabled": sorted(disabled & config.OPTIONAL_FEATURES)}
