# fill-sim 任务书（2026-11-09）

设计稿（必读，所有业务规则以它为准）：`D:/吴彬工作/claude/套利/research/small-lot-quant/paper-fill-design.md`。用户已按建议拍板 D-F1–F6：生产机运行；金额 100/300/1000/3000 USD；开仓门槛净 10bp；并发 ≤10；交易所 Gate、Bitget、Binance、Bybit、OKX、Aster（HL 第二批，本期不做）；观察 7 天。

本文件只写设计稿之外的工程约定。

## 1. 位置与运行环境

- 代码：`tools/fill_sim/`（独立包，**不得 import `backend/app`**）；测试：`tools/fill_sim/tests/`；systemd 模板：`tools/fill_sim/deploy/fill-sim.service`（参照下方现有服务）。
- 生产宿主机：Ubuntu，`/usr/bin/python3` = **Python 3.10.12**，**只能用标准库**（宿主机不装 pip 包）。HTTP 用 `urllib`/`http.client` + 线程或 asyncio 均可；SQLite 3.37。
- 现有同类服务单元（照抄风格）：`User=ubuntu`、`WorkingDirectory=/home/ubuntu/wubin/fill-sim`、`Restart=always`、`RestartSec=10`、`MemoryHigh=100M`、`MemoryMax=120M`、`OOMScoreAdjust=1000`、`Nice=10`。
- 配置：命令行参数 + 一个 env 文件（`EnvironmentFile=/home/ubuntu/wubin/fill-sim/fill-sim.env`），至少含 `TAOLI1_API_BASE`（默认 `http://127.0.0.1:8000`）、`TAOLI1_API_TOKEN`、`FILL_SIM_DB`、手续费 JSON、限频参数。token 不打日志。

## 2. 触发来源（与设计稿 §1 的差异，以此为准）

宿主机 ubuntu 用户读不到 docker 卷里的 radar.db，所以**不读 SQLite，改为轮询 taoli1 API**：

- `GET {TAOLI1_API_BASE}/api/shadow/trades?limit=50`，请求头 `X-Api-Token: <token>`，每 1 秒一次。返回 JSON 数组，按 `opened_at` 倒序，元素就是 `shadow_trades.payload`（示例见 §5）。不要带 `status` 参数（影子单可能 1 秒内就平了，我们要的是“新开”事件，不管它是否已平）。
- 用 `id` 去重；进程重启后从 fill_sim.db 恢复已见 id，只处理 `opened_at` 在 120 秒以内的新单（更老的记 `skipped_stale_trigger`）。
- 过滤按设计稿 §2：`strategy_version=="shadow-v2"`、`variant=="conv-half"`、`spread_tier` ∈ {0.5–1%, 1–2%, 2–5%, >5%}、`liquidity` ∈ {gt50k, 5k-50k}、`fake_flag==false`、两腿 `exchange` ∈ 支持列表。
- API 失败（超时 3s、非 200）：退避（2s 起，最长 30s），计数进状态；**不得提高轮询频率**。limit 固定 50，不要改大（taoli1 每次要序列化这些行，会占它的主循环）。

## 3. 交易所深度

- 只用公开深度/合约规格接口，无鉴权。你可以联网查官方文档并录制真实响应作为测试夹具（夹具存 `tools/fill_sim/tests/fixtures/`），**单元测试不得联网**。
- 腿的标识用 payload 里的 `buy_market_key` / `sell_market_key`：`exchange`、`market_type`（future/spot）、`raw_symbol`、`contract_size_multiplier`、`price_multiplier`。合约面值、数量步长、最小名义额从各所合约规格接口取（启动时拉、每 6 小时刷新、本地缓存），payload 里的 multiplier 用于交叉校验，不一致时该腿记 `spec_mismatch` 并跳过。
- 每家 50 档；Gate 合约深度数量单位是张，要乘面值。现货腿用现货深度接口。
- 限频严格按设计稿 §4.2：每家 ≤2 次/秒、全局 ≤8 次/秒的令牌桶；429/418/限频码 → 该所暂停 60 秒、指数退避；持仓跟踪优先于新开仓。

## 4. 输出

- `fill_sim.db`：表 `events`、`fills`、`positions`（字段按设计稿 §5，自行补齐时间戳 t0/t1/tA/tB、交易所盘口时间戳、延迟变体名、金额档、状态与原因）。启用 WAL；每天轮转 + 保留 30 天。
- 每日汇总：`out/daily-YYYY-MM-DD.json`，指标按设计稿 §5 与 §8（还在率、按金额档收益/胜率、容量曲线、影子偏差、去掉前 3 币、腿间延迟变体），重抽样按天做 90% 下界。
- 状态文件：`out/status.json` 每 30 秒覆盖写一次：运行时长、RSS、每家请求数/429 数/暂停中、API 失败数、持仓数、今日事件/开仓数。
- 命令行 `python3 -m fill_sim report --days 7` 打印设计稿 §8 的通过线逐条结果。

## 5. 影子 payload 示例（真实一行）

```json
{"opportunity_id":"7f841903573dc5c6","symbol":"AIOUSDT","buy_market_key":{"exchange":"gate","market_type":"future","raw_symbol":"AIO_USDT","dex":null,"price_multiplier":1.0,"contract_size_multiplier":10.0},"sell_market_key":{"exchange":"aster","market_type":"future","raw_symbol":"AIOUSDT","dex":null,"price_multiplier":1.0,"contract_size_multiplier":null},"opened_at":"2026-11-09T06:10:37.180725+00:00","closed_at":"2026-11-09T06:11:07.329534+00:00","open_spread_pct":0.8874729775856217,"exit_reason":"converged","holding_seconds":30.1,"spread_bps":100.0,"slippage_bps":120,"fees_bps":20.0,"net_bps":-39.98,"pair":"gate → aster","spread_tier":"0.5–1%","liquidity":"lt500","routes":"crossex/crossex","strategy_version":"shadow-v2","fake_flag":false,"executable":true,"id":"1a72d5e4b0eb43d1a79184c5e1e69d8d","variant":"conv-half"}
```

`buy` 腿 = 做多（买入）的一边，`sell` 腿 = 做空（卖出）的一边。

## 6. 约束

- 只新增 `tools/fill_sim/` 和本 docs 目录下的交接文件；不改 `backend/`、`frontend/`、compose、部署脚本、`.env`。
- 不下单、不读任何交易所密钥、不 SSH、不跑 WSL、不 push。可 commit（test/feat/docs 分开，conventional commits）。
- 不用第三方包（测试可用宿主机没有的 pytest，但被测代码必须纯标准库；最好测试也能用 `python -m unittest` 跑）。

## 7. 验收（实际执行，贴原始汇总行）

1. 单元测试：每家深度/规格解析（夹具）、VWAP 吃单与取整（含 below_min / too_thin）、三种延迟变体、平仓规则（收敛/24h/48h/stale_exit）、去重与并发上限、令牌桶与 429 退避、持仓优先、API 退避、重启恢复、每日汇总与 §8 通过线计算。
2. 测试汇总行（用 Python 3.10 兼容语法；本机若无 3.10，说明用的版本并确保没用 3.11+ 语法，例如 `tomllib`、`ExceptionGroup`、`asyncio.TaskGroup`）。
3. **联网冒烟一次**：用真实公开接口，对 6 家各取 1 个合约的深度和规格并打印解析结果（不需要 taoli1 API，可用一个手写的假影子事件跑完一轮开仓计算并打印 fills）。
4. 交接报告 `docs/task-handoff-2026-11-09-fill-sim.md`：文件清单、提交 hash、各所接口与限频依据（附官方文档链接）、手续费配置待核对项、剩余风险。

## PM decisions 第一轮补充

F1 资金费：PM 已核实 /api/shadow/trades 会删掉 payload.tracking（backend/app/db/repositories.py list_trades pop），不能依赖上游。允许 fill-sim 调用各所**公开**资金费接口（例如 Binance premiumIndex、Bybit tickers、OKX funding-rate、Gate 合约 contract/funding、Bitget current-fund-rate、Aster premiumIndex；以官方文档为准），在开仓时对合约腿各取一次 rate/下次结算时间/间隔，走同一令牌桶，按市场缓存 5 分钟。取不到才按现有逻辑标 provisional 并排除主收益；状态与日报里报告 funding_unknown 比例。现货腿资金费为 0。
F2 现货手续费：联网查 6 家官方 VIP0 现货 taker 费率并填入默认配置（合约费率同样再核一遍），在交接里附每条出处链接与查询日期；查不到的保持 null 并在报告列出。
F3 现货做空：现货腿出现在 sell（做空）一侧时不开仓，记 unsupported_spot_short，计入覆盖率分母，不进主收益。
F4 guard.json 由 PM 在服务器侧生成（不用你写生产监控），把它的字段契约（字段名、类型、过期秒数、缺失时行为）写进 tools/fill_sim/README.md 的单独一节，便于 PM 照着生成。
