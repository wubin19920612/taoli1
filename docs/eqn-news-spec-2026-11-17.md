# 任务书：接入 eqn.news 交易所公告（2026-11-17）

## 目标
把 eqn.news（方程式新闻，免 key）的**交易所公告**接入现有公告流水线：入库、公告页可见、走现有过滤后推飞书。
新增 taoli1 原来没有的交易所（Upbit、Bithumb、MEXC、Aster），已有官方源的交易所（Binance/OKX/Bybit/Gate/Bitget/Hyperliquid）与官方源**去重**，谁先到谁推，后到的不重复推送。

## 接口（已实测，样本在 backend/tests/fixtures/eqn/）
- MCP：`POST https://api.eqn.news/public/mcp`，JSON-RPC 2.0。`initialize` 后 `tools/call` name=`list_news`，arguments `{channel, limit(1-50), before?}`。
  - 响应可能是 `application/json` 或 SSE（`event: message` / `data: {...}`），两种都要能解析。响应头若有 `Mcp-Session-Id` 则后续请求带上；会话失效（4xx/JSON-RPC 错误）时重新 initialize 一次。
  - 结果在 `result.content[0].text`（JSON 字符串）：`{"items":[{no,time(ISO 毫秒 UTC),channels,source,zh,en,coins,url,source_url}],"next_cursor"}`；`result.isError` 为真按失败处理。
- 限频（每 IP）：MCP 5 次/秒；全部接口 10 秒 120 次。429 读 `Retry-After` 退避，期间不再请求。
- 轮询频道：`binance, korea, alpha, tier2, tier3`，每频道 limit=20。不要接 main/tradfi/major/oi/pricechange/cz/earn（新闻和价格监控，噪音）。

## 来源 → 交易所映射（source 字段，大小写不敏感）
| source | exchange |
|---|---|
| Binance EN, Binance Alpha | binance |
| Bitget Announcements | bitget |
| Bybit Announcements | bybit |
| OKX Announcements | okx |
| Gate.io Announcements | gate |
| Hyperliquid Perp Listing | hyperliquid |
| Mexc Announcements | mexc |
| Upbit, UPBIT LISTING | upbit |
| Bithumb, Bithumb Listing | bithumb |
| Aster Perp Listing, Aster Spot Listing | aster |
其他 source（含 Binance Wallet Pre-TGE、新闻、推特、Price Monitor）一律跳过，按 source 计数记 debug 日志，不报错。

## 字段映射 → ExchangeAnnouncement
- exchange=映射结果；source=`eqn`；announcement_id=`eqn-{no}`
- title=`en`（为空用 zh）；title_zh=`zh`；url=`source_url`（若为空或是 api.eqn.news/eqn.news 域名则用 `url`）
- symbols：`coins` 非空用 coins（走现有 symbol 规范化），否则 `infer_symbols(title)`
- kind=`classify_announcement(title)`；market_type=`infer_market_type(title)`；published_at=`time`；category=频道名
- 不需要 localize（自带中文）

## 实现要点
1. 新文件 `backend/app/services/announcement_eqn.py`：`EqnNewsClient`（httpx 异步，复用现有公告 provider 的 client 构建方式/代理设置/超时）和 `EqnAnnouncementProvider(AnnouncementProvider)`，`exchange="eqn"`，`fetch()`/`fetch_headlines()` 返回多交易所的公告；有 `last_source_articles`/`last_source_errors` 供轮询观测；`aclose()`。
2. 轮询：`announcement_polling.run_announcement_sources` 对 eqn provider 的激活条件改为 `settings.enabled and eqn 开关开`（不能用 `provider.exchange in record_exchanges`）；间隔用 provider 自己的 `poll_interval_seconds`（默认 10 秒，最小 5 秒），其他 provider 行为不变。`AnnouncementMonitor.process` 已按 record_exchanges 过滤交易所，保持。
3. 配置（`backend/app/core/config.py`）：`EQN_NEWS_ENABLED`（默认 true）、`EQN_NEWS_POLL_SECONDS`（默认 10）。关掉时不构建 provider。`default_announcement_provider` 里接入。
4. 交易所列表：`ANNOUNCEMENT_EXCHANGES`/`ANNOUNCEMENT_EXCHANGE_OPTIONS` 加 upbit(Upbit)、bithumb(Bithumb)、mexc(MEXC)、aster(Aster)；`AnnouncementSettings.record_exchanges` 默认值同步加上。
   - **已保存设置迁移**：生产库里保存的 record_exchanges 是旧 6 家的显式列表。`load_announcement_settings` 读取时，若保存的设置里没有迁移标记（如 `exchange_catalog_version < 2`），把 4 家新交易所追加到 record_exchanges 并写回标记；之后用户在设置页取消勾选要能保持取消。
   - 检查前端是否有写死的交易所列表/显示名，有就补上（选项应来自 `/announcements` 的 exchange options 接口）。
5. 跨来源去重（`AnnouncementMonitor`，对所有来源通用，不只 eqn）：入库前，在同交易所、**其他 source**、发布时间 ±6 小时内查找重复——规范化标题（小写、去标点和空白）与对方 title 或 title_zh 相同，或 url 相同。
   - 有重复：本条仍入库但 `alert_status="duplicate"`，不推送、不做 asset research、不做事件提醒；公告列表接口默认不返回 duplicate。
   - 记一行 info 日志：`announcement cross_source exchange=… first_source=… second_source=… lead_seconds=…`（first 的 fetched_at 与本条 fetched_at 之差），用来以后比较 eqn 和官方谁快。
   - 仓储层加需要的查询方法，必要时加索引；不改已有唯一键。
6. 飞书消息：来源为 eqn 时在交易所名后标注 `（eqn.news）`，其余格式不变。

## 不可修改
- 已有各交易所官方 provider 的抓取逻辑；`announcement_relevance.py` 的词表；交易/执行/采集相关模块。

## 测试（新增 `backend/tests/test_announcement_eqn.py`，用 fixtures，不联网）
- JSON 与 SSE 两种响应解析；会话失效重连一次；429 Retry-After 退避期间不请求；isError 处理。
- source 映射全表 + 未知 source 跳过；字段映射（url 回退、coins 为空时推断、title 为空回退 zh）。
- 去重：官方先到→eqn 记 duplicate 不推送；eqn 先到→官方记 duplicate；超出 6 小时不算重复；同 source 不参与；列表接口隐藏 duplicate。
- 设置迁移：旧显式列表补上新交易所且只迁一次；用户取消后不再加回。
- 轮询：eqn provider 不受 record_exchanges 激活条件影响，用自己的间隔；`EQN_NEWS_ENABLED=false` 时不构建。

## 验收命令
1. `cd backend && python -m pytest tests/test_announcement_eqn.py -q`
2. `cd backend && python -m pytest -q`（全量，报汇总行）
3. 前端如有改动：`cd frontend && npx tsc --noEmit && npm test -- --run`
完成后在仓库内提交一个 commit（`feat(announcements): ingest eqn.news exchange notices with cross-source dedup`），并在最后输出：改动文件列表、测试命令与汇总行。
