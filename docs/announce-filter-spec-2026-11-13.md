# 公告推送：只推对套利有用的「其他」公告（2026-11-13）

分支 `codex/announce-filter`，基于部署分支 b4c2edd。**不要 push、不要部署、不要跑 WSL。**

## 背景
生产 AnnouncementSettings：`other_alerts_enabled=true`、`alert_exchanges=[]`，所以 kind=other 的公告全部推飞书（24h 约 55 条），大部分是活动/空投/直播/竞赛/跟单/新闻。用户要求只推对套利有用的。上新/下架（listing/delisting）、launchpool、事件提醒逻辑不变。

## 要求
1. AnnouncementSettings 新增 `other_alerts_filter: Literal["all", "arbitrage_relevant"] = "arbitrage_relevant"`（旧数据兼容，设置页可切换，中文说明）。
2. 新纯函数 `is_arbitrage_relevant_announcement(title: str, title_zh: str | None = None, category: str | None = None) -> bool`（放 announcements.py 或新文件 announcement_relevance.py），大小写不敏感，中英文：
   - **保留**（命中任一且未命中排除）：充值/提币/提现 暂停或恢复（deposit/withdraw/withdrawal + suspend/resume/halt/reopen/open/close）、网络升级/硬分叉/主网迁移（network upgrade/hard fork/mainnet/migration）、资金费率/资金费率周期/结算周期/上限调整（funding rate/funding interval/settlement）、合约规格/面值/乘数/最小下单/价格精度/杠杆/保证金/限仓调整（contract size/specification/multiplier/tick size/leverage/margin/position limit/risk limit）、拆股/合股/股票分割（stock split/reverse split/split adjustment/拆股/合股）、下架/停止交易/移除交易对/合约交割（delist/removal/cease/terminate/delivery/remove trading pair）、系统维护/升级（maintenance/system upgrade/维护/升级）、代币换币/更名/迁移（token swap/rebrand/ticker change/换币/更名）、指数/标记价格调整（index price/mark price）。
   - **排除**（优先于保留）：活动/空投/奖励/抽奖/直播/嘉年华/竞赛/邀请/跟单/理财/学习/排行榜/储备金/广告/法币入金/voucher（campaign/airdrop/reward/giveaway/live stream/carnival/competition/invite/copy trade/earn/learn/leaderboard/proof of reserves/candydrop/candybomb/voucher/frenzy/share .* usdt|usdc|prize pool/活动/空投/奖励/瓜分/直播/嘉年华/大赛/邀请/跟单/理财/排行榜）。注意「suspending … withdrawal service」这类必须保留，即使标题有 network 等词。
   - 保留词表/排除词表做成模块常量，便于以后加。
3. 在 announcements.py 约 2570 行 `should_alert_other` 处：`other_alerts_filter == "arbitrage_relevant"` 时额外要求 `is_arbitrage_relevant_announcement(...)` 为真；不命中的仍入库，`alert_status="muted"`。`alert_exchanges` 白名单逻辑不变。
4. 前端设置页「交易所公告」加该开关（「全部其他公告 / 只推对套利有用的」）。

## 测试
- `docs/announce-titles-7d.json` 是生产近 7 天 282 条 other 公告标题（[exchange, kind, title]）。写参数化测试：至少把下列判对 ——
  保留：`Bitget announcement on suspending ETH - Arbitrum One network withdrawal service`、`Bitget announcement on resuming DOGE - Dogecoin withdrawals`、`Bitget to adjust funding rate interval for HOTUSDT perpetual futures`、`Important: BITOUSDT Contract Size Adjustment Due to a 1-for-2 Reverse Stock Split`、`SNDK Perpetual & Perp Options: Stock Split Adjustment`、`Bybit Will Support the Clearpool (CPOOL) Token Swap and Rebranding to CLEAR`；
  排除：`Gate Live Stream Carnival Phase 28: Watch Live and Join the Daily Reward Spins`、`Gate Futures Points Airdrop #168: Claim 0.018 QQQG & 100 USDT`、`Candybomb 嘉年华（第13期）：交易瓜分 60,000 USDT！`、`Bitget spot copy trade now supports rMSBI/USDT`、`Binance Alpha Trading Competition: Trade LAB (LAB) and Share $200K Worth of Rewards (2026-11-12)`、`Introducing Binance Square Leaderboards: Discover Top Traders and Share Your Achievements`、`Fiat-to-Crypto Frenzy: Complete Simple Tasks for a Chance to Win a Share of 80,000 USDT`、`CFD Trading Is Back: Claim a 200 USDx Position Voucher and Up to 686 USDT in Rewards`。
- 再输出一份对 282 条的分类结果到 `docs/announce-filter-preview.md`（保留/排除两列表，便于人工抽查），并在报告里给出保留数量。
- 服务层测试：filter=arbitrage_relevant 时活动类入库为 muted、充提类为 pending；filter=all 行为与现状一致；listing/delisting 不受影响。
- 运行：backend（backend/ 目录）公告相关测试 `pytest tests -q -p no:cacheprovider --basetemp ../.tmp-annf -k announce`；frontend `npx.cmd tsc --noEmit`、`npx.cmd vitest run --no-cache`。
- commit：`feat: 公告推送只推对套利有用的其他公告`，输出改动文件、测试汇总行、282 条中保留数量。
