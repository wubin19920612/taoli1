# 任务书：影子账本 + 机会面扩张 + 假机会识别（taoli1，阶段 1，2026-11-03）

设计来源：`D:/吴彬工作/claude/套利/research/small-lot-quant/design.md` §1.3、§3、§5.4（用户 11-03 已确认）；执行通道见 `research/gate-crossex/crossex-research.md`。

**分支**：派发时从最新生产提交拉 `codex/shadow-ledger`（先 fetch，生产提交以部署分支为准，`main` 很旧，不能用）。taoli1 同时有其他会话在改 SQLite 相关代码（s6-sqlite / S4），**改 `app/db/schema.py`、`repositories.py` 前先报告 PM 确认没有冲突**。

## 范围
- 新增：`app/services/shadow_ledger.py`（实时跟踪 + 记账）、`app/services/shadow_stats.py`（统计）、`app/services/fake_opportunity.py`（假机会评分）、`app/services/execution_routes.py`（执行通道目录）、对应路由与测试、前端"影子账本"页。
- 允许修改：`alert_engine.py`（只改成交额缺失那一处）、`spread_engine.py`（只在机会上附加字段）、`app/main.py`（注册消费者与后台任务）、`app/core/config.py`（新配置）、`app/db/schema.py` + `repositories.py`（新增 `shadow_trades` 表与读写）、`app/models/opportunity.py`（新增可选字段）、前端路由 / 导航。
- 禁止修改：建卡、Astro / arb-mvp 下单相关代码（`astro_*`、`arb_mvp_*`、`fs_astro.py`）、采集器调度、`opportunity_history` 的写入逻辑。
- 不推送、不部署、不连服务器；本机 pytest 临时目录用 `--basetemp` 放进 scratchpad。

## A. 成交额缺失不再一票否决
- `opportunity_matches_rule`：删掉"规则要求最小成交额且任一腿成交额为 None 就 return False"这条；已知成交额仍按原规则比较。
- 新增风险标签 `volume_unknown`（任一腿成交额为 None 时打上）。规则如果想继续拦，可以把它放进 `excluded_risk_labels`。
- 顺带查清币安部分合约 `volume_24h` 为空的原因，写进报告（**只查不修**，修复另开任务）。

## B. 执行通道标注（`execution_routes.py`）
每个机会的每条腿附加 `routes: list[str]`，取值：
- `native`：该交易所在 arb-mvp 主网直连白名单里。现在是 `hyperliquid`、`lighter`、`bitget`，做成配置 `NATIVE_ROUTE_EXCHANGES`，默认这三个。
- `crossex`：该腿能在 Gate CrossEx 找到同一个合约且 `state=live`。
  - 目录来源：`GET https://api.gateio.ws/api/v4/crossex/rule/symbols`（免鉴权，约 5 MB）。每 30 分钟拉一次，失败沿用上次结果、15 分钟后重试。**解析后只保留紧凑集合** `{(exchange_type, business_type, base, tag)}`，不要缓存原始 JSON。
  - 交易所映射：binance→BINANCE、okx→OKX、bybit→BYBIT、gate→GATE、aster→ASTER、hyperliquid→HYPERLIQUID、lighter→LIGHTER、kraken→KRAKEN、deribit→DERIBIT。bitget 没有。
  - 合约名：CrossEx 是 `{EX}_FUTURE_{BASE}_{QUOTE}`。base 只做**精确匹配**（用 market identity 的 raw_symbol 去掉报价币），不做模糊匹配；ANTHROPIC / ANTH / ANTHROPICX 这类不一致的一律算"匹配不上"。
  - HL 用 `tag` 区分 DEX：`""` 是主 DEX，`xyz`、`para` 是 HIP-3；必须与腿的 dex 一致才算有。复用并泛化现有 `gate_crossex_hyperliquid.py` 的做法（它已经按 tag 判断），**不要破坏 Astro 告警对它的使用**。
- 机会级字段 `executable: bool` = 两条腿各自至少有一个通道。

## C. 影子账本（实时跟踪，不用 `opportunity_history`）
`opportunity_history` 每 120 秒采样、只存开仓 ≥0.5% 的机会、保留 3 天，价差收敛后序列就断了，看不到平仓，所以**不能用来回放**。影子账本挂在每轮采集结果上实时跟踪：

**开仓**（每轮对每个机会判断）：
- 开仓价差 ≥ `SHADOW_MIN_OPEN_PCT`（默认 0.2）；两腿都是可执行盘口（`_has_executable_book`）；两腿报价年龄 ≤ 10 秒；同一 `opportunity_id` 没有未平的影子单；距该 `opportunity_id` 上次平仓 ≥ 10 分钟。
- **假机会也开**，但 `fake_flag=1`，统计时默认排除（用来验证假机会规则本身）。
- 不要求 `executable`，但记录下来（以后按"能否执行"分桶）。
- 同时未平的影子单上限 `SHADOW_MAX_OPEN`（默认 3000），超了就丢弃新的，并计数。

**跟踪与平仓**：每轮用两腿**当前盘口**（不管它还是不是"机会"）重新算平仓价差。先读 `spread_engine.py` 确认 `open_spread_pct` / `close_spread_pct` 的符号与公式，平仓价差用同一套口径。
- 每个影子单同时跑多个**策略变体**（同一个开仓，各自平仓，各算一笔）：
  - `conv-0.1`：平仓价差 ≤ 0.1% 时平；
  - `conv-half`：平仓价差 ≤ 开仓价差的一半时平。
- 共同规则（按优先级）：
  1. 任一腿行情消失或过期 ≥ 10 分钟 → 以最后可用价平，原因 `stale`；
  2. 止损：平仓价差 ≥ 开仓价差 + max(开仓价差, 1%) → `stop`；
  3. 收敛 → `converged`；
  4. 持有 ≥ 24 小时且平仓价差 ≤ 开仓价差一半 → `timeout`；持有 ≥ 48 小时无条件平 → `timeout_hard`。

**记账**（单位 bp，口径对齐设计稿 §1.2）：
- ① 价差收益 = 开仓价差 − 平仓价差；
- ② 滑点（估计）：按流动性档取固定值，可配置，默认 `gt50k:2`、`5k-50k:5`、`500-5k:15`、`lt500:30`、`unknown:30`（单边、每次成交），开平两腿共 4 次。流动性档暂按两腿较小的 24h 成交额分（>5000 万 / 500 万–5000 万 / 50 万–500 万 / <50 万 / 未知），报告里写明这是临时口径；
- ③ 手续费：复用 taoli1 现有的每所吃单费率设置（`effective_open_edge_pct` 用的那套），开平各两腿；
- ④ 资金费：持有期间跨过的每个结算点，按当时两腿资金费率差累计；
- 净 bp = ① − ② − ③ + ④。

**存储**：新表 `shadow_trades`，一行一笔（一个开仓 × 一个变体），开仓时插入、平仓时更新。字段：id、variant、opportunity_id、symbol、买 / 卖腿的 MarketKey（JSON）、opened_at、closed_at、开 / 平仓价差、exit_reason、holding_seconds、①–④ 与净值、桶标签（交易所对、价差档位、流动性档、通道组合如 `native/crossex`、strategy version）、开仓时两腿报价年龄、fake_score、fake_reasons、executable。按 `closed_at` 保留 30 天。重启后从表里恢复未平的影子单继续跟踪。

**桶维度**：variant、交易所对（有序）、价差档位（与 arb-mvp 一致：`<0.2%`、`0.2–0.3%`、`0.3–0.5%`、`0.5–1%`、`1–2%`、`2–5%`、`>5%`）、流动性档、通道组合、是否假机会。

## D. 假机会评分（`fake_opportunity.py`）
在内存里给每个市场维护"上次中间价、上次变动时间"，给每个 `opportunity_id` 维护最近 60 分钟、每分钟一个样本的价差环形缓冲（只给开仓 ≥1% 的维护）。规则：

| 类型 | 条件 | reason |
| --- | --- | --- |
| 硬 | 某腿中间价偏离同一资产多家交易所中位价 > 3%，且其余 ≥ 2 家彼此差 < 0.5% | `median_outlier` |
| 软 | 某腿中间价 15 分钟没变，同资产在别的交易所 15 分钟内波动 > 0.3% | `frozen_leg` |
| 软 | 1 小时价差均值 > 1% 且标准差 < 均值的 5% | `spread_plateau` |
| 软 | 任一腿成交额未知 | `volume_unknown` |
| 软 | 开仓价差 > 5% | `extreme_spread` |

硬条件命中、或软条件 ≥ 2 条 → `fake_flag=1`。结果作为机会的可选字段 `fake_score` / `fake_reasons` 返回给前端（只展示，本任务**不**据此拦告警）。

**回归用例**：构造类似 bitget BMT 的合成序列（bitget 腿价格 1 小时不动，币安等 3 家同步波动，价差稳定在 2.5% 左右），必须被判为假机会（同时命中 `median_outlier` 或 `frozen_leg` + `spread_plateau`）。

## E. 统计、接口、页面
- `shadow_stats.py` 的统计口径**与 arb-mvp 任务书 §6 完全一致**（`arb-mvp/docs/ev-ledger-spec-2026-11-03.md`）：Wilson 胜率区间、t 下界、按天整块重抽样（固定种子，2000 次）、`lcb` 取两者较小值、n_min = max(30, ceil((1.645σ/E)²))、`INSUFFICIENT` / `POSITIVE` / `NOT_POSITIVE` 三种状态（门槛：事件 ≥ n_min、天数 ≥ 5、币 ≥ 3）、CVaR5。一笔影子单就是一个事件。
- 接口（需登录，与现有 API 一致）：`GET /api/shadow/buckets?dims=...&since=...&until=...&include_fake=0`、`GET /api/shadow/trades?limit=...&status=open|closed`、`GET /api/shadow/status`（开启状态、未平数量、丢弃计数、目录更新时间）。
- 前端"影子账本"页：维度多选 + 桶表格（状态、事件 / 天 / 币、胜率、E、lcb、σ、四项分量、n_min、CVaR5）+ 最近影子单列表。中文。

## F. 配置与上线保护
- `SHADOW_ENABLED`（**默认 0**，上线后观察资源再开）、`SHADOW_MIN_OPEN_PCT=0.2`、`SHADOW_MAX_OPEN=3000`、`SHADOW_SLIPPAGE_BPS`（JSON）、`NATIVE_ROUTE_EXCHANGES`、`CROSSEX_CATALOG_REFRESH_SECONDS=1800`。
- 资源预算（生产机 1.9G 无 swap）：3000 个未平影子单时每轮新增耗时中位数 ≤ 5 ms、常驻内存增量 ≤ 30 MiB。写库批量提交，不要每笔一个事务。
- 影子账本异常不能影响采集和告警主流程：全部 try/except 隔离，计数 + 每 5 分钟最多打一条日志。

## 测试
1. A：成交额 None 不再拦、标签 `volume_unknown` 可被规则排除；原有告警测试除这一条行为外不许改。
2. B：目录解析（含 HL tag、Lighter、bitget 无、名字不一致匹配不上）、拉取失败沿用旧结果。
3. C：开仓条件各分支；两个变体各自平仓；四种平仓原因；资金费跨结算点；上限丢弃；重启恢复。
4. D：每条规则正反例；BMT 回归用例。
5. E：统计与手算一致、重抽样同种子结果相同；接口未登录 401。
6. 性能：合成 3000 个未平影子单 × 1000 个机会的单轮基准。

## 验收（Claude 执行）
- 锁定依赖的 venv 跑后端全量 pytest、前端测试、tsc 全部通过（现有测试不许放宽）。
- `git diff --name-only` 只含范围内文件。
- 本机用真实公开行情跑 10 分钟（`SHADOW_ENABLED=1`）：有影子单开平、目录有 HL/Lighter/CEX 条目、内存与耗时在预算内。**本机跑完立刻停**（本机后端走生产机出口，会挤占生产，见 11-03 事故）。
- 只读 Codex 审查：符号方向、过期判断、异常隔离、内存上限、是否改了禁止范围。
- 输出报告 `docs/shadow-ledger-report-2026-11-03.md`。

## PM decisions（2026-11-03，回应 ff22a89 性能未通过）
基准：常规轮中位 29.85 ms、P95 69 ms，首次开仓批量 209 ms、检查点 184 ms，都在事件循环上执行，会造成生产卡顿。按下面方向返工，**不许放宽或删除基准断言，只按下面重新定义预算**：
1. **只算变化的部分**：未平影子单按两腿 MarketKey 建索引；每轮只重算"至少一条腿报价代次变化"的影子单。假机会评分里的价差环形缓冲只给开仓 ≥1% 的机会维护（任务书原文），市场冻结检测按市场增量更新，不要每轮全量扫描。
2. **离开事件循环**：每轮的影子计算放到 `asyncio.to_thread`，输入是本轮快照的不可变引用；同一时间只允许一轮在跑，上一轮没跑完就跳过本轮并计数（`skipped_rounds` 进 `/api/shadow/status`）。所有 JSON 编码和写库也放进线程，单批 ≤ 200 行，分批提交。
3. **新预算**（基准里分别断言）：
   - 事件循环线程上每轮耗时：中位 ≤ 3 ms，P95 ≤ 10 ms；任何单次 ≤ 20 ms（含首次开仓和检查点）。
   - 工作线程每轮 CPU：压力场景（1000 机会 / 3000 未平）中位 ≤ 20 ms；**真实规模场景**（400 机会、其中 1/3 每轮报价变化、1000 未平）中位 ≤ 8 ms。两个场景都要跑、都要报。
   - 常驻内存增量 ≤ 30 MiB 不变。
4. `SHADOW_MAX_OPEN` 默认改为 1500。
5. 其他实现细节自行决定，写进报告。
