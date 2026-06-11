import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI

from app.main import _run_alert_loop
from app.models.alert import AlertEvent, AlertRule
from app.models.astro import AstroAlertActionResult
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.orderbook import DepthValidationResult
from app.models.settings import AlertMessageTemplateSettings, AstroCardSettings, LivePilotSettings, RiskSettings
from app.services.alert_engine import AlertMatch
from app.services.snapshot_store import SnapshotStore


def opportunity() -> Opportunity:
    return Opportunity(
        id="opp-1",
        type=OpportunityType.FF,
        symbol="BTCUSDT",
        buy_exchange="binance",
        buy_market_type=MarketType.FUTURE,
        sell_exchange="okx",
        sell_market_type=MarketType.FUTURE,
        open_spread_pct=0.8,
        close_spread_pct=0.5,
        fee_adjusted_open_pct=0.6,
        spread_width_pct=0.3,
        buy_bid=99,
        buy_ask=100,
        sell_bid=100.8,
        sell_ask=101,
        buy_volume_24h_usdt=10_000_000,
        sell_volume_24h_usdt=12_000_000,
        funding_rate_buy_pct=0.01,
        funding_rate_sell_pct=-0.02,
        net_funding_pct=-0.03,
        mark_index_diff_buy_pct=0.01,
        mark_index_diff_sell_pct=0.02,
        risk_labels=[],
        last_seen_at=datetime.now(UTC),
    )


class FakeRuleRepo:
    def __init__(self, rules: list[AlertRule]):
        self.rules = rules

    async def list(self) -> list[AlertRule]:
        return self.rules


class FakeEventRepo:
    def __init__(self, stop_event: asyncio.Event):
        self.stop_event = stop_event
        self.events: list[AlertEvent] = []

    async def create(self, event: AlertEvent) -> AlertEvent:
        self.events.append(event)
        self.stop_event.set()
        return event


class CountingEventRepo:
    def __init__(self, stop_event: asyncio.Event, target_count: int):
        self.stop_event = stop_event
        self.target_count = target_count
        self.events: list[AlertEvent] = []

    async def create(self, event: AlertEvent) -> AlertEvent:
        self.events.append(event)
        if len(self.events) >= self.target_count:
            self.stop_event.set()
        return event


class FakeSettingsRepo:
    def __init__(
        self,
        astro_card_settings: AstroCardSettings | None = None,
        live_pilot_settings: LivePilotSettings | None = None,
    ):
        self.astro_card_settings = astro_card_settings
        self.live_pilot_settings = live_pilot_settings or LivePilotSettings()

    async def get_risk_settings(self) -> RiskSettings:
        return RiskSettings()

    async def get_alert_message_template(self) -> AlertMessageTemplateSettings:
        return AlertMessageTemplateSettings()

    async def find_astro_card_settings(self) -> AstroCardSettings | None:
        return self.astro_card_settings

    async def get_live_pilot_settings(self) -> LivePilotSettings:
        return self.live_pilot_settings


class FakeAlertEngine:
    def __init__(self, match: AlertMatch):
        self.match = match
        self.seen_opportunity_ids: list[str] = []

    def evaluate(self, opportunities: list[Opportunity], rules: list[AlertRule], **kwargs) -> list[AlertMatch]:
        self.seen_opportunity_ids = [item.id for item in opportunities]
        return [self.match]


class FakeLimitedAlertEngine:
    def __init__(self, matches: list[AlertMatch]):
        self.matches = matches

    def evaluate(self, opportunities: list[Opportunity], rules: list[AlertRule], **kwargs) -> list[AlertMatch]:
        return self.matches[:3]


class RevalidatingSnapshotStore:
    def __init__(self, first: Opportunity, latest: Opportunity):
        self.first = first
        self.latest = latest
        self.calls = 0

    def get_opportunities(self) -> list[Opportunity]:
        self.calls += 1
        return [self.first] if self.calls == 1 else [self.latest]


class FakeFeishuNotifier:
    def __init__(self):
        self.sent_texts: list[str | None] = []

    async def send_alert(self, *args, **kwargs) -> None:
        self.sent_texts.append(kwargs.get("prebuilt_text"))


class FakeAstroAlertService:
    def __init__(self):
        self.card_settings: AstroCardSettings | None = None
        self.live_pilot_settings = LivePilotSettings()
        self.calls: list[str] = []

    async def handle_alert(self, opportunity: Opportunity) -> AstroAlertActionResult:
        self.calls.append(opportunity.id)
        return AstroAlertActionResult(
            enabled=True,
            status="created",
            action="add",
            message="已创建暂停卡片 BTC FF binance->okx，禁开=true",
            pair_name="BTC",
            pair_type="FF",
        )


class FakeOrderBookValidator:
    def __init__(self, result: DepthValidationResult):
        self.result = result
        self.calls: list[tuple[Opportunity, RiskSettings, AstroCardSettings | None, float | None]] = []

    async def validate(
        self,
        opportunity: Opportunity,
        risk_settings: RiskSettings,
        card_settings: AstroCardSettings | None = None,
        override_notional_usdt: float | None = None,
    ) -> DepthValidationResult:
        self.calls.append((opportunity, risk_settings, card_settings, override_notional_usdt))
        return self.result


class FailingAstroAlertService:
    async def handle_alert(self, opportunity: Opportunity) -> AstroAlertActionResult:
        raise RuntimeError("unexpected astro failure")


class BlankExceptionAstroAlertService:
    async def handle_alert(self, opportunity: Opportunity) -> AstroAlertActionResult:
        raise TimeoutError()


@pytest.mark.asyncio
async def test_live_pilot_alert_loop_filters_candidates_by_alert_rules_before_selection() -> None:
    stop_event = asyncio.Event()
    app = FastAPI()
    low_edge = opportunity().model_copy(
        update={
            "id": "low-edge",
            "symbol": "LOWEDGEUSDT",
            "open_spread_pct": 0.40,
            "fee_adjusted_open_pct": 0.30,
        }
    )
    high_edge = opportunity().model_copy(
        update={
            "id": "high-edge",
            "symbol": "HIGHEDGEUSDT",
            "open_spread_pct": 0.70,
            "fee_adjusted_open_pct": 0.60,
            "net_funding_pct": 0.00,
        }
    )
    store = SnapshotStore()
    store.set_opportunities([low_edge, high_edge])
    rule = AlertRule(
        id="rule-1",
        name="threshold",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.5,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
    )
    event_repo = FakeEventRepo(stop_event)
    alert_engine = FakeAlertEngine(AlertMatch(rule, high_edge, []))

    app.state.alert_rule_repo = FakeRuleRepo([rule])
    app.state.alert_event_repo = event_repo
    app.state.settings_repo = FakeSettingsRepo(
        live_pilot_settings=LivePilotSettings(enabled=True, max_symbols=10)
    )
    app.state.snapshot_store = store
    app.state.alert_engine = alert_engine
    app.state.feishu_notifier = FakeFeishuNotifier()
    app.state.astro_alert_service = FakeAstroAlertService()

    await asyncio.wait_for(_run_alert_loop(app, 60, stop_event), timeout=2)

    assert alert_engine.seen_opportunity_ids == ["high-edge"]


@pytest.mark.asyncio
async def test_alert_loop_appends_astro_result_to_feishu_and_event_message() -> None:
    stop_event = asyncio.Event()
    app = FastAPI()
    rule = AlertRule(
        id="rule-1",
        name="FF spread",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
    )
    opp = opportunity()
    store = SnapshotStore()
    store.set_opportunities([opp])
    event_repo = FakeEventRepo(stop_event)
    feishu = FakeFeishuNotifier()

    app.state.alert_rule_repo = FakeRuleRepo([rule])
    app.state.alert_event_repo = event_repo
    app.state.settings_repo = FakeSettingsRepo()
    app.state.snapshot_store = store
    app.state.alert_engine = FakeAlertEngine(AlertMatch(rule, opp, []))
    app.state.feishu_notifier = feishu
    app.state.astro_alert_service = FakeAstroAlertService()

    await asyncio.wait_for(_run_alert_loop(app, 60, stop_event), timeout=2)

    assert "Astro: 已创建暂停卡片 BTC FF binance->okx，禁开=true" in event_repo.events[0].message
    assert feishu.sent_texts[0] is not None
    assert "Astro: 已创建暂停卡片 BTC FF binance->okx，禁开=true" in feishu.sent_texts[0]


@pytest.mark.asyncio
async def test_alert_loop_refreshes_astro_card_settings_before_auto_create() -> None:
    stop_event = asyncio.Event()
    app = FastAPI()
    rule = AlertRule(
        id="rule-1",
        name="FF spread",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
    )
    opp = opportunity()
    store = SnapshotStore()
    store.set_opportunities([opp])
    event_repo = FakeEventRepo(stop_event)
    service = FakeAstroAlertService()

    app.state.settings = type(
        "Settings",
        (),
        {"astro_card_settings": AstroCardSettings(max_trade_usdt=10)},
    )()
    app.state.alert_rule_repo = FakeRuleRepo([rule])
    app.state.alert_event_repo = event_repo
    app.state.settings_repo = FakeSettingsRepo(AstroCardSettings(max_trade_usdt=77))
    app.state.snapshot_store = store
    app.state.alert_engine = FakeAlertEngine(AlertMatch(rule, opp, []))
    app.state.feishu_notifier = FakeFeishuNotifier()
    app.state.astro_alert_service = service

    await asyncio.wait_for(_run_alert_loop(app, 60, stop_event), timeout=2)

    assert service.card_settings is not None
    assert service.card_settings.max_trade_usdt == 77


@pytest.mark.asyncio
async def test_alert_loop_only_sends_three_selected_matches_per_symbol() -> None:
    stop_event = asyncio.Event()
    app = FastAPI()
    rule = AlertRule(
        id="rule-1",
        name="FF spread",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
    )
    matches = [
        AlertMatch(rule, opportunity().model_copy(update={"id": f"opp-{index}"}), [])
        for index in range(5)
    ]
    store = SnapshotStore()
    store.set_opportunities([match.opportunity for match in matches])
    event_repo = CountingEventRepo(stop_event, target_count=3)
    service = FakeAstroAlertService()

    app.state.alert_rule_repo = FakeRuleRepo([rule])
    app.state.alert_event_repo = event_repo
    app.state.settings_repo = FakeSettingsRepo()
    app.state.snapshot_store = store
    app.state.alert_engine = FakeLimitedAlertEngine(matches)
    app.state.feishu_notifier = FakeFeishuNotifier()
    app.state.astro_alert_service = service

    await asyncio.wait_for(_run_alert_loop(app, 60, stop_event), timeout=2)

    assert [event.opportunity_id for event in event_repo.events] == ["opp-0", "opp-1", "opp-2"]
    assert service.calls == ["opp-0", "opp-1", "opp-2"]


@pytest.mark.asyncio
async def test_alert_loop_skips_astro_create_when_latest_signal_collapsed() -> None:
    stop_event = asyncio.Event()
    app = FastAPI()
    rule = AlertRule(
        id="rule-1",
        name="FF spread",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
    )
    original = opportunity()
    collapsed = original.model_copy(
        update={
            "open_spread_pct": 0.10,
            "fee_adjusted_open_pct": -0.05,
            "last_seen_at": datetime.now(UTC),
        }
    )
    event_repo = FakeEventRepo(stop_event)
    service = FakeAstroAlertService()
    feishu = FakeFeishuNotifier()

    app.state.alert_rule_repo = FakeRuleRepo([rule])
    app.state.alert_event_repo = event_repo
    app.state.settings_repo = FakeSettingsRepo()
    app.state.snapshot_store = RevalidatingSnapshotStore(original, collapsed)
    app.state.alert_engine = FakeAlertEngine(AlertMatch(rule, original, []))
    app.state.feishu_notifier = feishu
    app.state.astro_alert_service = service

    await asyncio.wait_for(_run_alert_loop(app, 60, stop_event), timeout=2)

    assert service.calls == []
    assert event_repo.events[0].status == "muted"
    assert "Astro: skipped latest signal validation" in event_repo.events[0].message
    assert feishu.sent_texts == []


@pytest.mark.asyncio
async def test_alert_loop_skips_astro_create_when_order_book_validation_fails() -> None:
    stop_event = asyncio.Event()
    app = FastAPI()
    rule = AlertRule(
        id="rule-1",
        name="FF spread",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
    )
    opp = opportunity()
    store = SnapshotStore()
    store.set_opportunities([opp])
    event_repo = FakeEventRepo(stop_event)
    service = FakeAstroAlertService()
    validator = FakeOrderBookValidator(
        DepthValidationResult(
            passed=False,
            target_notional_usdt=1000,
            buy_filled_usdt=300,
            sell_filled_usdt=1000,
            buy_vwap=100,
            sell_vwap=100.8,
            quoted_open_pct=0.8,
            executable_open_pct=0.2,
            effective_executable_edge_pct=-0.1,
            slippage_loss_pct=0.6,
            blockers=["buy side depth filled 300.00/1000.00 USDT"],
            warnings=[],
        )
    )

    app.state.alert_rule_repo = FakeRuleRepo([rule])
    app.state.alert_event_repo = event_repo
    app.state.settings_repo = FakeSettingsRepo(AstroCardSettings(max_trade_usdt=50))
    app.state.snapshot_store = store
    app.state.alert_engine = FakeAlertEngine(AlertMatch(rule, opp, []))
    app.state.feishu_notifier = FakeFeishuNotifier()
    app.state.astro_alert_service = service
    app.state.orderbook_validator = validator

    await asyncio.wait_for(_run_alert_loop(app, 60, stop_event), timeout=2)

    assert service.calls == []
    assert validator.calls[0][0].id == "opp-1"
    assert validator.calls[0][2] is not None
    assert validator.calls[0][2].max_trade_usdt == 50
    assert "Astro: skipped order book validation" in event_repo.events[0].message
    assert "buy side depth filled" in event_repo.events[0].message


@pytest.mark.asyncio
async def test_alert_loop_uses_live_pilot_notional_for_order_book_validation() -> None:
    stop_event = asyncio.Event()
    app = FastAPI()
    rule = AlertRule(
        id="rule-1",
        name="FF spread",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
    )
    opp = opportunity()
    store = SnapshotStore()
    store.set_opportunities([opp])
    event_repo = FakeEventRepo(stop_event)
    service = FakeAstroAlertService()
    validator = FakeOrderBookValidator(
        DepthValidationResult(
            passed=True,
            target_notional_usdt=100,
            buy_filled_usdt=100,
            sell_filled_usdt=100,
            buy_vwap=100,
            sell_vwap=100.8,
            quoted_open_pct=0.8,
            executable_open_pct=0.8,
            effective_executable_edge_pct=0.5,
            slippage_loss_pct=0,
            blockers=[],
            warnings=[],
        )
    )

    app.state.settings = type(
        "Settings",
        (),
        {"astro_card_settings": AstroCardSettings(max_trade_usdt=10)},
    )()
    app.state.alert_rule_repo = FakeRuleRepo([rule])
    app.state.alert_event_repo = event_repo
    app.state.settings_repo = FakeSettingsRepo(
        AstroCardSettings(max_trade_usdt=10),
        LivePilotSettings(enabled=True, notional_per_symbol_usdt=100),
    )
    app.state.snapshot_store = store
    app.state.alert_engine = FakeAlertEngine(AlertMatch(rule, opp, []))
    app.state.feishu_notifier = FakeFeishuNotifier()
    app.state.astro_alert_service = service
    app.state.orderbook_validator = validator

    await asyncio.wait_for(_run_alert_loop(app, 60, stop_event), timeout=2)

    assert service.calls == ["opp-1"]
    assert service.live_pilot_settings.enabled is True
    assert validator.calls[0][3] == 100


@pytest.mark.asyncio
async def test_alert_loop_keeps_alert_when_astro_service_raises() -> None:
    stop_event = asyncio.Event()
    app = FastAPI()
    rule = AlertRule(
        id="rule-1",
        name="FF spread",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
    )
    opp = opportunity()
    store = SnapshotStore()
    store.set_opportunities([opp])
    event_repo = FakeEventRepo(stop_event)
    feishu = FakeFeishuNotifier()

    app.state.alert_rule_repo = FakeRuleRepo([rule])
    app.state.alert_event_repo = event_repo
    app.state.settings_repo = FakeSettingsRepo()
    app.state.snapshot_store = store
    app.state.alert_engine = FakeAlertEngine(AlertMatch(rule, opp, []))
    app.state.feishu_notifier = feishu
    app.state.astro_alert_service = FailingAstroAlertService()

    await asyncio.wait_for(_run_alert_loop(app, 60, stop_event), timeout=2)

    assert event_repo.events[0].status == "sent"
    assert "Astro: 处理失败，unexpected astro failure" in event_repo.events[0].message
    assert feishu.sent_texts[0] is not None
    assert "Astro: 处理失败，unexpected astro failure" in feishu.sent_texts[0]


@pytest.mark.asyncio
async def test_alert_loop_includes_exception_type_when_astro_error_text_is_blank() -> None:
    stop_event = asyncio.Event()
    app = FastAPI()
    rule = AlertRule(
        id="rule-1",
        name="FF spread",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
    )
    opp = opportunity()
    store = SnapshotStore()
    store.set_opportunities([opp])
    event_repo = FakeEventRepo(stop_event)
    feishu = FakeFeishuNotifier()

    app.state.alert_rule_repo = FakeRuleRepo([rule])
    app.state.alert_event_repo = event_repo
    app.state.settings_repo = FakeSettingsRepo()
    app.state.snapshot_store = store
    app.state.alert_engine = FakeAlertEngine(AlertMatch(rule, opp, []))
    app.state.feishu_notifier = feishu
    app.state.astro_alert_service = BlankExceptionAstroAlertService()

    await asyncio.wait_for(_run_alert_loop(app, 60, stop_event), timeout=2)

    assert event_repo.events[0].status == "sent"
    assert "Astro: 处理失败，TimeoutError" in event_repo.events[0].message
    assert feishu.sent_texts[0] is not None
    assert "Astro: 处理失败，TimeoutError" in feishu.sent_texts[0]
