# T2-1：配对区间回测后端核算

## 范围与基线

- 分支：`codex/zuudot-quote-age`；开始时基线 `d664552412818f97d62eb19cf3ea39a3d613a12a`。
- 实现 `POST /api/pair-spread/range-backtest`、纯函数核算、资金费分页和离线 pytest。未改前端、数据库写锁、collector、告警或部署文件。
- 工作期间其他任务提交了 `9c2c25b`（T2-2 图表时间轴）；本任务提交基于该提交，只包含本任务文件。
- 按用户要求只本地提交，不推送、不部署；没有进行线上备份、线上接口验证或全量测试。全量测试由 Claude 执行。

## 代码入口

- `backend/app/models/pair_range_backtest.py`：请求、响应模型。腿直接复用 `PairSpreadLegQuery`，保留 exchange、market_type、symbol、dex；aware 时间，`0 < end-start <= 30 天`，`0 < notional_usd <= 1e7`。
- `backend/app/api/routes_pair_spread.py::backtest_pair_spread_range`：沿用密码依赖、别名解析与 `pair_spread_query_service_factory`；优先读取现有 collector 的 `fee_settings`，不可用则采用 `FeeSettings()`；结束时关闭服务。
- `backend/app/services/pair_spread_query.py::query_range_backtest`：只抓两个端点各自附近的 K 线窗口、每腿一次资金费历史、每腿一次当前盘口。相同市场的两腿共用抓取结果。显式解析并保留 Hyperliquid DEX。
- `backend/app/services/pair_range_backtest.py::calculate_range_backtest`：纯函数，所有数据经 `RangeLegData` 和 `FeeSettings` 注入，不访问网络、数据库或系统时钟。
- `backend/app/services/funding_history_pagination.py`：资金费翻页、时间去重、排序、无进展检测、页数上限、保留部分成功数据；被既有资金费历史适配器复用。

## 核算与接口约定

- `notional_usd` 是**每条腿**的固定美元本金；各腿收益按自身入场价计算，收益百分比的分母为一个 `notional_usd`，不是两腿本金之和。
- 价格源为既有价差图 K 线 close，时间沿用图表 `bucket_at`。默认粒度 60 秒，跨度超过 7 天沿用图表规则使用 3600 秒。每个端点只查目标附近 ±5 个粒度，不回放整个区间；窗口没有有效价格时返回 502。
- `entry`、`exit` 各含 `leg1`、`leg2`，每腿含 `market`、`price`、`timestamp`、`target_at`、绝对偏差 `deviation_seconds`；等距离优先较早点。偏差严格超过 2 倍粒度才告警。
- 资金费严格计入 `(start_at, end_at]`；`rate_pct` 是百分数，`pnl = -position_sign * rate_pct / 100 * notional`。逐条结算附带 leg、exchange、symbol、dex，缺失不生成零记录。
- coverage 按当前结算周期与相位估计应有次数，实际次数除以应有次数，不从有缺口的历史推断周期。无法确定周期或现货为 null；明确应有 0 次且实际 0 次为 1；缺失为小于 1 并警告；周期变化可使比值超过 1，并警告。没有相位时按 UTC 对齐估算并明确提示。
- 每腿开平各一笔费：`2 * notional * spot/future_fee_pct / 100`。不额外叠加 `safety_slippage_pct`。
- 点差成本：每腿 `2 * notional * (ask-bid)/(ask+bid)`；任一腿盘口缺失、非有限数、非正数或交叉，则整个 `spread_cost_est=null`，total 按要求不扣点差并警告。
- 方向取反只使价格和资金费盈亏反号，手续费、点差成本仍是正数；因此有成本时 total 不简单反号。零成本用例验证 total 精确反号。
- `data_quality.source` 和 `granularity_seconds` 都是按 leg1/leg2 分项的对象；`gaps` 只列已知端点偏差或资金费缺失，不宣称核验了整个区间价格完整性。`is_estimate` 固定 true。
- 非法请求 422，鉴权失败 401，缺价格或市场身份失败 502，整次请求超过 60 秒为 504。HTTP 沿用现有超时，资金费单页另有 20 秒上限与最多 100 页限制。

## 资金费分页

- Binance/Aster：1000，向前推进 startTime；OKX：100，after 向后推进；Bybit：200，endTime 向后推进；Gate：沿用 1000，秒级 to 向后推进；Bitget：100，pageNo 递增；Hyperliquid：500，startTime 向前推进。
- Lighter/rh-lighter 继续用已有 700 小时分块；Arcus 继续用已有 1000 条向后翻页。
- OKX 改用实际 `realizedRate`，不拿预测 `fundingRate` 填补空结算。Gate 保留合法的数值零费率。
- 部分页失败或遇到无效结算，保留已解析的实际记录并给出 warning；不会默默把已经抓到的记录丢掉。
- Bitget 此接口没有时间筛选参数，查旧区间须从最新页向后翻；超时或 100 页上限会明确暴露，不能保证任意旧日期都有完整数据。
- 官方依据：[Binance](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History)、[OKX](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate-history)、[Bybit](https://bybit-exchange.github.io/docs/v5/market/history-fund-rate)、[Gate](https://www.gate.com/docs/developers/apiv4/en/futures/)、[Bitget](https://www.bitget.com/api-doc/classic/contract/market/Get-History-Funding-Rate)、[Hyperliquid](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)。

## 验证

- 新增测试 `backend/tests/test_pair_range_backtest.py`：0/1/3 次结算手算、收益方向、费用、资金费缺失/未知周期、开闭边界、重复记录、端点偏差、现货、盘口无效、DEX 身份、各交易所分页、游标卡死、部分超时、OKX 真实结算值、旧日期翻页、按腿抓取一次、端点窗口约束、路由 200/401/422/502/504 和服务关闭。
- 当前系统环境结果：新增 `46 passed in 1.83s`；既有 `test_pair_spread_query.py` 和 `test_api.py`：`175 passed, 1 warning in 31.73s`。既有 warning 是 `datetime.utcnow()` 弃用提示。
- 最终合并复验：`python -m pytest backend/tests/test_pair_range_backtest.py backend/tests/test_pair_spread_query.py backend/tests/test_api.py -q --basetemp=.pytest_tmp_t21`，结果 `221 passed, 1 warning in 30.29s`；仅该测试进程的 `PYTHONPATH` 指向当前 worktree 的 backend。
- 尝试创建 `.venv_t21` 并安装两份 lock；dev 依赖安装成功（含 pytest 8.4.2、pytest-asyncio 0.25.3、Ruff 0.16.8），业务依赖安装失败：`multidict==6.9.1` 从 `files.pythonhosted.org` 下载重试后超时。当前系统运行环境为 Python 3.12.10、FastAPI 0.135.1、Pydantic 2.12.5；尚未在 lock 指定的 FastAPI 0.162.0 完整环境验证，Claude 全量验收时须补跑。
- 新增文件通过 Ruff；修改的既有路由和查询文件通过 E4/E7/E9/F 检查；`git diff --check` 通过。
- 所有本任务 pytest 使用 worktree 内的 `.pytest_tmp_t21`，未运行全量。

## 遗留产物与下一步

- 原有未跟踪 `docs/zuudot-borrow-design-2026-11-01.md`、`.pytest_tmp_quoteage/` 保留且不纳入提交。
- 本地 `.venv_t21/`、`.uv_cache_t21/` 为测试依赖环境和下载缓存，不纳入提交；`.pytest_tmp_t21/` 如生成也不纳入提交。
- 核算是固定本金、历史 close、当前盘口成本的估算，未验证实盘可成交深度、历史手续费档位或历史结算周期变化；盘口不可用时 total 会少计未知点差成本，响应已明确告警。
- Claude 继续全量验收；后续 T2-3 前端拖选及结果面板在新任务执行，按上面的响应字段接入，并持续显示估算/coverage/缺口提示。
