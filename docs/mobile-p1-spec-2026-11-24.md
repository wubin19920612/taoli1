# 手机 App P1：后端只读接口 + 只读会话 + 提醒事件（2026-11-24）

背景与拍板：`D:/吴彬工作/claude/套利/research/mobile-app/design.md`（§3、§6）。本期只做后端，不做 FCM、不改前端页面。

## 范围
可新建：`backend/app/api/routes_mobile.py`、`backend/app/services/mobile_summary.py`、`backend/app/models/mobile.py`、对应测试。
可修改：`backend/app/db/auth.py`、`backend/app/core/security.py`、`backend/app/api/routes_auth*`（登录接口所在文件）、`backend/app/services/feishu.py`、`backend/app/db/repositories*`、`backend/app/main.py`（注册路由/仓库）、数据库迁移位置。
不可修改：`astro_client.py` 的写接口、任何建卡/下单逻辑、已有测试的断言（只能新增测试）。

## 1. 只读会话
- `POST /api/auth/login` 增加可选字段 `scope`：`"full"`（默认，行为不变）| `"mobile_readonly"`。
- `auth_sessions` 加列 `scope TEXT NOT NULL DEFAULT 'full'`（迁移兼容旧库）。
- `mobile_readonly` 会话只允许：`GET /api/mobile/*`、`POST /api/auth/logout`、`GET /api/health*`。其它任何路由 → 403 `{"detail":"手机只读会话无权访问"}`。在鉴权依赖里集中判断，不要逐个路由加。
- `X-Api-Token` 和 `full` 会话可以访问 `/api/mobile/*`。
- `GET /api/auth/sessions` 返回带 `scope`。
- 同样受现有登录限频/TOTP 一次性规则约束，不得绕过。

## 2. `GET /api/mobile/summary`
数据源：`astro_client.list_pairs_cached()` + 与 `GET /api/instruments/{symbol}` 相同的服务层行情（直接调服务函数，不走 HTTP）。**不新增对交易所的请求**，同一 symbol 在一次 summary 内只查一次。
计算逻辑**逐字移植**前端 `frontend/src/components/FloatingWatchPanel.tsx`：`astroHasPosition`、`astroPositionMetrics`、`astroPairBases`、`astroLegs`、`astroMarket`、`executablePrice`、`spreadPct`、`astroEffectiveRatioReference`、`astroMetrics`、`astroProfitFeeRate`、`astroLocalProfitEstimate`（约 218–425 行）。手机与网页数字必须一致。

每张卡输出（`models/mobile.py`，Pydantic）：
```
name, type, buy_ex, sell_ex, symbol_a, symbol_b, dex_a, dex_b,
enabled(status), disable_open, disable_close,
state: "running"|"paused"|"open_disabled"|"close_disabled",
holding: bool,
open_spread_now, close_spread_now: float|None   # %，= astroMetrics.openMetric/closeMetric
open_condition, close_condition: float|None     # = openPosition/closePosition（与网页同口径）
gap_to_open = open_spread_now - open_condition     # >0 表示已满足开仓
gap_to_close = close_condition - close_spread_now  # >0 表示已满足清仓
qty_a, qty_b, avg_open_a, avg_open_b, notional_usdt,
unrealized_usdt(=astroLocalProfitEstimate), astro_profit(=profit), realized_profit,
funding_a, funding_b, funding_diff, funding_interval_h_a/b, next_funding_at_a/b,
metric_error: str   # 行情缺失原因，中文，与前端文案一致
quote_age_s: float|None
```
顶层：`updated_at`、`astro_cache_age_s`、`stale`（Astro 缓存或任一持仓卡行情 >30 秒为 true）、`cards`。
排序：持仓卡在前（`state!=running` 的置顶），未持仓按 `gap_to_open` 从大到小。
开/清价差的符号与 `gap` 方向请先用一张真实卡（测试夹具）对照前端口径核实；若前端口径下“满足开仓”是 `open_spread_now >= open_condition` 以外的关系，以前端/Astro 文档为准并在报告里写明。

## 3. `GET /api/mobile/cards/{name}/history?hours=24`
复用价差查询页的历史数据服务（`routes_pair_spread.py` 背后的服务函数），按该卡两腿查询，返回 `points:[{t, open_spread, close_spread}]` + 当前 `open_condition/close_condition`。`hours` 限 1–72。找不到卡 404。

## 4. 提醒事件
- 新表 `alert_events(id INTEGER PK, created_at REAL, kind TEXT, title TEXT, body TEXT, level TEXT, symbol TEXT NULL, card_name TEXT NULL)`；索引 created_at；保留 30 天（启动时和每天清理一次）。
- 在 `services/feishu.py` 的发送入口（`send_alert`、`send_text`、`send_phone_urgent_text`）统一记录：**先写表再发飞书；写表失败只记日志，绝不影响飞书发送、绝不抛到调用方**。`kind` 由调用处能拿到的信息推断，拿不到就 `"general"`；`level`：加急=urgent，其余 normal。
- 禁止记录密钥/令牌；body 截断 2000 字。
- `GET /api/mobile/alerts?since=<unix>&limit=<=200>`：按时间倒序。

## 5. 资源约束
生产机 1.9G 内存、2 核：summary 不得常驻新任务；单次 summary 在 50 张卡下 < 300ms（测试里用假数据测量并断言 < 1s）；不加新依赖。

## 验收
1. 新增测试覆盖：只读会话白名单/403、旧库迁移、summary 计算与前端一致（至少 FF、FR/ratio、HL dex、单腿缺行情、无持仓 5 类夹具）、排序、stale、alert 写表失败不影响飞书、清理 30 天。
2. 后端全量：`cd backend && python -m pytest -q`（报告贴汇总行）。
3. `git diff --name-only a1d61da` 只含范围内文件。
4. 报告：改了哪些文件、开清价差/gap 方向核实结论、未完成项。
不要 push、不要部署、不要跑 WSL。
