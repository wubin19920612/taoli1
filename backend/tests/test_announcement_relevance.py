import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.db.database import connect_database
from app.db.repositories import AnnouncementRepository, SettingsRepository
from app.db.schema import initialize_schema
from app.models.announcement import AnnouncementKind, AnnouncementSettings, ExchangeAnnouncement
from app.services.announcement_relevance import is_arbitrage_relevant_announcement
from app.services.announcements import AnnouncementMonitor

# Reviewed against the task's rule families. Numbers are 1-based source rows,
# retaining duplicates so the complete seven-day production sample is covered.
RETAINED_SAMPLE_ROWS = {
    3, 5, 6, 7, 9, 10, 11, 13, 16, 17, 32, 35, 36, 38, 45, 48, 54, 58, 60, 64,
    73, 78, 79, 81, 84, 85, 89, 90, 92, 98, 99, 103, 104, 107, 109, 117, 118,
    122, 123, 128, 130, 133, 142, 146, 147, 149, 159, 167, 176, 185, 192, 193,
    195, 200, 201, 202, 204, 205, 206, 208, 210, 214, 216, 225, 226, 227, 228,
    229, 231, 236, 237, 238, 244, 248, 249, 251, 252, 255, 257, 260, 261, 262,
    263, 266, 267, 268, 269, 271, 272, 275, 281,
}
SAMPLE_TITLES = json.loads(
    (Path(__file__).parent / "fixtures" / "announce-titles-7d.json").read_text(encoding="utf-8")
)


def test_announcement_relevance_sample_is_complete():
    assert len(SAMPLE_TITLES) == 282
    assert len(RETAINED_SAMPLE_ROWS) == 91
    assert all(kind == "other" for _, kind, _ in SAMPLE_TITLES)


@pytest.mark.parametrize(
    "row_number,exchange,kind,title",
    [(index, *row) for index, row in enumerate(SAMPLE_TITLES, 1)],
    ids=[f"sample-{index}-{row[0]}" for index, row in enumerate(SAMPLE_TITLES, 1)],
)
def test_announcement_relevance_production_sample(row_number, exchange, kind, title):
    assert is_arbitrage_relevant_announcement(title) is (row_number in RETAINED_SAMPLE_ROWS)


@pytest.mark.parametrize("title", [
    "Bitget announcement on suspending ETH - Arbitrum One network withdrawal service",
    "Bitget announcement on resuming DOGE - Dogecoin withdrawals",
    "Bitget to adjust funding rate interval for HOTUSDT perpetual futures",
    "Important: BITOUSDT Contract Size Adjustment Due to a 1-for-2 Reverse Stock Split",
    "SNDK Perpetual & Perp Options: Stock Split Adjustment",
    "Bybit Will Support the Clearpool (CPOOL) Token Swap and Rebranding to CLEAR",
    "Notice of Suspension for TRON (TRX) Network Deposit and Withdrawal",
    "Recovery Update: Withdrawals for More Assets to Reopen",
    "DEPOSITS WILL CLOSE during maintenance",
    "Withdrawals halted for ABC",
    "Opening ABC deposits",
    "ABC Hard Fork and Network Upgrade",
    "ABC Mainnet Migration",
    "Changes to funding interval and settlement caps",
    "Contract specification and multiplier adjustment",
    "Specifications of selected futures contracts updated",
    "Minimum order size and tick size adjustment",
    "Changes of Max. Order Size for USDT Perpetual Contracts",
    "Gate to Adjust Size Decimal Places for Certain Trading Pairs",
    "Updates on Step Size for Spot Trading Pairs",
    "Notice on adjustment to maximum spot trade quantity",
    "Bitget to update ABC collateral ratio for UTA",
    "Margin and leverage adjustment",
    "Position limit and risk limit adjustment",
    "Delisting ABC and removal of trading pairs",
    "Cease trading and terminate ABC contract before delivery",
    "Remove trading pair ABC/USDT",
    "Exchange to remove ABC/USDT trading pairs",
    "Ceasing trading and termination of ABC futures",
    "Scheduled system upgrade",
    "Gate to Upgrade USDT-M Perpetual Futures",
    "ABC ticker change",
    "ANTHROPIC stock perps rename and share capital adjustment",
    "欧易关于对 ANTHROPIC 永续合约及 X-Perp 执行 Rebase 的公告",
    "Changes to index price and mark price",
    "Bitget announcement on the temporary adjustment to the COPPERUSDT futures index",
    "暂停充值及提币服务",
    "恢复充币与提现",
    "网络升级、硬分叉与主网迁移公告",
    "资金费率周期、结算周期及资金费率上限调整",
    "合约规格、面值及乘数调整",
    "最小下单数量、价格精度调整",
    "杠杆、保证金及限仓调整",
    "拆股、合股及股票分割公告",
    "下架、停止交易及移除交易对公告",
    "合约交割通知",
    "系统维护与升级通知",
    "代币换币、更名公告",
    "指数价格与标记价格调整",
    "【重要】Bitget 关于COPPERUSDT 合约指数临时调整公告",
    "Gate Announcement on the CLEAR Token Migration",
])
def test_arbitrage_relevant_announcement_retained(title):
    assert is_arbitrage_relevant_announcement(title)


@pytest.mark.parametrize("title", [
    "Gate Live Stream Carnival Phase 28: Watch Live and Join the Daily Reward Spins",
    "Gate Futures Points Airdrop #168: Claim 0.018 QQQG & 100 USDT",
    "Candybomb 嘉年华（第13期）：交易瓜分 60,000 USDT！",
    "Bitget spot copy trade now supports rMSBI/USDT",
    "Binance Alpha Trading Competition: Trade LAB (LAB) and Share $200K Worth of Rewards (2026-11-12)",
    "Introducing Binance Square Leaderboards: Discover Top Traders and Share Your Achievements",
    "Fiat-to-Crypto Frenzy: Complete Simple Tasks for a Chance to Win a Share of 80,000 USDT",
    "CFD Trading Is Back: Claim a 200 USDx Position Voucher and Up to 686 USDT in Rewards",
    "Funding Rate Reward Campaign",
    "Margin trading giveaway",
    "Learn about network upgrades",
    "Earn with leverage",
    "Copy trading system upgrade",
    "Contract size adjustment competition",
    "Proof of Reserves system upgrade",
    "Fiat deposit maintenance",
    "Advertising platform system upgrade",
    "Share 1,000 USDT with leverage",
    "Share 1,000 USDC with leverage",
    "Share our prize pool with leverage",
    "资金费率调整活动",
    "保证金理财奖励",
    "网络升级直播",
    "杠杆交易抽奖竞赛",
    "学习合约规格",
    "储备金系统升级",
    "法币入金系统维护",
    "Plain weekly news",
    "Network status update",
    "Deposit information",
    "Grand opening of a new office",
    "Research on market movements",
    "Earnings report released",
    "",
])
def test_arbitrage_relevant_announcement_excluded(title):
    assert not is_arbitrage_relevant_announcement(title)


@pytest.mark.parametrize("title,title_zh,category,expected", [
    ("Operational notice", "恢复提现服务", None, True),
    ("Operational notice", None, "maintenance_system_updates", True),
    ("Operational notice", None, "Network Upgrade", True),
    ("Funding Rate Adjustment", "资金费率调整活动", None, False),
    ("Maintenance notice", None, "campaigns", False),
    ("Reward Campaign", "系统升级通知", None, False),
    ("Clearpool rebranding announcement", None, None, True),
    ("Withdrawals resuming for LEARNUSDT", None, None, True),
    ("NETWORK WITHDRAWAL SERVICE SUSPENDING", None, None, True),
])
def test_arbitrage_relevant_announcement_combines_fields(title, title_zh, category, expected):
    assert is_arbitrage_relevant_announcement(title, title_zh, category) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("filter_mode", ["all", "arbitrage_relevant"])
async def test_announcement_filter_settings_legacy_and_round_trip(filter_mode):
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = SettingsRepository(db)
        assert (await repo.get_announcement_settings()).other_alerts_filter == "arbitrage_relevant"
        # Stored payloads from before the new field keep all previous settings.
        await db.execute(
            "INSERT INTO app_settings (key, payload) VALUES (?, ?)",
            ("announcements", json.dumps({"other_alerts_enabled": True, "alert_exchanges": []})),
        )
        await db.commit()
        legacy = await repo.get_announcement_settings()
        assert legacy.other_alerts_filter == "arbitrage_relevant"
        assert legacy.other_alerts_enabled is True
        assert legacy.alert_exchanges == []
        await repo.set_announcement_settings(AnnouncementSettings(other_alerts_filter=filter_mode))
        assert (await repo.get_announcement_settings()).other_alerts_filter == filter_mode
    finally:
        await db.close()


def test_announcement_filter_rejects_invalid_setting():
    with pytest.raises(ValidationError):
        AnnouncementSettings(other_alerts_filter="unknown")


@pytest.mark.asyncio
@pytest.mark.parametrize("filter_mode,enabled,alert_exchanges,record_exchanges,expected", [
    ("arbitrage_relevant", True, [], ["bitget"], ["muted", "sent", "sent", "sent", "sent"]),
    ("all", True, [], ["bitget"], ["sent"] * 5),
    ("arbitrage_relevant", False, [], ["bitget"], ["muted", "muted", "sent", "sent", "sent"]),
    ("arbitrage_relevant", False, ["bitget"], ["bitget"], ["sent"] * 5),
    ("arbitrage_relevant", True, ["bitget"], ["bitget"], ["sent"] * 5),
    ("arbitrage_relevant", True, ["bitget"], [], []),
])
async def test_announcement_filter_persistence_and_independent_alert_paths(
    filter_mode, enabled, alert_exchanges, record_exchanges, expected,
):
    now = datetime(2026, 11, 13, tzinfo=UTC)
    cases = [
        (AnnouncementKind.OTHER, "Leverage trading reward campaign"),
        (AnnouncementKind.OTHER, "Suspending ABC network withdrawals"),
        # Exclusion words only filter OTHER, never these independent alert paths.
        (AnnouncementKind.LISTING, "New listing reward campaign"),
        (AnnouncementKind.DELISTING, "Delisting ABC and ending its reward campaign"),
        (AnnouncementKind.LAUNCHPOOL, "Launchpool reward campaign"),
    ]
    rows = [ExchangeAnnouncement(
        announcement_id=str(index), exchange="bitget", source="test", kind=kind,
        title=title, url=f"https://example.com/{index}", published_at=now, fetched_at=now,
    ) for index, (kind, title) in enumerate(cases)]
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = AnnouncementRepository(db)
        # Seed the source watermark so these are new notices, not initial history.
        await repo.create_if_new(rows[0].model_copy(update={
            "id": "baseline", "announcement_id": "baseline", "alert_status": "muted",
        }))
        sent = []

        async def send(message):
            # Sending only happens after persistence, while the row is pending.
            pending = [row for row in await repo.list(limit=10) if row.alert_status == "pending"]
            assert len(pending) == 1
            sent.append(message)

        monitor = AnnouncementMonitor(repo, alert_sender=send, now_fn=lambda: now)
        settings = AnnouncementSettings(
            other_alerts_enabled=enabled, other_alerts_filter=filter_mode,
            record_exchanges=record_exchanges, alert_exchanges=alert_exchanges,
        )
        created = await monitor.process(rows, settings)
        assert [row.alert_status for row in sorted(created, key=lambda row: row.announcement_id)] == expected
        assert len(sent) == expected.count("sent")
        for row in created:
            stored = await repo.get_by_identity(
                exchange=row.exchange, source=row.source, announcement_id=row.announcement_id,
            )
            assert stored.alert_status == row.alert_status
        # Changing to all never replays an already recorded muted campaign.
        assert await monitor.process(
            rows, settings.model_copy(update={"other_alerts_filter": "all"}),
        ) == []
    finally:
        await db.close()
