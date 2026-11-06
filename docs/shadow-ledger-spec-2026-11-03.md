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

## PM decisions 第二轮（2026-11-03，回应只读审查打回 db05329）
1. **P1 恢复时资金费漏计**（`shadow_ledger.py:133/337`）：每个变体行各自保存自己的资金费结算进度（已计入的最后一个结算时刻），恢复时按行恢复，不共享。补测试：两个变体被分在不同批次提交、中途中断后恢复，各自资金费都正确（审查复现的 2/0 bp 用例必须变成 2/2）。
2. **P1 开关关闭时必须完全不运行**（`shadow_runtime.py:99/213`、`main.py:1015`）：`SHADOW_ENABLED=0` 时不创建工作线程、不注册行情消费者、不拉 CrossEx 目录给影子用、**不执行任何影子表的写入或删除**（包括保留期清理）。建表语句（`CREATE TABLE IF NOT EXISTS`）可以保留。`/api/shadow/status` 返回 `enabled: false`，其他影子接口返回空结果。补测试：关闭状态下用 SQLite trace 断言没有任何针对 `shadow_trades` 的 INSERT/UPDATE/DELETE，且没有影子线程。
3. **P2 接口读未提交数据**（`repositories.py:95/117`）：影子接口的读取用独立的只读连接（`file:...?mode=ro`，在 `asyncio.to_thread` 里执行），只能看到已提交的数据。补测试：写事务未提交时接口读不到这些行，回滚后结果不变。
4. **P2 日志限频**（`shadow_runtime.py:137`）：影子相关所有异常路径统一走限频日志（每类每 5 分钟最多一条，其余只计数，计数进 `/api/shadow/status`）。补测试。
5. 另外排查：全量测试第一次运行出现过一次 `PytestUnhandledThreadExceptionWarning`（重跑消失）。检查影子线程在测试结束 / 应用关闭时是否会在已关闭的连接上继续写或抛异常，确保关闭流程先停线程再关连接。

## PM decisions 第三轮（2026-11-05，生产规模预筛选）

背景：生产实测 5505 机会、约 12400 市场，其中 open_spread_pct ≥0.2% 约 643 个。原基准只测 1000 机会。按 5500 机会实测（本机）：事件循环中位 27 ms、p95 125 ms、最大 184 ms，工作线程 219–244 ms/轮，RSS 增量 45–84 MiB。生产 CPU 约慢 4 倍且后端已接近单核满载，不能开启。

1. **预筛选**：每轮只处理"相关机会"= `open_spread_pct >= shadow_min_open_pct`，或 id 已在持仓中的机会；再加上这些机会和全部持仓用到的市场。`freeze_snapshot`、worker 计算、display 缓存都只针对相关集合。持仓 id 集合由 worker 发布为不可变快照，事件循环不得读取 worker 正在修改的结构。
2. **显示**：不相关机会不带影子标注，发布时保留原对象、原顺序、原数量；效果与 `SHADOW_ENABLED=0` 时这些机会的显示相同。相关机会的标注不变。合并发布的代价计入事件循环预算。确认 `store.set_opportunities` 的其他消费者和前端能接受混合列表，补测试。
3. **账本语义不变（硬要求）**：加等价性测试，用固定随机种子的多轮合成序列（含跨门槛上下穿、机会消失、市场缺失、报价过期、冷却、触达 max_open、资金费结算、检查点、重启恢复），分别走全量路径和预筛选路径，断言开平仓、各分项、状态完全一致。若发现账本依赖了不相关机会或市场，停下报告。
4. **新增 `production` 基准场景**：5500 机会、12000 市场，其中 650 个 ≥门槛，750 对持仓，三分之一报价每轮变化。预算（本机，已按生产慢 4 倍折算）：事件循环中位 ≤5 ms、p95 ≤15 ms、最大 ≤40 ms；工作线程墙钟中位 ≤30 ms；RSS 增量 ≤30 MiB。
5. **既有场景**：`stress` 的事件循环中位预算由 3 ms 调为 5 ms，工作线程保持 20 ms 的 CPU 预算但墙钟断言调为 30 ms（PM 明确调整：该场景 1000 个机会全部相关，超过生产实际相关数，且需与 production 一致）。`realistic` 不动。其他断言不许放宽。
6. **基准运行**：在基准输出中记录运行前系统 CPU 负载；连续跑 3 次，报告全部 3 次原始值，全部达标才算通过。未达标不要改阈值，停下报告实测值和主要耗时位置。
7. 不开启生产；`SHADOW_ENABLED` 默认仍为 0。不引入独立进程。

### 第三轮补充（回应假机会评分依赖不相关市场）

`fake_opportunity.py` 的 median_outlier 要同资产其他交易所的现价，frozen_leg 要 15 分钟价格历史。冻结腿常常正是价差刚出现时才变成"相关"，所以只在相关后才开始记录会漏掉最该识别的假机会，不接受。决定：

8. **相关市场集合**扩展为：相关机会和持仓用到的市场，加上这些机会所属资产（`asset_id or symbol`）在快照中的全部市场。这些市场每轮送入评分器。
9. **分钟全量采样**：每跨一个分钟（与评分器现有 `new_minute` 对齐）额外把全部市场送入评分器一次，维持所有市场的 `PriceState` 与分钟样本；其余轮次不相关资产的市场不处理。全量轮的捕获计入事件循环 p95 和最大预算，production 基准必须包含这种轮次。
10. **等价性要求调整**：
   - 开平仓时间、各分项、持仓状态、`median_outlier`、`spread_plateau`、`volume_unknown`、`extreme_spread` 必须与全量路径完全一致。
   - `frozen_leg` 对从未相关的资产允许差异，仅限采样带来的：`changed` 时间最多晚 60 秒；分钟内先变后回的价格及分钟内高低点可能采不到。把这条差异写进代码注释和报告，测试断言偏差不超过一个采样周期；再加一个回归：资产长期冻结、价差刚越过门槛的第一轮开仓时，`frozen_leg` 必须已被识别（含 BMT 式样例）。
   - `spread_plateau` 的环形缓冲：机会跌破门槛后不再送入时，行为必须与全量路径的清除一致，补测试。
11. 全量路径只保留在测试里作对照基准，生产代码不留开关。若等价性还发现其他依赖，或分钟全量轮无法满足第 4 条的最大预算，停下报告实测值和耗时分布，不要改阈值。

### 第三轮补充二（回应 929a3f1：容量依赖与分钟全量轮超预算）

生产约 12400 市场，已超过 `max_markets=6000`：现有全量路径本身就会按出现顺序丢掉一半市场，它不能作为容量饱和时的等价基准。决定：

12. **容量**：`max_markets` 默认 20000。达到上限时相关资产优先准入：淘汰 `seen` 最旧的不相关资产市场；只有全部是相关资产市场才丢弃并计入 `dropped`。`dropped` 和当前 PriceState 数进入 `status`。淘汰要有上界，不能每次全表扫描。
13. **等价性范围**：第 10 条的完全等价只在两条路径都未饱和时要求。饱和时不要求与全量路径等价，改为断言：相关资产市场永不被不相关市场挤掉、`median_outlier` 按相关资产全部已准入市场计算。把 824f771 里两个容量反例改成这个语义（它们是本轮新增且基于旧上限，不算放宽既有测试），原 `test_shadow_fake.py` 的容量测试若需调整要在报告里写明原因。
14. **分钟全量轮分片**：全量市场捕获不得一次占住事件循环。分片捕获（每片最多 1000 市场），片间让出事件循环；第 4 条的最大 40 ms 按单次连续占用计。分片期间到来的新一轮跳过并计入 `skipped_rounds`。允许不同片的报价相差一个分片周期；只进入 frozen_leg 的历史，不影响当轮开平仓。
15. **普通轮的市场查找**：事件循环不得每轮遍历全部市场来找相关资产的市场。资产到市场的索引在 worker 或分钟全量轮里构建；相关机会的两条腿从 prepared 精确取得。同资产新出现的市场允许最多晚 60 秒进入 `median_outlier` 投票，这是第 10 条之外唯一新增的豁免，写进注释和报告。先查生产路径的行情对象是替换还是原地修改：若快照元组和对象实际不可变，可以直接传引用、不做逐对象冻结，把依据和测试写进报告。
16. **内存**：production 场景 RSS 仍按 ≤30 MiB。若 20000 容量或索引导致超标，报告实测构成后停下，不要改阈值。
17. **若全部实现后 production 仍不达标**：停下报告耗时分布，由 PM 决定是否改为按固定周期（例如每 5 秒）处理一轮，不要自行加节流。

### 第三轮补充三（回应只读审查打回 6051fe0）

18. **H1 发布失败不得影响持仓快照**：`_finish_round` 里持仓 id / 市场 key 快照的更新必须与发布解耦，发布抛异常时也要更新（`shadow_runtime.py:235、247`）。回归：开仓后发布失败，下一轮机会消失、盘口满足平仓时必须正常平仓。
19. **H2 身份刷新后不得重新发布旧机会**：在 `SnapshotStore` 增加机会列表版本号，所有 `set_opportunities`（含 `routes_market_identities._refresh_store` 走到的路径）都递增；影子在捕获时记下版本，发布前版本不同就放弃发布（计入 status），不影响账本处理。影子自己的发布也要正确处理版本。回归用真实 `_refresh_store`：刷新清空后影子任务不得恢复旧机会。不改变其他消费者的行为。
20. 这两条修复不改性能预算；修完重跑一次 production 基准，只记录，不因未达标停止。

### 第三轮补充四（2026-11-05，用户确认放宽预算并生产灰度）

21. 用户已拍板：放宽本机基准预算，是否可长期开启以生产灰度实测为准。`production`：事件循环中位 5→7 ms；最大 40→80 ms。`stress`：最大 20→40 ms。`realistic` 和其他预算不变。在基准注释和报告写明"本机基准只防退化，开启判断看生产灰度"。
22. status 需能看出因版本变化放弃发布的次数；若补充三已实现，只核对，不重复。

### 第三轮补充五（回应复审 7788172）

23. **版本绑定到快照**：机会版本要在快照发布时写入 `PublishedSnapshot`（或等效的入队元数据），影子用快照携带的版本做条件发布，不在 `submit` 时读取。回归：旧快照入队 → 真实 `_refresh_store` 清空 → 消费旧快照，不得恢复旧机会，且 `skipped_publications` 增加。
24. **持续弃发**：PM 接受，不加补发。默认 5 秒采集周期下计算不会每轮跨周期，账本处理也不受弃发影响，只影响显示标注；生产灰度盯 `skipped_publications`。
25. **stress 预算**：Windows `thread_time` 量化为 15.625 ms，31.25 ms 属量化噪声。stress 去掉 CPU 中位断言，保留墙钟 30 ms；最大 40→50 ms。其他不变。

## PM decisions 第四轮（2026-11-05，回应生产灰度：持仓被存量价差占满）

灰度事实：`open_count` 5 分钟内顶满 1500，未平 1499 行里 968 行开于前 5 分钟，1068 行是 `lt500`（24h 成交额 < 50 万美元），311 行现货/现货；`dropped` 约 9.7 万，是重复尝试次数。修复目标：账本记录"新出现的价差事件"，且不被单一类别占满。

26. **只在上穿时开仓**：只有机会从"低于 `shadow_min_open_pct` 或不在列表"变为"达到门槛"时才算事件。
    - 账本维护 `op_id → 最近一次达到门槛的时间`；持续 60 秒未达到门槛才重新具备上穿资格，防止列表短暂缺失造成假上穿。过期项要清理，内存有界。
    - 进程启动或恢复后的第一轮只登记，不开仓（计入 `baseline_skipped`）；该状态不持久化。
    - 平仓后仍需走原 cooldown，cooldown 结束后也必须重新上穿。
    - 报价不新鲜等暂时无法判断的情况：事件保留，下一轮重试，直到开仓或机会跌破门槛。容量或名额拒绝：事件作废，每个事件只计一次 `dropped`，价差不重新上穿就不再尝试。
27. **分类名额**（按行计，每对两行；上限取整后作为常量，不加环境变量）：`lt500` ≤ 30% × `max_open`；`unknown` ≤ 10%；现货/现货 ≤ 20%；单个 symbol ≤ 6 行。总上限 `shadow_max_open` 不变。开平仓和 restore 都必须保持计数准确。
28. **状态接口**：`/api/shadow/status` 增加 `dropped_by_reason`（capacity / liquidity / market_type / symbol，合计等于 `dropped`）、`baseline_skipped`。
29. **版本隔离**：`STRATEGY_VERSION` 改为 `shadow-v2`。restore 时，v1 的未平行不再进入账本和名额，改为已平：`exit_reason="retired"`，`closed_at` 取恢复时刻，`net_bps` 保持空；不删除任何历史行。`retired` 不进入任何收益统计。`/api/shadow/buckets` 默认只统计当前版本；显式传 `strategy_version` 时才看旧版本。
30. **不改**：止损、收敛、24h/48h 超时、滑点模型、`liquidity_tier` 的算法和存储标签、默认 `SHADOW_ENABLED=0`（关闭时不运行）、混合发布和性能预算。
31. **验收**：
    - 先写失败测试：启动存量不开仓；上穿开仓；短暂缺失不算上穿；重新上穿；各名额和 restore 后计数；dropped 每事件只计一次；v1 退役且不进统计。
    - 预筛选路径与全量路径的多轮等价性测试必须覆盖上穿、基线轮和名额。
    - backend 全量测试通过。production 基准跑 3 次，原始汇总行写入报告；基准因基线轮需要调整的只能是场景准备，不能放宽预算。未达标如实记录，不要为达标改预算。
    - 若发现账本行为依赖第 26–29 条未说明的取舍，停下写入报告，不要自行决定。

### 第四轮补充一（回应 ce1513e 的冷却问题）

32. **退役不触发冷却**：`retired` 不是真实平仓，既不写入也不恢复 v2 的 cooldown；重启后从历史行重建 cooldown 时也要排除 `retired`。退役机会仍要遵守第 26 条：基线轮只登记，重新上穿才开仓。

## PM decisions 第五轮（2026-11-06，影子计算移出主进程）

33. 影子账本的重计算（市场预筛选、_calculate/_work、ledger 状态、shadow 表的读写与恢复）全部放到一个独立子进程中执行；主进程不再持有 ShadowLedger 状态，也不再为影子开线程做 CPU 计算。用 multiprocessing 的 spawn 上下文，子进程只 import 影子需要的模块（不 import FastAPI 应用、采集器）。
34. 主进程每轮只做：从快照中抽取影子所需的最小输入（只含预筛选需要的字段，原样 dict/tuple，可 pickle），非阻塞地发给子进程；子进程忙（上一轮未完成）时跳过本轮并计入 skipped_rounds。主进程侧抽取+序列化的 CPU 时间在状态中报告为 main_cpu_ms，目标中位 <5ms（生产规模 ~4600 条目录、~12000 价格状态）。
35. 结果回传（annotations/状态）通过子进程→主进程的队列异步读取，读取不能阻塞事件循环（不得在事件循环里同步 get；可用专用读取线程只做 I/O 和反序列化，或 loop.add_reader 一类方式）。现有 API 输出字段与语义不变；状态新增 worker_pid、child_rss_mb、main_cpu_ms、child_restarts。
36. 故障隔离：子进程崩溃/超时/异常绝不抛到主流程。崩溃后最多自动重启 3 次（指数退避，从 5s 起），之后标记 error 并保持停用直到后端重启。子进程 RSS 超过 SHADOW_CHILD_MAX_RSS_MB（新配置，默认 250）时停止子进程、记 error、不再重启。aclose 时先请求优雅退出（落盘 pending writes），10s 未退出则 terminate。子进程独占 SQLite 写入 shadow 表；主进程不得再写 shadow 表（只读 API 若需查询，沿用现有读路径并确认在 WAL 下无锁冲突）。
37. 新配置 SHADOW_MIN_LIQUIDITY_USD（默认 0 = 不过滤，行为与现在一致）：24h 成交额低于该值、或成交额未知的机会，在准入阶段丢弃并计入 dropped_by_reason.liquidity。是否在生产设置由 PM 决定，你不要改任何 .env。
38. SHADOW_ENABLED=0 时不得启动子进程，开销为零。

## PM decisions 第五轮补充一（原文追加到 spec 末尾）
39. 主进程每轮的抽取与序列化必须是“增量”的，子进程保存镜像：
   a. 执行目录 catalog.entries 只在 catalog.updated_at（或等价版本）变化时发送，不得每轮 pickle。
   b. 市场目录字段（directory_values）只发送新增/删除/字段变化的市场；判断变化不得每轮对 12000 个市场全量构建 tuple 再比较——优先用市场对象身份、快照/存储已有的版本号或时间戳；若没有可用版本，可在 store 侧加一个最小的变更计数，但不能改变现有公开行为。
   c. 报价字段（market_values）只发送自上一轮以来变化的市场（按报价时间戳或版本），子进程按 key 合并。
   d. 阈值（id/spread）可以每轮发，但用紧凑编码（例如 array('d') + 顺序/ID 表仅在集合变化时发送），不要逐个构建 dict→tuple。
   e. 子进程镜像与主进程不同步（序号断档、子进程重启、队列丢弃）时，下一轮发全量重同步，计数 resync_count 进状态。
40. 主线程单段占用：抽取工作按块切分（每块后 await asyncio.sleep(0)），event_loop_max_segment_ms 中位 ≤3ms；整轮主线程墙钟合计中位 ≤5ms（Windows 下 thread_time 有 15.6ms 量化，以 perf_counter 墙钟为准，并在报告里说明）。
41. 基准预算不放宽：production 场景 loop 中位 ≤7ms，stress 场景 ≤5ms，realistic 照旧。正确性：增量模式与全量模式在同一输入序列上产生完全相同的开/平仓记录（新增等价测试，含 resync 场景）。

## PM decisions 第五轮补充二（原文追加到 spec 末尾）
42. P1 关闭通知不得依赖跨进程锁：backend/app/services/shadow_runtime.py:540 同步调用 multiprocessing Event.set()，子进程（shadow_worker.py:187 is_set()）持锁时被杀会留下死锁，阻塞主事件循环。改为不依赖子进程持锁的退出通知（例如经已有队列发送 stop 消息，或主进程端非阻塞写 pipe），并在 _dispose() 回收时丢弃旧通知对象；退避期间关闭不得访问已回收对象。
43. P2 有界关闭：shadow_runtime.py:468 terminate() 后无限 join。统一一个关闭期限（总计 ≤15s）：优雅退出 10s → terminate → join 2s → kill → join 2s；所有阻塞 join 放到线程里，不阻塞事件循环；_shutdown 先建立期限再等监督任务；aclose 的异常不得穿透 lifespan，用 finally 保证其余资源继续关闭（记 error）。
44. P2 统计接口：shadow_runtime.py:396 在主进程对全部历史行同步 pickle。改为只把统计查询参数发给子进程，由子进程读 SQLite 计算并回传结果；结果大小有上限，主进程侧无 O(行数) 工作。API 输出不变。
45. P2 快照冻结语义：生产代码直到 shadow_runtime.py:363 才读报价，之前已多次让出，期间原地修改会进入本轮计算。恢复一致冻结：一轮内所有分片使用同一时刻的输入（例如在第一次让出前记录本轮需要的引用/版本，或检测期间变化则本轮作废并计 skipped_rounds）。并把 backend/tests/test_shadow_selection_runtime.py:44 这类“分片期间修改报价仍使用原值”的回归接到真实 spawn 路径，不能只测 harness。

## PM decisions 第五轮补充三（原文追加到 spec 末尾）
46. 第 45 条的冻结改为“引用冻结”。依据（PM 已核实）：backend/app/services/snapshot_store.py 的 set_markets/set_opportunities 整体替换列表，get_* 返回列表副本；生产代码中没有对已发布 MarketSnapshot/Opportunity 字段或其列表的原地修改。因此已发布对象视为不可变，这是影子运行时依赖的契约，写进 spec。
   a. submit 在第一次让出前只做 O(n) 的引用捕获：tuple(snapshot.markets)、tuple(snapshot.opportunities)、catalog 版本与 entries 引用；后续所有分片都从这份引用元组读取。不得逐对象复制 __dict__ / 列表。
   b. 回归测试改为验证替换语义：在真实 spawn 路径下，分片进行期间 store 被 set_markets/set_opportunities 替换成新列表（含报价变化、市场增删），本轮仍完全使用旧引用的值，下一轮使用新值。删除或改写依赖“原地修改已发布对象”的用例，并在报告中列出理由（契约变更，不是放宽）。
   c. 增加一个契约守护测试：扫描 backend/app（不含 tests、shadow 模块）中对 MarketSnapshot/Opportunity 实例字段的属性赋值或其列表字段的 append/extend/clear 等原地修改（AST 级即可，覆盖常见变量名不够就按类型注解/调用来源做最佳努力），发现即失败，以防将来有人打破契约。
47. 性能目标：production loop 中位回到 ≤15ms（第六轮水平 12.9–15.0ms），单段中位 ≤3ms，RSS 增量 ≤30MiB；原 7ms 预算断言保持不变（已知不达标，不要改）。
