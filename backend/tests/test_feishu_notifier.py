from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.models.alert import AlertRule, AlertSeverity
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import AlertMessageTemplateSettings
from app.services.alert_messages import build_alert_message
from app.services.feishu import FeishuConfig, FeishuNotifier


def make_opportunity() -> Opportunity:
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
        buy_bid=99.0,
        buy_ask=100.0,
        sell_bid=100.8,
        sell_ask=101.0,
        buy_volume_24h_usdt=10_000_000,
        sell_volume_24h_usdt=12_000_000,
        funding_rate_buy_pct=0.01,
        funding_rate_sell_pct=-0.02,
        funding_next_rate_buy_pct=0.015,
        funding_next_rate_sell_pct=0.025,
        funding_next_time_buy=datetime(2026, 5, 15, 8, 0, tzinfo=UTC),
        funding_next_time_sell=datetime(2026, 5, 15, 8, 0, tzinfo=UTC),
        net_funding_pct=-0.03,
        net_funding_next_pct=0.01,
        buy_funding_interval_hours=8,
        sell_funding_interval_hours=8,
        net_funding_hourly_pct=-0.00375,
        net_funding_daily_pct=-0.09,
        net_funding_next_hourly_pct=0.00125,
        net_funding_next_daily_pct=0.03,
        mark_index_diff_buy_pct=0.01,
        mark_index_diff_sell_pct=0.02,
        risk_labels=["FUNDING_AGAINST"],
        last_seen_at=datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
    )


def test_build_payload_explains_rule_parameters() -> None:
    rule = AlertRule(
        name="FF 价差",
        severity=AlertSeverity.WARNING,
        types=["FF"],
        include_exchanges=["binance"],
        exclude_exchanges=["gate"],
        include_symbols=["BTCUSDT"],
        exclude_symbols=["BADUSDT"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        max_data_age_seconds=600,
        excluded_risk_labels=["LOW_VOLUME"],
        consecutive_hits=3,
        cooldown_seconds=300,
    )
    notifier = FeishuNotifier(FeishuConfig(webhook_url=""))

    observations = [
        SimpleNamespace(
            observed_at=datetime(2026, 5, 15, 1, 59, 44, tzinfo=UTC),
            open_spread_pct=0.72,
            fee_adjusted_open_pct=0.52,
            funding_edge_pct=0.03,
            combined_open_edge_pct=0.55,
            net_funding_pct=-0.03,
            net_funding_next_pct=0.01,
        ),
        SimpleNamespace(
            observed_at=datetime(2026, 5, 15, 1, 59, 52, tzinfo=UTC),
            open_spread_pct=0.80,
            fee_adjusted_open_pct=0.60,
            funding_edge_pct=0.03,
            combined_open_edge_pct=0.63,
            net_funding_pct=-0.03,
            net_funding_next_pct=0.01,
        ),
    ]

    payload = notifier._build_payload(
        rule,
        make_opportunity(),
        "https://example.com",
        observations=observations,
    )
    text = payload["content"]["text"]
    assert text == build_alert_message(rule, make_opportunity(), "https://example.com", observations=observations)

    assert "【告警触发】" in text
    assert "规则：FF 价差" in text
    assert "等级：warning（普通告警）" in text
    assert "套利类型：FF（永续买入 / 永续卖出）" in text
    assert "包含交易所：binance" in text
    assert "排除交易所：gate" in text
    assert "包含标的：BTCUSDT" in text
    assert "排除标的：继承实时机会页隐藏黑名单" in text
    assert "开仓阈值：>= 0.500%" in text
    assert "综合开仓阈值：>= 0.250%" in text
    assert "最低成交额：>= 1000K USDT" in text
    assert "数据时效：<= 600s" in text
    assert "排除风险：LOW_VOLUME" in text
    assert "连续命中：3 次" in text
    assert "冷却时间：300s" in text
    assert "【行情快照】" in text
    assert "买入腿：binance future" in text
    assert "卖出腿：okx future" in text
    assert "价差对：BTCUSDT | binance future -> okx future" in text
    assert "方向：买入 binance future BTCUSDT，卖出 okx future BTCUSDT" in text
    assert "价差：开仓 0.800% / 平仓 0.500%" in text
    assert "资金费率差（周期）：当前 -0.03% / 预测 0.01%" in text
    assert "结算周期：8h / 8h" in text
    assert "综合开仓：0.610%" in text
    assert "资金费率：0.01% / -0.02%" in text
    assert "【连续监测】" in text
    assert "1. 09:59:44 | 价差 0.720% | 净估算 0.520% | 资金差（周期） 0.03% | 综合 0.550%" in text
    assert "2. 09:59:52 | 价差 0.800% | 净估算 0.600% | 资金差（周期） 0.03% | 综合 0.630%" in text
    assert "风险：FUNDING_AGAINST" in text


def test_build_payload_can_use_prebuilt_alert_text() -> None:
    rule = AlertRule(name="FF spread")
    notifier = FeishuNotifier(FeishuConfig(webhook_url=""))

    payload = notifier._build_payload(
        rule,
        make_opportunity(),
        "",
        prebuilt_text="custom alert\n\nAstro: 已创建暂停卡片 BTC FF binance->okx，禁开=true",
    )

    assert payload["content"]["text"] == (
        "custom alert\n\nAstro: 已创建暂停卡片 BTC FF binance->okx，禁开=true"
    )


def test_alert_message_falls_back_to_current_cycle_when_next_funding_is_missing() -> None:
    rule = AlertRule(name="interval adjusted")
    opportunity = make_opportunity().model_copy(
        update={
            "funding_rate_buy_pct": 0.08,
            "funding_rate_sell_pct": 0.02,
            "funding_next_rate_buy_pct": None,
            "funding_next_rate_sell_pct": None,
            "net_funding_pct": -0.06,
            "net_funding_next_pct": None,
            "buy_funding_interval_hours": 8,
            "sell_funding_interval_hours": 1,
            "net_funding_hourly_pct": 0.01,
            "net_funding_daily_pct": 0.24,
            "net_funding_next_hourly_pct": None,
            "net_funding_next_daily_pct": None,
        }
    )

    text = build_alert_message(rule, opportunity)

    assert "资金费率差（周期）：当前 -0.06% / 预测 -0.06%" in text
    assert "结算周期：8h / 1h" in text
    assert "资金费率差（日化）" not in text


def test_alert_message_formats_market_times_in_utc_plus_8() -> None:
    rule = AlertRule(name="local time alert")
    observations = [
        SimpleNamespace(
            observed_at=datetime(2026, 5, 15, 1, 59, 44, tzinfo=UTC),
            open_spread_pct=0.72,
            fee_adjusted_open_pct=0.52,
            funding_edge_pct=0.03,
            combined_open_edge_pct=0.55,
            net_funding_pct=-0.03,
            net_funding_next_pct=0.01,
        )
    ]

    text = build_alert_message(rule, make_opportunity(), observations=observations)

    assert "下一次结算：16:00 / 16:00" in text
    assert "1. 09:59:44 |" in text
    assert "下一次结算：08:00 / 08:00" not in text
    assert "1. 01:59:44 |" not in text


def test_build_payload_honors_alert_message_template_blocks() -> None:
    rule = AlertRule(
        name="compact alert",
        severity=AlertSeverity.WARNING,
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
    )
    template = AlertMessageTemplateSettings(
        include_rule_details=False,
        include_funding=False,
        include_volume=False,
        include_risk=False,
        include_observations=False,
        include_dashboard_link=False,
    )
    observations = [
        SimpleNamespace(
            observed_at=datetime(2026, 5, 15, 1, 59, 44, tzinfo=UTC),
            open_spread_pct=0.72,
            fee_adjusted_open_pct=0.52,
            funding_edge_pct=0.01,
            combined_open_edge_pct=0.53,
            net_funding_pct=-0.03,
            net_funding_next_pct=0.01,
        )
    ]
    notifier = FeishuNotifier(FeishuConfig(webhook_url=""))

    payload = notifier._build_payload(
        rule,
        make_opportunity(),
        "https://example.com",
        observations=observations,
        template=template,
    )

    text = payload["content"]["text"]
    assert "【告警触发】" in text
    assert "compact alert" in text
    assert "价差对：BTCUSDT | binance future -> okx future" in text
    assert "开仓 0.800%" in text
    assert "【规则参数】" not in text
    assert "资金费率" not in text
    assert "成交额" not in text
    assert "风险" not in text
    assert "【连续监测】" not in text
    assert "Dashboard" not in text


class FakeFeishuOpenClient:
    def __init__(
        self,
        token_payload: dict | None = None,
        message_payload: dict | None = None,
        urgent_payload: dict | None = None,
    ):
        self.requests: list[tuple[str, str, dict | None, dict | None]] = []
        self.token_payload = token_payload or {
            "code": 0,
            "tenant_access_token": "tenant-token",
            "expire": 7200,
        }
        self.message_payload = message_payload or {
            "code": 0,
            "data": {"message_id": "om-message-id"},
        }
        self.urgent_payload = urgent_payload or {
            "code": 0,
            "data": {"invalid_user_id_list": []},
        }

    async def post(self, url: str, **kwargs):
        self.requests.append(("POST", url, kwargs.get("json"), kwargs.get("headers")))
        if url.endswith("/auth/v3/tenant_access_token/internal"):
            return FakeFeishuResponse(self.token_payload)
        if "/im/v1/messages" in url:
            return FakeFeishuResponse(self.message_payload)
        return FakeFeishuResponse({"code": 0})

    async def patch(self, url: str, **kwargs):
        self.requests.append(("PATCH", url, kwargs.get("json"), kwargs.get("headers")))
        return FakeFeishuResponse(self.urgent_payload)


class FakeFeishuResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


@pytest.mark.asyncio
async def test_send_phone_urgent_alert_posts_message_then_phone_urgent() -> None:
    client = FakeFeishuOpenClient()
    notifier = FeishuNotifier(
        FeishuConfig(
            webhook_url="",
            app_id="cli_xxx",
            app_secret="secret",
            alert_chat_id="oc_chat",
            phone_user_ids=["ou_user_1", "ou_user_2"],
            phone_enabled=True,
        ),
        client=client,
    )

    await notifier.send_phone_urgent_text("BTCUSDT reached 110000")

    assert client.requests[0] == (
        "POST",
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        {"app_id": "cli_xxx", "app_secret": "secret"},
        None,
    )
    assert client.requests[1][0] == "POST"
    assert client.requests[1][1] == (
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    )
    assert client.requests[1][2] == {
        "receive_id": "oc_chat",
        "msg_type": "text",
        "content": "{\"text\":\"BTCUSDT reached 110000\"}",
    }
    assert client.requests[1][3] == {"Authorization": "Bearer tenant-token"}
    assert client.requests[2] == (
        "PATCH",
        "https://open.feishu.cn/open-apis/im/v1/messages/om-message-id/urgent_phone?user_id_type=open_id",
        {"user_id_list": ["ou_user_1", "ou_user_2"]},
        {"Authorization": "Bearer tenant-token"},
    )


@pytest.mark.asyncio
async def test_send_phone_urgent_alert_skips_when_disabled() -> None:
    client = FakeFeishuOpenClient()
    notifier = FeishuNotifier(
        FeishuConfig(
            webhook_url="",
            app_id="cli_xxx",
            app_secret="secret",
            alert_chat_id="oc_chat",
            phone_user_ids=["ou_user_1"],
            phone_enabled=False,
        ),
        client=client,
    )

    await notifier.send_phone_urgent_text("BTCUSDT reached 110000")

    assert client.requests == []


@pytest.mark.asyncio
async def test_send_phone_urgent_alert_raises_on_feishu_business_error() -> None:
    client = FakeFeishuOpenClient(
        message_payload={
            "code": 99991672,
            "msg": "Access denied",
            "error": {"log_id": "2026052723345945709A75F6A8ECEFF41B"},
        }
    )
    notifier = FeishuNotifier(
        FeishuConfig(
            webhook_url="",
            app_id="cli_xxx",
            app_secret="secret",
            alert_chat_id="oc_chat",
            phone_user_ids=["ou_user_1"],
            phone_enabled=True,
        ),
        client=client,
    )

    with pytest.raises(RuntimeError, match="99991672.*Access denied.*2026052723345945709A75F6A8ECEFF41B"):
        await notifier.send_phone_urgent_text("BTCUSDT reached 110000")


@pytest.mark.asyncio
async def test_send_phone_urgent_alert_raises_on_invalid_phone_user_ids() -> None:
    client = FakeFeishuOpenClient(
        urgent_payload={
            "code": 0,
            "data": {"invalid_user_id_list": ["ou_bad_user"]},
        }
    )
    notifier = FeishuNotifier(
        FeishuConfig(
            webhook_url="",
            app_id="cli_xxx",
            app_secret="secret",
            alert_chat_id="oc_chat",
            phone_user_ids=["ou_bad_user"],
            phone_enabled=True,
        ),
        client=client,
    )

    with pytest.raises(RuntimeError, match="invalid phone user IDs.*ou_bad_user"):
        await notifier.send_phone_urgent_text("BTCUSDT reached 110000")
