# P16 手机实时机会接口交接

分支为 `codex/mobile-p1`，基线为 `bccb8f6`。本次交付新增 `GET /api/mobile/opportunities`，供手机端读取实时机会列表。使用 `mobile_readonly` 会话访问该路径时返回机会列表，同一会话访问 `/api/opportunities` 返回 403。

## 接口与业务规则

- 可选筛选参数为 `type`（`SF`、`FF`、`SS`）、`symbol`、`exchange`、`min_open_spread_pct` 和 `min_volume_24h_k`（最小值 0）。`include_risky` 默认 `false`。`sort_by` 使用网页的 `OpportunitySort`，手机端默认 `open_spread_pct`。`sort_order` 默认 `desc`，`limit` 范围 1–100，默认 50。
- `backend/app/api/routes_mobile_opportunities.py` 直接复用网页 `list_opportunities` 的符号别名、服务器风控、陈旧数据剔除、筛选和排序。`total` 为筛选后的总数，`items` 在排序后按 `limit` 截断，`observed_at` 为本次读取的 UTC 时间。
- `backend/app/models/mobile.py` 定义 `MobileOpportunityList` 和 `MobileOpportunityItem`。买卖腿各保留交易所、市场类型、原始标的和 DEX。`min_volume_24h_usdt` 为两腿 24 小时成交额较小值，任一腿缺失时返回 `null`。`max_age_ms` 使用网页请求时计算的两腿报价年龄较大值，任一腿缺失时返回 `null`。
- 同一会话最多一个在途请求，启动间隔至少 3 秒。P16 使用独立的 `MobileQueryLimits` 实例，两种限频分别返回 429。非法参数返回 422，内部失败返回 503 和固定文案“机会数据暂不可用”。

## 验收与接续

- `backend/tests/test_mobile_opportunities.py` 使用进程内 ASGI 和本地测试数据库验证网页筛选排序一致性、别名、风险过滤、字段、排序截断、限频、错误和会话权限。验收范围为 Windows 本地测试与 Git 提交，线上和 App 真机接入留待后续任务。
- 在 `backend` 下使用任务指定的 Python 和 `--basetemp` 运行 `python -m pytest tests -k mobile -q`，结果为 `214 passed, 4404 deselected, 1 warning in 59.53s`。唯一警告来自 Starlette TestClient 的 httpx 弃用提示。
- 已存在的 `docs/mobile-p12-feishu-push-2026-11-25.md`、`docs/mobile-p13-pair-spread-2026-11-25.md`、`docs/mobile-p14-eqn-news-2026-11-25.md`、`docs/mobile-p15-premium-index-2026-11-26.md`、`docs/mobile-p16-opportunities-2026-11-26.md` 仍为未跟踪规格文档。
- App 对接可在新任务使用响应模型接入列表，并以两腿 `raw_symbol`、`dex`、市场类型和交易所构造价差查询入口。
