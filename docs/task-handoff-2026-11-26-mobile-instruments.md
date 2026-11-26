# P17 手机标的查询接口交接

分支为 `codex/mobile-p1`，基线为 `deed01b`。本阶段交付三个只读后端接口，验收范围为 Windows 本地测试和 Git 提交。

## 接口与代码入口

- `GET /api/mobile/instruments/{symbol}?dex=&asset_id=` 复用网页标的查询。`backend/app/api/routes_mobile_instruments.py` 调用 `routes_instruments._lookup_instrument` 时关闭 Astro 路线查询，并按 `backend/app/models/mobile.py` 中的 `MobileInstrument*` 模型精简响应。市场和价差保留原始标的、DEX 及网页顺序。价差最多 200 条，`spread_total` 为完整数量。资产组 `market_count` 用于选择资产，按网页过滤前的分组统计。
- `GET /api/mobile/instruments/{symbol}/statistics?dex=&asset_id=` 复用网页统计及 `InstrumentStatisticsResult`。同名不同资产且未选资产时返回 409。
- `GET /api/mobile/instrument-market-cap/{base}?coin_id=` 复用网页市值及 `InstrumentMarketCapResult`。输入无效时返回 422。
- `mobile_readonly` 会话可访问三个手机入口，访问对应网页入口返回 403。`mobile_query_utils.MobileQueryLimits` 按会话限制在途请求和启动间隔。报价使用独立名额，间隔至少 2 秒。统计和市值共用名额，间隔至少 5 秒。P13、P15、P16 使用各自的名额及 3 秒默认值。未处理的内部失败返回 503 和固定文案“标的数据暂不可用”。

## 验收与接续

- `backend/tests/test_mobile_instruments.py` 验证字段和排序与网页一致、截断、资产选择、跳过 Astro、错误状态和文案、并发及间隔限流和权限。
- `backend` 下运行 `python -m pytest tests -k 'mobile or instrument' --basetemp=<scratchpad>/run1 -q`，汇总为 `344 passed, 4286 deselected, 1 warning in 66.08s (0:01:06)`。警告来自 Starlette TestClient 的 httpx 弃用提示。提交后追加的全量回归以交付回复中的汇总为准。
- 线上未发布，App 接入与真机验收留待后续任务。App 可用 `asset_groups` 处理同名资产选择，并在展开相应区域后请求统计或市值。两个入口共用限频，调用需遵守 5 秒启动间隔。
- 工作树原有的 P12、P13、P14、P15、P16 任务书仍为未跟踪文件。P17 任务书随本次提交归档。
