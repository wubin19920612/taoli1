"""Startup pruning, HTTP guards and shared-resource regression coverage."""
import logging
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.routing import APIRoute

from app import main
from app.core import config
from app.runtime import cache_census
from app.runtime.memory_probe import MemoryProbe
from app.runtime.supervisor import TaskStatus, TaskSupervisor
from app.services import pair_spread_query


FEATURES = {
    "squeeze": (["SqueezeRepository", "SqueezeRouteRepository", "SqueezePaperRepository",
                 "SqueezeMonitor", "SqueezeRouteMonitor", "SqueezePaperMonitor"],
                ["squeeze_repo", "squeeze_route_repo", "squeeze_paper_repo", "squeeze_monitor",
                 "squeeze_route_monitor", "squeeze_paper_monitor"],
                {"squeeze-monitor", "squeeze-route-monitor", "squeeze-paper-monitor"},
                "/api/squeeze-arbitrage/"),
    "negative_basis": (["NegativeBasisMonitor", "NegativeBasisMonitorRepository"],
                       ["negative_basis_monitor"], {"negative-basis-monitor"},
                       "/api/negative-basis-monitor/"),
    "oil_news": (["OilNewsRepository", "OilNewsMonitor", "OilNewsProvider", "OilNewsTranslator"],
                 ["oil_news_repo", "oil_news_monitor"],
                 {"oil-news-loop", "oil-news-translation-loop"}, "/api/oil-news"),
    "opportunity_radar": (["OpportunityRadarAlertEngine"], ["opportunity_radar_alert_engine"],
                          {"opportunity-radar-alert-loop"}, "/api/opportunity-radar/"),
    "symbol_spread": ([], [], set(), "/api/pair-spread/symbol-query"),
    "fat_finger": ([], [], set(), "/api/second-level-sampling/fat-finger-backtest"),
    "gate_twap": (["GateTwapJobManager"], ["gate_twap_manager"], set(), "/api/gate-twap/"),
    "second_level_sampling": (["SecondLevelSampler", "SecondLevelSamplingRepository"],
                              ["second_level_sampler"], set(), "/api/second-level-sampling/"),
    "funding_research": (["FundingResearchRepository"], ["funding_research_repo"],
                         {"funding-research-loop"}, "/api/funding-research/"),
}


def test_configuration_normalizes_validates_and_expands(monkeypatch, caplog):
    monkeypatch.setattr(config, "find_dotenv", lambda **kwargs: "")
    monkeypatch.delenv("DISABLED_FEATURES", raising=False)
    assert config.get_settings.__wrapped__().disabled_features == frozenset()
    monkeypatch.setenv("DISABLED_FEATURES", " Squeeze , oil_news ,, SQUEEZE ")
    assert config.get_settings.__wrapped__().disabled_features == frozenset({"squeeze", "oil_news"})
    monkeypatch.setenv("DISABLED_FEATURES", "second_level_sampling")
    with caplog.at_level(logging.INFO):
        settings = config.get_settings.__wrapped__()
    assert settings.disabled_features == frozenset({"second_level_sampling", "fat_finger"})
    assert "fat_finger" in caplog.text and "second_level_sampling" in caplog.text
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
    initialize = AsyncMock()
    monkeypatch.setattr(main.SecondLevelSampler, "initialize", initialize)
    client = Mock(has_credentials=False)
    client.aclose = AsyncMock()
    monkeypatch.setattr(main, "GateTwapClient", Mock(return_value=client))
    monkeypatch.setattr(pair_spread_query, "_REALTIME_SYMBOL_SPREAD_CACHE", {})
    return started, initialize, client


@pytest.mark.parametrize("disabled", [frozenset(), *[frozenset({key}) for key in FEATURES], frozenset(FEATURES)])
async def test_startup_routes_tasks_and_shared_resources(disabled, startup, monkeypatch, tmp_path):
    started, initialize, gate_client = startup
    effective = disabled | ({"fat_finger"} if "second_level_sampling" in disabled else set())
    for key in effective:
        for constructor in FEATURES[key][0]:
            monkeypatch.setattr(main, constructor, Mock(side_effect=AssertionError(f"constructed {constructor}")))
    settings = config.Settings(
        database_url=f"sqlite:///{tmp_path / 'radar.db'}", disabled_features=disabled,
        squeeze_monitor_enabled=True, squeeze_route_enabled=True, squeeze_paper_enabled=True,
        funding_research_enabled=True, memory_probe_enabled=True, memory_census_enabled=True,
    )
    app = main.create_app(settings=settings, start_collector=True)
    async with app.router.lifespan_context(app):
        assert app.state.account_position_service.providers[0].client is gate_client
        assert (initialize.await_count == 0) == ("second_level_sampling" in effective)
        for key, (_, states, tasks, _) in FEATURES.items():
            for state in states:
                assert (getattr(app.state, state, None) is None) == (key in effective), state
            for task in tasks:
                assert (task in started) == (key not in effective), task
        assert {"market-collector", "alert-loop", "fs-monitor", "memory-sampler"} <= started.keys()
        assert (tmp_path / "radar-squeeze-route.db").exists() == ("squeeze" not in effective)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.get("/api/features")
            assert response.json() == {"disabled": sorted(effective)}
            for route in app.routes:
                if not isinstance(route, APIRoute):
                    continue
                protected = any(route.path.startswith(FEATURES[key][3]) for key in effective)
                protected |= "oil_news" in effective and route.path == "/api/settings/oil-news"
                if not protected:
                    continue
                path = route.path
                for param in route.param_convertors:
                    path = path.replace("{" + param + "}", "test")
                for method in route.methods:
                    response = await client.request(method, path, json={})
                    assert response.status_code == 404, (method, path, response.text)
                    assert response.json() == {"detail": "feature_disabled"}
            for path in ("/api/health", "/api/health/live", "/api/health/detail",
                         "/api/pair-spread/exchanges", "/api/account-positions", "/api/settings/risk"):
                assert (await client.get(path)).status_code == 200, path
            # Keep the real readiness conditions: missing snapshot is still unhealthy.
            assert (await client.get("/api/health/ready")).status_code == 503
            app.state.snapshot_store.mark_published()
            assert (await client.get("/api/health/ready")).status_code == 200
        if disabled == frozenset(FEATURES):
            targets = cache_census.build_census_targets(app.state)
            report = cache_census.run_census(targets)
            assert not report.get("census_errors")
            rows = {row["name"]: row for row in report["cache_census"]}
            for prefix in ("squeeze_monitor.", "squeeze_route_monitor.", "negative_basis_monitor.",
                           "oil_news_monitor.", "gate_twap_manager.", "opportunity_radar_alert_engine."):
                selected = [row for name, row in rows.items() if name.startswith(prefix)]
                assert selected and all(row.get("missing") for row in selected)
            symbol = next(row for name, row in rows.items() if name.endswith("_REALTIME_SYMBOL_SPREAD_CACHE"))
            assert symbol["entries"] == 0
            MemoryProbe(app.state.snapshot_store, app.state.task_supervisor, census_targets=targets).sample()
        assert pair_spread_query._REALTIME_SYMBOL_SPREAD_CACHE == {}
    gate_client.aclose.assert_awaited_once()


@pytest.mark.parametrize("disabled", [frozenset(), frozenset({"funding_research"})])
async def test_existing_funding_toggle_and_public_startup_snapshot(disabled, startup, monkeypatch):
    started, _, _ = startup
    app = main.create_app(settings=config.Settings(
        database_url="sqlite:///:memory:", disabled_features=disabled,
        funding_research_enabled=False, dashboard_password="test-password",
    ), start_collector=True)
    async with app.router.lifespan_context(app):
        assert "funding-research-loop" not in started
        monkeypatch.setenv("DISABLED_FEATURES", "squeeze")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            assert (await client.get("/api/features")).json() == {"disabled": sorted(disabled)}
