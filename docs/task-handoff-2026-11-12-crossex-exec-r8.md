# crossex-exec 第八轮交接（2026-11-12，本地验收完成）

> 当前最新交付见文末[第十二轮](#第十二轮)：PM 已批准公开现货中位价 ±0.2% 的外仓结算币保守折算；负债继续停开，旧日亏锁不因登记/折算成功清除。含 Claude 线上解锁操作顺序。本轮仅本地交付。

> 当前最新交付见文末[第十一轮](#第十一轮)：按用户选择实施外仓方案 (b)，永久禁单、收益核实与全户硬风控隔离。`io:OAI_USDC` 非零贡献的历史折算证据仍不足，按要求停开；没有推送、部署或实盘验收。下方旧轮次保留作历史证据。

> 当前最新交付见文末[第十轮](#第十轮)：baseline 故障与管理写并发修复、双版本验收、外部持仓待选方案。下方各旧轮次与合并记录保留作历史证据。

> 最新交付见文末 [合并终审修复](#合并终审修复)：`codex/crossex-exec` 的 `abf8dba` 已用 `--no-ff` 合入，本地代码验收以该节为准。下方第八轮原始交付及性能测量保留作历史证据。

第八轮实现、双版本两套 unittest、不变量模拟器、采样 CPU/内存实测及接口契约已完成。本节取代文末历史停点；原 WIP 已保留并接续提交。**用户明确限定本地交付：不 push、SSH、WSL、amend 或真实鉴权。本轮没有部署或实盘验收。**

## 范围、基线与本地提交

- 模块：crossex-exec 第八轮；任务书 [crossex-exec-round8-2026-11-12.md](crossex-exec-round8-2026-11-12.md)，另包含用户授权的公共采样 meta/脱敏热点优化。第九轮 research 文件不在仓库，仅使用用户提供的热点摘要，没有声称读到该文件全文。
- 工作树：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\crossex-exec-r8`；分支 `codex/crossex-exec-r8`；本次基线 `f125c4d4377d35a1c7ee4bd7856bcdebfc7007fd`。
- 起始 7 个已跟踪修改与 6 个新模块全部保留接续。启动备份 `%TEMP%/crossex-r8-resume-20261112T021055Z.zip`，SHA-256 `8d1b333cada6bc66cca8eddd6e91a0d68755154a4417ddb900c1c1c8b2d6980a`，包括 WIP、基线/diff 和性能对比旧模块；它是本地源码备份，**不是线上数据库备份**。
- `9bbc257b6e7231dfa758c1585c08d276d1481fe8`：test，六个测试文件，旧成交额测试配置、映射/风控/采样回归和模拟器扩展。
- `92dc928fccb4ebc85b4d5cdddfc7d6ffd5938f8a`：feat，参数、分类/映射、动态定量、API、冻结版本、类型化采样存储和测量入口。
- 本文、契约、README、next-task 和性能 JSON 为独立 docs 提交。自身 hash 用 `git log -1 --format=%H -- docs/task-handoff-2026-11-12-crossex-exec-r8.md` 获取，不 amend。
- 显式暂存本模块文件；Git 操作仅本地，hooks/GPG 在本次提交命令中关闭。父仓库 Git 索引写入已按本地提交授权获自动审批；没有自动审批拒绝。未改前端、fill-sim 源码、`execution.py`、真实 env 或部署模板。

## PM 裁决与旧测试适配逐项记录

本次明确批准三类原任务书已改变的机制可用“显式旧配置 + 原断言保留 + 新默认测试”适配。实际只需对旧成交额测试增加配置；没有借此修改其他风控断言。

| 项目 | 本次实际修改 | 新默认/回归证据 |
|---|---|---|
| 500 万→默认 100 万 | `test_engine.py::test_actual_fees_missing_or_low_volume_never_sends` 只增加 `Settings.update({'min_quote_volume_usd':5000000}, source='admin_api')`；原 4,999,999/9,000,000 输入、missing_fees 分支及零发单断言原样保留 | `test_round8_engine.py::test_default_million_accepts_4999999_with_other_guards_satisfied` 验证默认可开；运行中调门槛及低成交额/缺失成交额发送前零发单另有测试 |
| 固定 100U→深度/OI/上限定量 | 本次无需改旧测试数值断言；独立旧纯估算函数保留省略 quantity 时的 100U 兼容值，真实 Engine 总是显式传入定量结果 | `test_round8_completion.py::test_default_quantity_is_depth_sized_and_unconfirmed_fails_closed`，以及 `test_round8_sizing_contract.py` 四类约束、步长、最小额、单腿 OI 测试 |
| 固定 CROSSEX_BASES→成交额/类别 | 本次无需替换旧白名单断言；显式名单仍是用户要求的额外限制 | `test_new_default_unrestricted_bases_and_explicit_legacy_constraint` 用同一新代码验证显式 BTC 限制拒绝、空名单默认接受；原人工排除测试保留 |
| 只增不删的 API 字段 | `test_api.py` 的完整键集合只追加 config/sizing/turnovers、有效风险限额和原生单位字段；原键与全部原值断言保留 | 新映射全链路测试同时校验公共单位、原生数量/价格、费用和净损益 |
| 不变量模拟器夹具 | 内存 Store 增加事务及按行基准接口；假交易所原生量/价与公共单位明确换算，I3 的原生步长换算为公共步长；倍率 1 的旧断言等价。仅供 PM 小夹具的缺失 instrument 默认为倍率 1 | 原 2,400 次随机故障输入和原两段 48 回合尾部仍运行；新增场景继续逐回合执行 I1a–d、I2–I7，无跳过 |
| 新行情/类别边界 | 仅新增测试：批量接收时间、90 秒采样新鲜度、股票选择/外部参考、硬排除类别及排序后价格比 | 原断言均保留 |

之前已批准的 PM 裁决继续适用：向下取整后不足最小数量/名义额记 size_too_small、不补步长；只有两腿 OI 都无效才 oi_missing，单腿有效可用；未知分类不默认 crypto；Binance OI 以数量/value 与标记价交叉核验，Bitget 用 V2 size，OKX 成交额用基础量×low24h 保守下界。此前 `test_round3.py` 两个最小量断言替换及提交号保留在文末历史证据中，本次未再次改写。**本次没有剩余风控语义冲突或待 PM 裁决项。**

## 已完成实现与关键入口

- `settings.py`：全部参数的范围、严格数值类型、版本、原子审计、重启恢复和默认回退告警。管理 API 与共享发送关口按数据库路径共用锁，配置在开仓初判冻结，发送前检查相同版本；已经通过共享关口的批次沿用该版本，不让一次更新只作用于其中一腿。
- 已持仓冻结完整参数快照。三个账户损失门槛按活动仓位的冻结值取最严格者，旧无快照持仓按旧默认值；空仓用当前配置。新门槛更严且账户已越线时只拒绝新开仓，不重写旧仓快照；独立权益回撤原全平停机路径保持。环境/代码硬上限继续叠加。
- `sizing.py`、`open_interest.py`：每腿预算受单笔、较薄腿容忍带内深度、有效 OI、双腿剩余总额约束；最终限价参与名义额与总额计算，公共步长向下取整。费用、资金费周期、余额、盘口时间、OI 单位/身份/300 秒时效等检查独立存在。
- `universe.py`、`liquidity.py`、`market.py`：全目录精确市场身份、公开批量成交额、crypto/股票/未知分类、排除、US/KRX 选择、映射发现及确认。pre_ipo/commodity 和指定禁止标的不因人工 crypto 或别名重新开放。股票未选中的所既不能开仓、凑成交额家数，也不能当第三家参考；未知不自动当币。
- `policy.py`、`engine.py`、`accounts.py`、`limits.py`：公共数量与原生订单数量贯穿双向开仓、部分成交、补平、实仓核对、重启、减仓和 PnL；原始 symbol/exchange/multiplier 保留。旧仓不因后来拒绝映射或下调额度而失去减仓能力。新代码需要费率时先按精确合约刷新，再做准入检查。
- `api.py`、`management.py`、`api_data.py`：独立且不同的管理/只读令牌、严格有界 JSON、写入事务、只读连接、版本/审计/交易范围/映射路由；原业务字段保留，新增 sizing、成交额、原生单位及有效风险限额。Python 3.10 空 query 和拒绝请求体残留问题已覆盖。
- `public_store.py`、`storage.py`、`baseline.py`：样本、每对基准状态、最新报价和成交额落类型化 SQLite 行；旧三个 meta blob 原子迁移后移除，结构性锁存保留。生产每对更新只操作自身状态，不再每次读写整个状态字典；公共字段只写数值/身份白名单，不做递归凭据脱敏。订单、事件、配置和对外 API 继续脱敏。
- 公共采样仍每 30 秒，默认成交额预筛 20 万，不受 CROSSEX_BASES 影响；最多 8 个批量公开请求，经既有队列限频。成交额缓存 300 秒与中间价用于新样本的 90 秒限制分开；批量响应到达后记录接收时点，不用请求开始时间误拒绝有效交易所时间。
- **未改**：24h 样本留存、6h 中位数/MAD 窗口、至少 300 点且跨度 3h 的预热、90 秒参考新鲜度、10 分钟结构性重评、冷却、资金费成本、时间重拉、未知订单不重发、裸露/穿零/恢复和原止盈止损语义。样本全量历史没有常驻 Python 内存。

## 最终验收与复现

本机 Python **3.12.10 / SQLite 3.49.1**；用户指定解释器实际为 **Windows Python 3.10.20 / SQLite 3.50.4**，不等同于生产 Ubuntu Python 3.10.12。执行下列两条 unittest，两版本都全过：

```powershell
python -m unittest discover -s tools/crossex_exec/tests -v
python -m unittest discover -s tools/fill_sim/tests -v
$py310 = 'C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/125141d8-f3d5-4518-98a0-6a4761963215/scratchpad/uvpy/cpython-3.10-windows-x86_64-none/python.exe'
& $py310 -m unittest discover -s tools/crossex_exec/tests -v
& $py310 -m unittest discover -s tools/fill_sim/tests -v
```

| 解释器 | crossex-exec | fill-sim |
|---|---|---|
| 3.12.10 | 323 项，102.315s，OK | 158 项，29.907s，OK |
| 3.10.20 | 323 项，108.626s，OK | 158 项，32.003s，OK |

最终增加两个行情时间回归后重跑了两版本完整 crossex-exec；fill-sim 未变，沿用同次交付已通过的完整结果。另 50 个 Python 文件按 3.10 语法 `ast.parse(feature_version=(3,10))` 检查通过，`git diff --check` 通过；LF/CRLF 提示不是测试失败。

每个版本模拟器种子 **20261111、20261112、20261113** 均为 **2,568 回合**（2,400 随机 + 168 定向），恢复 N=1；每个种子的 I1a–d、I2–I7 均各执行 2,568 次且实际作用次数均非零。函数名仍保留 `_2448_rounds` 以便追溯，实际回合由日志及断言给出。

| 每个种子的新增必需覆盖 | 次数 |
|---|---:|
| runtime_config_changed / runtime_config_new_open | 各 1 |
| mapping_opened / mapping_buy / mapping_sell | 2 / 1 / 1 |
| skip_volume_too_low / skip_size_too_small | 各 1 |

每个种子还实际覆盖部分成交、零成交、未知超时、查询/账户/实仓/数据库故障、重启、跨日、冷却、权益熔断和恢复；不是只检查空跑。专项测试使用实际 Engine/Executor 配合假原生交易所验证映射两方向、部分成交、补平、重启与关闭；没有真实下单。

日志均在 `%TEMP%`，完整 SHA-256：

| 日志文件 | SHA-256 |
|---|---|
| crossex-r8-final-crossex_exec-py312-20261112.log | `150607bc9948d1c8e5f33215d12daf36950fe40eb60d335d4cae4eef884e4fb2` |
| crossex-r8-final-crossex_exec-py310-20261112.log | `974298b25485da6bfd11b4f207638193be757b1295438c4545dec070a2e09d3c` |
| crossex-r8-acceptance-fill_sim-py312-20261112.log | `b0b4230de02e3a7548804c71518393c3275747faec9d4b8fbbe37678c6464429` |
| crossex-r8-acceptance-fill_sim-py310-20261112.log | `50c824e682b920fbbf917178eb16cccaeabef43897fc0cfe9a2222a927eac3af` |

## CPU 前后对比与约 500 合约内存实测

原始结果及热点调用数已汇总入仓库 [crossex-exec-r8-sampling-2026-11-12.json](crossex-exec-r8-sampling-2026-11-12.json)。测量脚本 `tools/crossex_exec/benchmark_sampling.py`；Windows 11 10.0.26200，14 逻辑 CPU，Python 3.12.10。独立进程、相同合成中间价/历史、真实 SQLite、逐对流式插入历史、不联网/不读取凭据；3 轮无 profiler 的 CPU/wall 测量，另取第 4 轮 cProfile。旧版从起始 WIP 备份的 performance-before 模块加载。

| 场景 | 历史与样本行 | CPU 秒/轮 | 峰值进程工作集 | SQLite + WAL |
|---|---|---:|---:|---:|
| 修改前：84 币种×6 所=504 合约、1,260 对 | 24h，3,630,060 行 | 11.25 / 18.4375 / 18.4844 | 30.10 MiB | 412.52 MiB |
| 修改后：同上 | 同上 | 2.0625 / 1.9844 / 2.0156 | 29.11 MiB | 412.40 MiB |
| 放大：500 币种×6 所=3,000 合约、7,500 对 | 6h，5,437,500 行 | 6.6875 / 6.7031 / 6.7031 | 32.90 MiB | 595.54 MiB |

- 稳态采用第 2、3 轮均值，排除首次初始化状态：**18.4609 → 2.0000 CPU 秒/轮，减少 89.17%**。按 30 秒周期折算该采样部分约 **61.54% → 6.67% 单核**；它是由进程 CPU 秒计算的占用，不是生产监控采样。全部三轮均值另为 16.0573 → 2.0208 秒，原始数据完整保留。
- 单轮 profile 中 `security.clean` 原为 17,479,234 次、新路径 0 次；原 storage encode/set_meta 各 1,262 次，新路径 encode 为 0，set_meta 只有 1 次且分派到类型化报价行。性能收益来自消除逐对全表状态 JSON/递归脱敏，不跳过基准计算或放宽风控。profile 的累计秒数含插桩开销，不与上表无插桩时间混用。
- 用户提供的影子 CPU “约 85% 单核”没有本次线上复测，不能宣称线上已降至 6.67%。测量包含采样组件，不包括完整服务的目录响应、真实适配器、账户、并发 API 或长期运行成本。Windows 工作集也不等于 Linux cgroup 的 page cache 计费。
- 在宿主可用约 500MB 的前提下，建议后续影子部署先以 **MemoryMax=256M** 作为待验候选，为其余进程留余量；这不是已验证的生产限制。现有模板仍为 80M，**本次未改或安装**。上线任务须实测 Linux RSS、`memory.current/peak/events`、WAL/磁盘与持续影子 CPU 后再确定限额；不可从本机 29–33 MiB 直接保证 80M 足够。

复现（路径替换为本机保留位置，命令不访问网络）：

```powershell
python -m tools.crossex_exec.benchmark_sampling --before-zip "$env:TEMP/crossex-r8-resume-20261112T021055Z.zip" --output "$env:TEMP/crossex-r8-sampling-before-repeat.json"
python -m tools.crossex_exec.benchmark_sampling --output "$env:TEMP/crossex-r8-sampling-after-repeat.json"
python -m tools.crossex_exec.benchmark_sampling --bases 500 --history-hours 6 --output "$env:TEMP/crossex-r8-sampling-500-bases-repeat.json"
```

原始 `%TEMP%` 文件为 `crossex-r8-sampling-before-20261112.json`、`crossex-r8-sampling-after-20261112.json`、`crossex-r8-sampling-500-bases-20261112.json`，各自 SHA-256 已写入汇总 JSON 的 `source_sha256`。基准临时 DB 由测量工具关闭后清理；日志、源码备份和结果文件保留。

## 线上状态、边界与下一任务

- 推送：未执行；线上备份/校验：未执行；部署版本/容器/API 线上验收：未读取、未修改，不能据本地分支推断线上状态。本轮没有 SSH、WSL、真实鉴权、实盘开关或订单。
- 工作区原 WIP 已纳入本轮实现；本模块新增文件均显式进入 test/feat/docs 提交，提交后预期无未跟踪文件。Temp 下的日志、源码备份及测量 JSON 属于保留验收产物，不加入 Git。没有清理/覆盖其他任务文件。
- [接口契约](live-exec-contract-2026-11-11.md) 和 [执行器说明](../tools/crossex_exec/README.md) 已同步。taoli1 页面/转发/CSRF 属于独立 `live-exec-page` 任务，本轮不改。旧 fill-sim 的 100U measured 结果仍仅是同机会历史对照，不是第八轮动态定量策略的同规格回测。
- 下一轮请新建任务并先读本节。第九轮可用本次 profile/CPU 结果作为起点，在另行授权的部署/影子任务验证 Linux 整个进程的 CPU/内存；勿把本轮已解决的公共 meta 热点再当作未实现，也勿因此变更风控。
- 生产 Python 3.10.12、Linux cgroup、真实公开目录长期变化、24h 影子运行、实际账户及首次真实交易都未在本轮验证。公开 OI 的单位/时间证据及已知保守口径保留在下方，不将本机测试冒充生产或交易验收。

<details>
<summary>历史停点和前次 PM / 公开行情证据（已被以上交付状态取代）</summary>

## 历史：第八轮 PM 裁决续跑停点（2026-11-12，当时未完成）

本轮按用户“通用授权仅限新增键；风控语义冲突停下”要求，停在新发现的成交额旧断言冲突处。**第八轮未验收通过，不能部署或开启实盘。** 已完成并提交的独立修复、已提交的测试和未提交实现 WIP 分别列在下面。继续时必须保留当前工作区；仅 checkout 当前 HEAD 无法恢复 WIP 的六个新增模块。

## 目标、范围与基线

- 模块：crossex-exec 第八轮；任务书 `ab33e0536d5df64bca62872acd128578148516e4` 的 `docs/crossex-exec-round8-2026-11-12.md`，加本次用户四项 PM 裁决和 Python 3.10 空查询串修复。
- worktree：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\crossex-exec-r8`；分支 `codex/crossex-exec-r8`；起始 HEAD `5bb0e6591749704e0d63c6717753a6cba6244f9e`；起始工作区干净。
- 先读了旧交接最后的“第八轮交付（2026-11-12 续跑，定量行为冲突待 PM，未完成）”、AGENTS、任务书、现有实现与测试。未改 taoli1 前端、fill-sim 源码、部署配置、真实 env 或 `execution.py`。
- 本轮明确禁止 push、SSH、WSL、amend、真实鉴权接口。实际网络只读官方文档及公开行情 GET；测试使用内存假交易所或受 socket/DNS 守护的 loopback 假服务。

## PM 已裁决项及两项断言替换

1. 向下取整后低于任一腿最小数量或最终限价下最小名义额，拒绝为 `size_too_small`；不再加一个公共步长。
2. 某腿没有可核实单位、有效且不超过五分钟的公开 OI 时，只使用另一腿有效 OI；两腿都没有时才 `oi_missing`。深度、单笔、总额/余额及成交额条件继续独立检查。
3. Binance 用历史 OI 两字段比值对照标记价实测；Bitget 只用 V2 公开 OI 的基础币 `size` 并精确匹配 symbol；OKX 成交额继续用基础币量乘 `low24h` 的保守下界。
4. 未知分类始终待人工判断，人工确认前不开。

`test_round3.py` 两项获准替换已提交在 `eb9573f`，实现 `c1ccc0f`，原函数名保留以便追踪：

| 测试 | 替换前 | 替换后 | 保留的原安全断言 |
|---|---|---|---|
| `test_r6_final_sell_limit_minimum_bumps_one_common_step` | 最小名义额 99.94，0.980 × 101.96 不足，成功补到 0.981，并断言满足 99.94 | 同样输入必须抛 `ValueError('size_too_small')` | 随后的 `max_qty=.980` 拒绝断言未改 |
| `test_r6_min_quantity_bumps_once_and_still_invalid_minimum_rejects` | `min_qty=.981` 时成功补到 .981 | 同样输入必须抛 `ValueError('size_too_small')` | `min_qty=.982` 的拒绝断言未改 |

另新增独立最大量测试：最小要求已满足、候选量 .980、交易所最大量 .979 时仍必须 `final_size_above_max`，避免原最大量场景只因最小额不足而通过。

## 新阻塞：默认成交额门槛与旧行为断言

原文件 `tools/crossex_exec/tests/test_engine.py::EngineTests.test_actual_fees_missing_or_low_volume_never_sends`：

```python
with patch.object(self.accounts,'check_instruments',side_effect=ValueError('missing_fees')):
    self.engine.ingest(trigger())
with patch.object(self.market,'turnovers',return_value=['4999999','9000000']):
    self.engine.ingest(trigger('low-volume'))
self.assertFalse(self.gate.calls+self.bg.calls)
```

原行为按固定 5,000,000 门槛拒绝。第八轮要求默认 1,000,000，两腿上述成交额已满足默认条件。在其余准入条件合格的原夹具中，真实 Engine 会发出两笔假交易所 IOC，每腿数量 .490，因此旧零发单断言失败。这不是新增键导致的字典差异。

**待 PM 批准的具体方案**：只在该旧测试中显式设置 `min_quote_volume_usd=5000000`，保留原成交额输入、费率缺失检查和最后零发单断言；新增测试保持默认 1,000,000，并验证 4,999,999 / 9,000,000 在其他条件合格时可开。后一个新增测试已经加入并通过；旧测试尚未添加设置、未改断言，也没有隐藏或跳过失败。

已通过交互问题提交上述方案，截至本次保存交接仍未收到答复。等待时间不是批准。若继续发现阈值、方向、上限或恢复语义冲突，仍需逐项交给 PM，不能把此方案当作通用改测试授权。

## 已提交的独立修复与测试

| 提交 | 类型 | 内容 |
|---|---|---|
| `eb9573f` | test | 两项获准最小量断言替换；空查询串红测 |
| `c1ccc0f` | feat | 去掉向上补步长；空 query 直接得到 `{}` |
| `fd851a3` | test | 每个参数边界、严格类型、原子性、持久化及审计 |
| `ed5d588` | test | 四类定量约束、单腿 OI、过期/缺失/单位/identity |
| `f1233ed` | test | 分类、股票市场选择、映射确认/拒绝、KO/KAT 与倍率边界 |
| `89b2aff` | test | 动态采样、成交额缓存、运行中配置与发送版本；夹具新增 category/OI |
| `579c6c8` | test | 独立管理令牌、请求体与 JSON 校验；模拟器补 OI 输入；summary 新增三类成交额/分类键 |
| `326172c` | test | 保留成交额冲突、最大量独立拒绝、旧夹具补 identity/采样方法；拒绝请求体清理红测 |

本交接及旧交接/接口文档/README 更新为单独 docs 提交，不硬编码自身 hash；用 `git log -1 --format=%H -- docs/task-handoff-2026-11-12-crossex-exec-r8.md` 获取。没有 amend。普通暂存因父仓库索引写权限失败一次，随后按本地提交授权经自动审批执行；没有自动审批拒绝。提交禁用 hooks/GPG，逐项显式暂存。

原测试适配除两项 PM 特批替换外只增加输入/键：`test_exec.instrument` 新增 `category='crypto'`；`FakeMarket` 新增公开 OI 和采样方法；模拟器假市场新增 OI；第六轮纯发送定价夹具补 `instruments`；第七轮空采样异常夹具补 `sample_quotes`；第七轮完整计数字典追加 `volume_too_low/volume_unknown/category_unconfirmed` 为 0，旧六项值和事件数 6 保留。未降低盘口重拉次数/时间边界、裸露/穿零/恢复断言。

## Python 3.10 实测

用户给定解释器存在，实际是 **Windows Python 3.10.20 / SQLite 3.50.4**，不是服务器的 3.10.12。默认解释器为 Python 3.12.10。

直接执行 `parse_qs('', keep_blank_values=True, strict_parsing=True, max_num_fields=4)` 在该 3.10.20 抛出 `ValueError: bad query field: ''`。`c1ccc0f` 改为 query 为空时直接 `{}`，非空保持严格解析。新测试覆盖 summary/positions/trades/stats，带与不带尾部 `?`；非空错误字段、重复参数、空值仍 400。两个版本专项都通过。

3.10 的首次完整回归还暴露新管理 API 的一次 `WinError 10053`：未读完小请求体就返回 403 并关闭连接。新增确定性测试先观察 `rfile.tell()==0`，WIP 修复对有明确 Content-Length 且不超过 8193 字节的未消费请求体做有界丢弃，socket 仍有原两秒 timeout；已消费 JSON 不重复读取。修后 3.10/3.12 管理 API 各 5 项、3.10 空查询串 2 项通过。该管理 API 修复仍属于下面的未提交 WIP，不属于 `c1ccc0f`。

## Binance OI 公开 GET 证据

采集时间：2026-11-12 01:29:04–01:29:06 UTC。每个 symbol 只做两个公开 GET：`/futures/data/openInterestHist?symbol=...&period=5m&limit=1` 和 `/fapi/v1/premiumIndex?symbol=...`，六个请求均 HTTP 200。没有签名、API Key 或账户请求。[Binance 官方字段文档](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#open-interest-statistics)

| 原生 symbol | sumOpenInterest | sumOpenInterestValue | value / quantity | 同轮 markPrice | 绝对偏差 |
|---|---:|---:|---:|---:|---:|
| BTCUSDT | 97672.34200000 | 7193447044.46082000 | 73648.76173913 | 73601.40000000 | 0.064348965% |
| ETHUSDT | 2298346.92600000 | 4711243462.79184000 | 2049.84 | 2049.82882946 | 0.000544950% |
| 1000SHIBUSDT | 4352230866.00000000 | 18886157.66453772 | 0.00433942 | 0.00433974 | 0.007373714% |

OI 时间戳均为 `1794446700000`，标记价分别为 `1794446945000/1794446946000/1794446946000`，相差 245–246 秒。数据支持这三个响应中的数量为**原生合约基础单位**、value 为 USDT 名义额；1000SHIB 的比值是原生 1000SHIB 报价，不再乘 1000。这是实测支持，不是官方字段页明确写出了单位。WIP 每次仍核对返回 symbol、时效和 value/quantity 与 mark 的偏差 ≤1%；无法核实视为该腿缺失，不默认无限 OI。

原始响应 `%TEMP%/crossex-r8-binance-oi-public-20261112.json`，SHA-256 `fd07058554a2c19f7ee3358f984c8a759d4e07e58c3fb825c23050b6796d9fba`。

Bitget WIP 只解析 [V2 Open Interest](https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market#get-open-interest) 的 `data.openInterestList[].size`（基础币）及 `data.ts`，精确校验 symbol，拒绝 V3 外形。Bybit 保留 [官方文档](https://bybit-exchange.github.io/docs/v5/market/open-interest) 的 `openInterest` 双边基础币口径，不换成 `singleOpenInterest`。OKX `oiUsd` 按 USD、Gate `open_interest_usd` 按 USDT 报价金额保留单位；Aster 没有已核实公开 OI 路由，返回缺失，依 PM 规则使用另一腿。

另公开 GET 实测 Binance/Aster `/ticker/24hr?symbol=BTCUSDT` 均无 bid/ask 字段，只有成交额、成交价等；WIP 因此每轮额外用各所 bookTicker 取得中间价，共最多 8 个公开批量请求，仍走既有 RequestQueue/Limiter。不能用 lastPrice 冒充 BBO 中间价。Bitget 批量成交额明确选 V2 `usdtVolume/bidPr/askPr`；OI/成交额不混用 V3 字段。OKX 成交额仍是 `volCcy24h × low24h` **保守下界**，可能把实际成交额足够的合约挡在门外，不能标成精确 24h 报价成交额。

## 未提交 WIP、备份与继续入口

已修改但未提交：`tools/crossex_exec/accounts.py`、`admission.py`、`api.py`、`engine.py`、`limits.py`、`market.py`、`policy.py`。

新增未跟踪模块，必须保留：`liquidity.py`、`management.py`、`open_interest.py`、`settings.py`、`sizing.py`、`universe.py`，均在 `tools/crossex_exec/`。不是可以清理的临时产物。

WIP 备份（上述 13 个实现文件、tracked.diff、基线 HEAD）：`%TEMP%/crossex-r8-wip-20261112T015948Z.zip`，SHA-256 `72014ac762335f58a4e2f4bd531a2486e84ead2eb08bb0934ad4975ffe7253b0`。代码仍在工作区，未 stash、删除或覆盖。Python 缓存和测试日志仍保留。

目前 WIP 已有可运行的参数/审计、管理 API、四项定量、公开 OI 解析、动态成交额/采样、分类和映射纯逻辑，以及新仓配置冻结/共享关口版本复核。专项测试通过**不代表整轮可交付**。后续至少还需：

1. 获得上述旧成交额测试裁决，再按明确范围处理；当前默认参数实现仍按任务书 100 万，没有把代码默认改回 500 万来藏失败。
2. 补齐参数的全部运行语义：`max_equity_drop_usd/daily_loss_stop_usd/total_loss_stop_usd` 目前只具备参数校验/存储，尚未完整接入账户风险判断；已有仓位必须沿用开仓时版本，不因之后参数修改而改变风控。
3. 完整复核映射全链路。已开始在开仓、原生下单、流水/实仓、减仓间换算；尚缺两方向实盘假交易所、重启、部分成交、补平/平仓的独立验收。`save_tracking_upnl` 与 API 投影仍需核对 common/native 价格和数量；不能拿普通同代码模拟器代替这项。
4. `min_venues_with_volume>2` 对 confirmed 映射/股票所集合的资格还需完善；分类 metadata 枚举和预上市硬排除还需完整审核。未知不得默认 crypto。
5. 发送前 OI/映射/成交额失败计数与时效、管理操作和发送关口并发、拒绝请求体清理等需继续专项核查；当前新报错并非都已经投影成 summary skip。
6. `api_data.py` 还未新增 trades/positions 的 sizing、参数版本、成交额投影，映射原生与公共数量的展示也未完成；接口文档新增 schema 仍是 WIP 草案。
7. 基准仍逐对更新既有 `baseline_status`；约 500 合约采样规模的 CPU/内存实测未做，不能给 MemoryMax 建议。先优化 SQLite 采样/状态更新，再实测，不能把 fill-sim 的 RSS 测试当成本模块采样结果。
8. 三种子模拟器仍只有原 2448 回合及新增 OI 输入，**未加入运行中改参数、映射开仓、volume_too_low、size_too_small 的非空覆盖**。需要补齐这些实际执行覆盖，保留 1a–1d、2–7 全部断言，再跑两条 unittest 和可用 Python 3.10。
9. 完成代码自审、合适检查及独立 feat/docs 本地提交；不 push、不 SSH、不 WSL、不鉴权，不扩大到前端模块。

## 本轮验证与线上状态

完整输出与三种子计数在下述日志。首次接入回归为 299 项，9 failures/2 errors；盘口多取一轮和两个新增接口夹具缺失已在保持原断言下修复，第七轮 41 项恢复全过。后续完整 3.12 回归 305 项只有上述成交额旧断言失败；3.10 同轮有相同失败和一次管理 API 连接中断，已通过红绿测试修复并重跑最终完整套件。

fill-sim 全套未改，Python 3.12：158 项 / 29.390s / OK；Python 3.10：158 项 / 31.118s / OK。47 个执行器 Python 文件通过 `ast.parse(feature_version=(3,10))`。真实 Python 3.10 的验证范围限于上述 Windows 解释器，不等于 Ubuntu 3.10.12、systemd 或内存上限验收。

未 push、未生产备份、未部署、未 SSH/WSL/amend、未读真实凭据或调用真实鉴权接口、未启动生产服务。线上版本和配置未触碰。当前不要将第八轮标为完成；补齐后再为下一个模块创建新任务。

### 最后一次实现改动后的完整结果

执行以下两条 unittest，另用用户提供的 Python 3.10 解释器分别再运行相同参数：

```text
python -m unittest discover -s tools/crossex_exec/tests -v
python -m unittest discover -s tools/fill_sim/tests -v

Python 3.12.10: crossex-exec Ran 306 tests in 93.845s / FAILED (failures=1)
Python 3.10.20: crossex-exec Ran 306 tests in 99.990s / FAILED (failures=1)
Python 3.12.10: fill-sim Ran 158 tests in 29.390s / OK
Python 3.10.20: fill-sim Ran 158 tests in 31.118s / OK
```

两版执行器均 **305 项通过，唯一失败** 为前述 `test_actual_fees_missing_or_low_volume_never_sends`，完整执行器命令均以 code 1 结束。没有 skip/xfail 或忽略失败。fill-sim 在最后仅修改管理 API 请求体清理后未重复运行，因本轮没有修改其代码。完整运行的 3.10 路径：`C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/125141d8-f3d5-4518-98a0-6a4761963215/scratchpad/uvpy/cpython-3.10-windows-x86_64-none/python.exe`。

原模拟器在两个 Python 版本均三个种子各 2448 回合通过，`random_rounds=2400`、`N=1`，1a–1d 与 2–7 每项 checks 都等于 2448。实际开仓对数分别 37/34/47。非空 exercised 在两版一致：

| seed | 1a | 1b | 1c | 1d | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 20261111 | 35 | 60 | 25 | 84 | 74 | 95 | 74 | 600 | 23 | 330 |
| 20261112 | 27 | 56 | 13 | 77 | 68 | 83 | 68 | 600 | 30 | 355 |
| 20261113 | 37 | 78 | 27 | 106 | 94 | 115 | 94 | 600 | 31 | 372 |

这些仍是原场景覆盖；新增运行时参数/映射/成交额不足/size_too_small **只在定向测试中有部分覆盖，没有加入三个种子的非空计数**，不能当作第八轮模拟器验收。约 500 合约采样内存测量尚未开始。

日志均位于本机 `%TEMP%`，不入库、不覆盖历史日志：

| 日志 | SHA-256 |
|---|---|
| `crossex-r8-pm-final-py312-20261112.log` | `93483692a5ccb399276bf4ac02ffe3d3ea3b670a7752cd8e37ca25ca2b47c538` |
| `crossex-r8-pm-final-py310-20261112.log` | `a465f618840118a403a47aeb64547427e980b52fa2955209f30fe930169dea1e` |
| `crossex-r8-fill-py312-20261112.log` | `bec29c0c66f9c277fe01db0b1bea79dd6f83021e46b60412fb8eec75fec52a31` |
| `crossex-r8-fill-py310-20261112.log` | `40fe0eda2bb42924edaa486b32eda14771317b2c4bb15f5b4bb2f2a7760bbf09` |

先红日志另有 `crossex-r8-pm-api-red-20261112.log`、`crossex-r8-pm-floor-red-20261112.log`、`crossex-r8-settings-red-20261112.log`、`crossex-r8-liquidity-red-20261112.log`、`crossex-r8-universe-red-20261112.log`、`crossex-r8-engine-red-20261112.log`、`crossex-r8-market-red-20261112.log`、`crossex-r8-admin-red-20261112.log`。3.10 首次连接中断证据在 `crossex-r8-pm-boundary-py310-20261112.log`，修后两版完整日志不再有该错误。

</details>

## 合并终审修复

2026-11-12，仅处理 crossex-exec 合并与本地验收。起始工作区干净；工作树仍为 `C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\crossex-exec-r8`，当前分支 `codex/crossex-exec-r8`。已先读来源分支旧交接“终审修复”、原始终审报告和本交接。没有 push、SSH、WSL、amend、真实鉴权、服务启动或订单；所有交易行为均为测试替身。

### 基线和提交

| 类型 | 提交 | 内容 |
|---|---|---|
| 合并第一父 | `b24f5aff42e9aac5e68eeae1345b3f3deb811ddc` | 第八轮完整交付 |
| 合并第二父 | `abf8dbad96183a77b35fce6a52e4e7ef0dc4ef18` | 终审修复 `18e475c/f580331/dab6d98/68c69a1` 及 Python 3.10 空查询修复 |
| merge | `3b1d056aea67b2251ca61b9e3ad432adbae5cf43` | 实际执行 `git merge --no-ff --no-commit codex/crossex-exec`，解决冲突后生成双亲合并提交 |
| test | `eb297ef` | 新增 11 项合并交界回归；其中锁顺序测试先失败 |
| feat | `8e95ea73635d3718739df68d9450b06352e90cef` | 共享关口统一先持有 Executor 锁、再持有 Store 锁，消除与 save/flush/recover 的锁反转 |
| docs | 本节所在独立提交 | 原始结果、接口适配、README 和 next-task；用 `git log -1 --format=%H -- docs/task-handoff-2026-11-12-crossex-exec-r8.md` 查询 |

测试和实现均已在文档提交前独立提交；没有 amend。Git 写入父仓库 worktree 索引所需的自动审批已通过，没有审批拒绝。提交关闭 hooks/GPG，不进行远端操作。

### 冲突处理和保留的规则

| 文件 | 合并处理 |
|---|---|
| `docs/task-handoff-2026-11-11-crossex-exec.md` | 保留第八轮历史与来源分支完整“终审修复”节，最新状态指向本节 |
| `tools/crossex_exec/README.md` | 保留第八轮运行说明、独立归档/service 布局、SQL 分页及内存说明，区分各分支历史测试数量 |
| `api.py` | 两边等价的空 query 防护合为一处；保留独立 ADMIN 令牌、写接口、有界 JSON/请求体清理和只读连接约束 |
| `storage.py` | 同时保留 `Store(PublicStore)`、路径级共享锁、类型化采样/迁移，以及原子 `save_orders`、SQL 过滤、64/128 行 rowid 分页与索引 |
| `limits.py` | `exposure(snapshot, intent=None)` 支持第八轮无单笔参数调用，同时使用 SQL 过滤迭代器；冻结风险配置按 `get_pair` 查询，不重新全量加载历史 |
| `engine.py` | 流式统计与冻结风险阈值共存；新增配置/映射/定量复核接入提交后的共享 `check_release`；单仓查询使用 `get_pair` |
| `api_data.py`（自动合并后的语义修正） | summary 已改为流式活动/关闭仓位投影，冻结风险从活动投影的 `config_at_open` 取值；不引用已经移除的全量 `pairs` 变量，不恢复全量物化 |
| `execution.py`（自动合并后的衔接） | 同一共享锁区覆盖准入、SENT 原子提交与最终复查；管理写入不能使一对订单跨配置版本。后续独立 feat 修正该锁区的锁顺序 |

成功开仓发送路径为：两腿 INTENT 与事件同事务准备 → 两个 transport 都完成限频/建连 → barrier action 持有 Executor/Store 锁 → 整对准入（保留原重拉上限）→ 两条 SENT 与事件原子 COMMIT → 两腿硬上限/权益/账户缓存复查 → `Engine.check_release` 复查参数版本、目录/映射确认、映射价格比、当前数量相对于深度/OI/剩余总名义额预算 → `can_open(ignore_cids=本批次)` → 冻结参数限制下的盘口年龄/时间差最终检查 → 同时放行。

最终检查后成功路径只有内存操作；不写库、不重拉、不在各腿单独否决。`ignore_cids` 仅排除本批次造成的非终态阻挡和拒绝率样本，硬敞口仍包含两条 SENT。INTENT 原子失败回滚、owned 孤立清理、SENT/UNKNOWN 原编号保守恢复全部保留。

第八轮的版本化参数、定量、实时成交额、类别/原始市场身份/映射、ADMIN 写接口、冻结持仓配置、SQLite 行采样、按行状态更新及公共字段脱敏优化全部保留。`tools/fill_sim` 源码和测试相对两边未改；没有改动前端或扩大到第九轮。

service 与来源 `abf8dba` 一致：`WorkingDirectory=/home/ubuntu/wubin/crossex-exec`，模块/环境/var 布局保留，**`MemoryMax=192M`**。本交接此前“模板仍为 80M”和 256M 候选是合并前记录；当前以合入的 192M 模板为准。192M 仍须 Linux/cgroup/实际采样与并发 API 影子验收。本次没有重新测量采样 CPU/内存，不能把前面的历史测量称为合并后或生产复测。

### 测试保留和必要接口适配逐项记录

本次没有修改任何原测试断言、随机种子、故障序列、N=1 恢复界限、跳过设置或验收阈值。相对第一父 `b24f5af`，既有测试仅增加下面接口适配，直接继承来源分支已有改动：

1. `test_engine.py::EngineTests.setUp`：测试执行器增加绑定 `check_release=self.engine.check_release`，使替换执行器也调用最终共享关口。
2. `test_invariants.py::FakeDatabase.iter_orders`：补 pair/state/excluded state/sent_since/accounting/reduce_only 筛选接口，与真实 SQLite 查询语义一致。
3. `test_invariants.py::FakeDatabase.save_orders`：批量准备/发送模型增加快照回滚，第二条失败时撤销本批次所有订单和事件。
4. `test_invariants.py::FakeDatabase.get_pair`：增加按 ID 查询，支持取消全量仓位读取后的运行路径。
5. `test_invariants.py::FakeDatabase.iter_pairs`：增加活动/排除状态迭代，保留现有内存模型和全部断言。

来源新增的 `test_final_review.py`（19 项）与 `memory_probe.py` 保持原文；新增 `test_merge_final_review.py`（11 项）单独提交。新测试覆盖 SENT 真正提交后版本变化、映射确认/价格比失效、深度收缩、OI 到期、配置年龄上限及等号、管理写入序列化、恢复锁竞争、开仓路径禁全量历史、流式 summary 冻结阈值。

逐函数 AST 审计：相对第一父，两套测试 30 个文件、1,649 个 `assert*/fail/skipTest` 或 `assert` 均保留；相对第二父，两套测试 22 个文件、1,517 个此类节点中，**本次合并新增删改为零**。两边既有差异按第八轮已完成记录继承，以下 6 个函数不是本次修改或新增批准：

| 原有差异位置 | 第八轮已经完成的处理 |
|---|---|
| `test_api.py::DataTests.test_positions_use_fills_and_executable_books_not_intent_or_mark` | 精确键集合增加 config/sizing/turnovers/native 字段，原值断言保留 |
| `test_api.py::DataTests.test_trade_contract_actual_vwap_fees_gaps_and_sim_identity` | 同上，增加第八轮字段 |
| `test_invariants.py::InvariantSimulationTests.assert_reduction` | I3 原生步长换算为公共数量步长，原倍率 1 场景等价 |
| `test_round3.py::Round3PolicyTests.test_r6_final_sell_limit_minimum_bumps_one_common_step` | 既有 PM 裁决：不足最小名义额拒绝，不补一步；继承 `b24f5af` 原文 |
| `test_round3.py::Round3PolicyTests.test_r6_min_quantity_bumps_once_and_still_invalid_minimum_rejects` | 既有 PM 裁决：不足最小数量拒绝；继承 `b24f5af` 原文 |
| `test_round7.py::ReferenceAndAPITests.test_all_six_counters_are_zero_then_count_once_per_trigger_across_restart` | 完整计数字典新增第八轮原因，原六类计数与事件数断言保留 |

审计还确认原终审报告及 service 与来源分支一致、两模块共 71 个 Python 文件通过 3.10 AST 语法检查。`git diff --check b24f5af HEAD` 排除未修改的原终审报告后通过；该报告保留 32 处原 Markdown 双空格换行，未为消除 whitespace 提示而改写原始证据。

### 完整验收和原始汇总

最后一次实现修改为 `8e95ea7`。默认解释器 Windows **Python 3.12.10 / SQLite 3.49.1**；用户给定路径实际为 **Python 3.10.20 / SQLite 3.50.4**，不等同于 Ubuntu 生产环境。各用以下命令完整运行两套测试，未按用例筛选；三种子属于执行器完整套件，全部真实执行。

```powershell
python -m unittest discover -s tools/crossex_exec/tests -v
python -m unittest discover -s tools/fill_sim/tests -v
$py310 = 'C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/125141d8-f3d5-4518-98a0-6a4761963215/scratchpad/uvpy/cpython-3.10-windows-x86_64-none/python.exe'
& $py310 -m unittest discover -s tools/crossex_exec/tests -v
& $py310 -m unittest discover -s tools/fill_sim/tests -v
```

```text
Python 3.12.10 / SQLite 3.49.1
$ python -m unittest discover -s tools/crossex_exec/tests -v
Ran 353 tests in 115.537s

OK
exit_code=0

$ python -m unittest discover -s tools/fill_sim/tests -v
Ran 158 tests in 31.218s

OK
exit_code=0

Python 3.10.20 / SQLite 3.50.4
$ python -m unittest discover -s tools/crossex_exec/tests -v
Ran 353 tests in 123.745s

OK
exit_code=0

$ python -m unittest discover -s tools/fill_sim/tests -v
Ran 158 tests in 32.240s

OK
exit_code=0
```

每个版本三个种子 `20261111/20261112/20261113` 各 **2,568 回合 = 2,400 随机 + 168 定向**，I1a–d、I2–I7 每项 checks 均为 2,568 且 exercised 非零；运行中参数变更、映射两方向开仓、成交额不足和定量不足均实际覆盖。不同 Python 的查询轮询次数受 wall-clock 确认循环影响，下面保留各版本独立原文，不把计数改成相同。

Python 3.12.10 / SQLite 3.49.1

```text
INVARIANTS seed=20261111 rounds=2568 random_rounds=2400 N=1 checks={'1a': 2568, '1b': 2568, '1c': 2568, '1d': 2568, '2': 2568, '3': 2568, '4': 2568, '5': 2568, '6': 2568, '7': 2568} exercised={'1a': 35, '1b': 68, '1c': 25, '1d': 92, '2': 82, '3': 103, '4': 82, '5': 600, '6': 23, '7': 345} coverage={'account_failures': 151, 'baseline_admission': 82, 'cooldown_active': 167, 'db_failures': 22, 'full_fill': 138, 'halted': 344, 'halting_retry': 1, 'intent_write_abort': 21, 'mapping_buy': 1, 'mapping_opened': 2, 'mapping_sell': 1, 'open': 82, 'opened_pairs': 41, 'partial_fill': 27, 'position_failures': 137, 'position_read': 3418, 'query': 4332, 'query_failures': 1418, 'recovery_obligation': 23, 'reduce': 103, 'restarts': 142, 'runtime_config_changed': 1, 'runtime_config_new_open': 1, 'scheduled_account': 149, 'scheduled_adverse': 133, 'scheduled_database': 140, 'scheduled_detail': 148, 'scheduled_equity': 142, 'scheduled_midnight': 121, 'scheduled_none': 578, 'scheduled_partial': 164, 'scheduled_positions': 136, 'scheduled_restart': 142, 'scheduled_spread': 129, 'scheduled_stale': 150, 'scheduled_timeout_ack': 146, 'scheduled_timeout_unknown': 146, 'scheduled_zero': 144, 'skip_baseline_not_flat': 75, 'skip_book_skew': 28, 'skip_book_stale': 112, 'skip_funding_diff': 56, 'skip_not_single_venue_outlier': 129, 'skip_size_too_small': 1, 'skip_too_few_venues': 26, 'skip_volume_too_low': 1, 'timeout_ack': 28, 'timeout_unknown': 21, 'zero_fill': 20}
INVARIANTS seed=20261112 rounds=2568 random_rounds=2400 N=1 checks={'1a': 2568, '1b': 2568, '1c': 2568, '1d': 2568, '2': 2568, '3': 2568, '4': 2568, '5': 2568, '6': 2568, '7': 2568} exercised={'1a': 27, '1b': 64, '1c': 13, '1d': 85, '2': 76, '3': 91, '4': 76, '5': 600, '6': 30, '7': 370} coverage={'account_failures': 116, 'baseline_admission': 76, 'cooldown_active': 24, 'db_failures': 16, 'full_fill': 128, 'halted': 369, 'halting_retry': 1, 'intent_write_abort': 15, 'mapping_buy': 1, 'mapping_opened': 2, 'mapping_sell': 1, 'open': 76, 'opened_pairs': 38, 'partial_fill': 24, 'position_failures': 139, 'position_read': 3432, 'query': 5040, 'query_failures': 648, 'recovery_obligation': 30, 'reduce': 91, 'restarts': 142, 'runtime_config_changed': 1, 'runtime_config_new_open': 1, 'scheduled_account': 116, 'scheduled_adverse': 135, 'scheduled_database': 149, 'scheduled_detail': 128, 'scheduled_equity': 138, 'scheduled_midnight': 134, 'scheduled_none': 578, 'scheduled_partial': 159, 'scheduled_positions': 138, 'scheduled_restart': 142, 'scheduled_spread': 184, 'scheduled_stale': 145, 'scheduled_timeout_ack': 139, 'scheduled_timeout_unknown': 149, 'scheduled_zero': 134, 'skip_baseline_not_flat': 65, 'skip_book_skew': 20, 'skip_book_stale': 86, 'skip_funding_diff': 91, 'skip_not_single_venue_outlier': 153, 'skip_size_too_small': 1, 'skip_too_few_venues': 46, 'skip_volume_too_low': 1, 'timeout_ack': 22, 'timeout_unknown': 34, 'zero_fill': 15}
INVARIANTS seed=20261113 rounds=2568 random_rounds=2400 N=1 checks={'1a': 2568, '1b': 2568, '1c': 2568, '1d': 2568, '2': 2568, '3': 2568, '4': 2568, '5': 2568, '6': 2568, '7': 2568} exercised={'1a': 37, '1b': 86, '1c': 27, '1d': 114, '2': 102, '3': 123, '4': 102, '5': 600, '6': 31, '7': 387} coverage={'account_failures': 134, 'baseline_admission': 102, 'cooldown_active': 158, 'db_failures': 20, 'full_fill': 178, 'halted': 386, 'halting_retry': 1, 'intent_write_abort': 20, 'mapping_buy': 1, 'mapping_opened': 2, 'mapping_sell': 1, 'open': 102, 'opened_pairs': 51, 'partial_fill': 27, 'position_failures': 141, 'position_read': 3416, 'query': 5488, 'query_failures': 647, 'recovery_obligation': 31, 'reduce': 123, 'restarts': 114, 'runtime_config_changed': 1, 'runtime_config_new_open': 1, 'scheduled_account': 134, 'scheduled_adverse': 151, 'scheduled_database': 156, 'scheduled_detail': 134, 'scheduled_equity': 144, 'scheduled_midnight': 142, 'scheduled_none': 578, 'scheduled_partial': 152, 'scheduled_positions': 141, 'scheduled_restart': 114, 'scheduled_spread': 153, 'scheduled_stale': 145, 'scheduled_timeout_ack': 157, 'scheduled_timeout_unknown': 134, 'scheduled_zero': 133, 'skip_baseline_not_flat': 43, 'skip_book_skew': 28, 'skip_book_stale': 73, 'skip_funding_diff': 68, 'skip_not_single_venue_outlier': 132, 'skip_size_too_small': 1, 'skip_too_few_venues': 22, 'skip_volume_too_low': 1, 'timeout_ack': 39, 'timeout_unknown': 37, 'zero_fill': 20}
```

Python 3.10.20 / SQLite 3.50.4

```text
INVARIANTS seed=20261111 rounds=2568 random_rounds=2400 N=1 checks={'1a': 2568, '1b': 2568, '1c': 2568, '1d': 2568, '2': 2568, '3': 2568, '4': 2568, '5': 2568, '6': 2568, '7': 2568} exercised={'1a': 35, '1b': 68, '1c': 25, '1d': 92, '2': 82, '3': 103, '4': 82, '5': 600, '6': 23, '7': 345} coverage={'account_failures': 151, 'baseline_admission': 82, 'cooldown_active': 167, 'db_failures': 22, 'full_fill': 138, 'halted': 344, 'halting_retry': 1, 'intent_write_abort': 21, 'mapping_buy': 1, 'mapping_opened': 2, 'mapping_sell': 1, 'open': 82, 'opened_pairs': 41, 'partial_fill': 27, 'position_failures': 137, 'position_read': 3418, 'query': 398, 'query_failures': 76, 'recovery_obligation': 23, 'reduce': 103, 'restarts': 142, 'runtime_config_changed': 1, 'runtime_config_new_open': 1, 'scheduled_account': 149, 'scheduled_adverse': 133, 'scheduled_database': 140, 'scheduled_detail': 148, 'scheduled_equity': 142, 'scheduled_midnight': 121, 'scheduled_none': 578, 'scheduled_partial': 164, 'scheduled_positions': 136, 'scheduled_restart': 142, 'scheduled_spread': 129, 'scheduled_stale': 150, 'scheduled_timeout_ack': 146, 'scheduled_timeout_unknown': 146, 'scheduled_zero': 144, 'skip_baseline_not_flat': 75, 'skip_book_skew': 28, 'skip_book_stale': 112, 'skip_funding_diff': 56, 'skip_not_single_venue_outlier': 129, 'skip_size_too_small': 1, 'skip_too_few_venues': 26, 'skip_volume_too_low': 1, 'timeout_ack': 28, 'timeout_unknown': 21, 'zero_fill': 20}
INVARIANTS seed=20261112 rounds=2568 random_rounds=2400 N=1 checks={'1a': 2568, '1b': 2568, '1c': 2568, '1d': 2568, '2': 2568, '3': 2568, '4': 2568, '5': 2568, '6': 2568, '7': 2568} exercised={'1a': 27, '1b': 64, '1c': 13, '1d': 85, '2': 76, '3': 91, '4': 76, '5': 600, '6': 30, '7': 370} coverage={'account_failures': 116, 'baseline_admission': 76, 'cooldown_active': 24, 'db_failures': 16, 'full_fill': 128, 'halted': 369, 'halting_retry': 1, 'intent_write_abort': 15, 'mapping_buy': 1, 'mapping_opened': 2, 'mapping_sell': 1, 'open': 76, 'opened_pairs': 38, 'partial_fill': 24, 'position_failures': 139, 'position_read': 3432, 'query': 425, 'query_failures': 31, 'recovery_obligation': 30, 'reduce': 91, 'restarts': 142, 'runtime_config_changed': 1, 'runtime_config_new_open': 1, 'scheduled_account': 116, 'scheduled_adverse': 135, 'scheduled_database': 149, 'scheduled_detail': 128, 'scheduled_equity': 138, 'scheduled_midnight': 134, 'scheduled_none': 578, 'scheduled_partial': 159, 'scheduled_positions': 138, 'scheduled_restart': 142, 'scheduled_spread': 184, 'scheduled_stale': 145, 'scheduled_timeout_ack': 139, 'scheduled_timeout_unknown': 149, 'scheduled_zero': 134, 'skip_baseline_not_flat': 65, 'skip_book_skew': 20, 'skip_book_stale': 86, 'skip_funding_diff': 91, 'skip_not_single_venue_outlier': 153, 'skip_size_too_small': 1, 'skip_too_few_venues': 46, 'skip_volume_too_low': 1, 'timeout_ack': 22, 'timeout_unknown': 34, 'zero_fill': 15}
INVARIANTS seed=20261113 rounds=2568 random_rounds=2400 N=1 checks={'1a': 2568, '1b': 2568, '1c': 2568, '1d': 2568, '2': 2568, '3': 2568, '4': 2568, '5': 2568, '6': 2568, '7': 2568} exercised={'1a': 37, '1b': 86, '1c': 27, '1d': 114, '2': 102, '3': 123, '4': 102, '5': 600, '6': 31, '7': 387} coverage={'account_failures': 134, 'baseline_admission': 102, 'cooldown_active': 158, 'db_failures': 20, 'full_fill': 178, 'halted': 386, 'halting_retry': 1, 'intent_write_abort': 20, 'mapping_buy': 1, 'mapping_opened': 2, 'mapping_sell': 1, 'open': 102, 'opened_pairs': 51, 'partial_fill': 27, 'position_failures': 141, 'position_read': 3416, 'query': 493, 'query_failures': 32, 'recovery_obligation': 31, 'reduce': 123, 'restarts': 114, 'runtime_config_changed': 1, 'runtime_config_new_open': 1, 'scheduled_account': 134, 'scheduled_adverse': 151, 'scheduled_database': 156, 'scheduled_detail': 134, 'scheduled_equity': 144, 'scheduled_midnight': 142, 'scheduled_none': 578, 'scheduled_partial': 152, 'scheduled_positions': 141, 'scheduled_restart': 114, 'scheduled_spread': 153, 'scheduled_stale': 145, 'scheduled_timeout_ack': 157, 'scheduled_timeout_unknown': 134, 'scheduled_zero': 133, 'skip_baseline_not_flat': 43, 'skip_book_skew': 28, 'skip_book_stale': 73, 'skip_funding_diff': 68, 'skip_not_single_venue_outlier': 132, 'skip_size_too_small': 1, 'skip_too_few_venues': 22, 'skip_volume_too_low': 1, 'timeout_ack': 39, 'timeout_unknown': 37, 'zero_fill': 20}
```

### 日志、线上状态与下一任务

所有原始 stdout/stderr 日志以二进制捕获到本机 `%TEMP%`，以下为完整 SHA-256；既有验收日志未覆盖。首轮合并基线 342 项通过；新增专项先得到 11 项中 1 个锁反转失败，修后 11 项全过，最终完整套件再验收。

| Log in `%TEMP%` | SHA-256 |
|---|---|
| `crossex-merge-final-crossex_exec-py312-20261112T122034.log` | `ed8a0c9efd1a61a27fbb3cd4f30b3083fd27c8ab40e6570621ddeff3709ac4e4` |
| `crossex-merge-final-fill_sim-py312-20261112T122034.log` | `ffc9f316ca86d37781d50a16c0e64ba0d4b31c032079df0cad2ccc4c5b4c7174` |
| `crossex-merge-final-crossex_exec-py310-20261112T122041.log` | `c11a716a636a63d09851d8d987480815b1f3a07b1482692d5c625f62ec7e017f` |
| `crossex-merge-final-fill_sim-py310-20261112T122041.log` | `85e5967c87022bd8e9221fb7d44c5ae273f19f6f0b97af59caefe22b90919575` |
| `crossex-merge-first-py312-20261112T121309.log` | `621f26eb896f746da707ecff74c7af059007e77f3d6e769241e82f3446d75dea` |
| `crossex-merge-intersection-red-20261112T121905.log` | `4e270a16e0f15b870a2b0769d030dc31705d155bb1b4241d3f50c87a2804b2d3` |
| `crossex-merge-intersection-green-20261112T121956.log` | `1024ed8b1048fa5045c819b37b3630454a19753f3a3e1d140e73e1da2423b805` |
| `crossex-merge-audit-20261112T122201.json` | `862f98befaac0e9821644f087c584353050006da695ac18b8790aac95d68e6d7` |

仅本地交付：推送未执行；线上 DB 备份、校验、部署版本和线上接口检查均未执行，线上状态未读取或修改。没有真实 API 凭据/账户调用，不把本地通过认作真钱交易验收。仓库无新增未跟踪交付文件；Temp 验收日志和忽略的 Python 缓存保留。

后续请新建对应模块任务，从本节继续：另行授权 Linux/systemd/SQLite 3.37、实际目录和账户、完整 cgroup 资源预算及 24h 影子验收；页面联调仍属于 `live-exec-page`。不要继续使用两个合并父分支的旧 HEAD 作为合并完成版本，也不要根据历史的“80M/323 项/289 项”描述覆盖本节。

## 第九轮

### 目标、分支与交付范围

工作目录仍为 `C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\crossex-exec-r8`，分支 `codex/crossex-exec-r8`；起点 `efd296eea4effa2914cddd639f7f6ba7e208d693`，进入任务时工作区干净。先读第八轮只读终审、本轮任务书及本交接，然后完成执行器模块的本地修复。用户明确要求不 push/SSH/WSL/amend/鉴权，因此未采用仓库默认的生产交付步骤。

本轮提交按用途分开：

| 提交 | 内容 |
|---|---|
| `48bc076` | test：第八轮终审、第九轮管理与存储回归，先在旧实现上得到红灯 |
| `18e214c` | test：锁交错、实际写出时间、恢复与基线边界、旧夹具新增接口适配 |
| `e24adc2` | feat：全部本轮功能及离线稳态测量入口 |

文档、契约与验收产物另作 docs 提交；功能固定版本完整 SHA 见 [验收数据](crossex-exec-r9-validation-2026-11-12.json)。本节替代此前“353 项/无解除接口”等与本轮有关的状态描述，历史证据保留原文。

### 修复与关键入口

| 项目 | 实现及验收 |
|---|---|
| P1-1 嵌套锁死锁 | `engine.py` 的 alert/lifecycle、`limits.py` 的 HardLimits、`stops.py` 共用 Store RLock；组合顺序为协调器→Executor→Store。账户线程不获取 Executor；发送线程不获取协调器。补账户先进入告警、发送先持 Store、SENT 提交时账户刷新到达等交错，线程按限时结束且未误开仓。 |
| P1-2 SENT 后成交额复核 | `Engine.check_release` 在持久化 SENT 后通过 `MarketData.cached_turnovers` 只读冻结配置对应的两腿缓存；复查金额、300 秒时效及兼容市场所数。299.75 秒缓存经历 0.5 秒提交后整对拒发；金额/时效等号及第三所过期都有测试，最终关口禁用刷新调用。 |
| P1-3 OKX 盘前标志 | `Instrument.rule_type` 保留原 ruleType；native category 优先盘前。pre_market/rebase_contract 及其他未知非 normal 规则在 Universe 候选、人工分类、映射、采样与 Engine 开仓路径被拒绝；人工 crypto 决策不能覆盖。既有持仓原始市场身份仍可用于减仓。 |
| P2 两腿真实发送时间 | `Executor` 将 sent_persisted_ms 与 sent_ms 分开；`Transport` 在完成签名、请求即将写出前调用纯内存观察。随 ACK/UNKNOWN 等原有保存流程落库，最终统一放行后没有增加同步写库。250ms 腿延迟及传输层 150ms 签名延迟被正确观测，签名失败不伪造发送时刻。 |
| CPU | 继承第八轮表存储/30 秒采样；`public_store.py` 行比较、变化才写、当轮缺失即撤销；`storage.py` 的 6 小时样本查询增加 bucket 主键范围，同时保留 at 精确边界。风控、窗口、预热、保留期均未降低。 |
| release-halt | `__main__.py` 同一 flock，原服务停止后才操作；HALTED 且 confirm 精确匹配。`equity.py` 核对两边空仓/全部未完成订单、本地流水/待记账/在途发送及仓位锁；无负债、正权益且新鲜才重建基线并写 HALT_RELEASED。只清权益停机锁，不启动服务。 |
| rebaseline | 停机 CLI 同上；运行 CLI 交给本机 ADMIN API。`Management` 绑定 `Engine.rebaseline`，协调器阻止新一轮发送；网络读取不持 Store/Executor 锁；提交前重查本地状态、时效及 revision。SQLite 事务同时写全部基线和 EQUITY_REBASELINED，失败回滚；旧在途刷新不可覆盖新 revision。 |

重设基线保留订单、费用及收益历史，以 risk_pnl_baseline 的每日/累计偏移建立新的风险计量起点。已有亏损 200 的空仓停机案例重设后归零风险计量，再新增亏损 100 仍按原累计限额停机。原 STOP 文件、日亏锁、手工锁、仓位类锁均保留原规则。API 返回金额为数字、时间为 UTC ISO8601；内部审计保留精确 Decimal 字符串和 epoch 秒。

分离 sent_ms 后，崩溃/签名失败可能留下没有实际写出观测的 SENT 记录；最近订单/拒单率查询使用 `coalesce(sent_ms,sent_persisted_ms,0)`，新增对应索引，避免遗漏历史风险样本。send_delta_ms 仍只用实际 sent_ms，任一未知就为 null。原有历史共享时刻不追溯伪造。

### 线上 stale 与负债的调查结论

本轮只读本地 Git 中的 `abf8dba` 源码，没有访问运行服务器或真实账户。可确认：旧 `Engine.refresh_accounts` 会先发布缓存并更新 account_equity_current，**不会在该后台路径清除 account_snapshot_unavailable**；但旧 `reconcile` 成功后确实执行 `stops.discard('account_snapshot_unavailable')`，而 `tick` 并不因该锁跳过对账。因此不能将现象定性为“锁一旦置位永不解除”。

读取起点用于 10 秒时效检查，权益写入本身不证明快照新鲜；旧对账的其他异常也统一投影为 account_snapshot_unavailable。持续权益更新与持续 stale 可以同时发生。第八轮前的采样 CPU/锁等待、私有读取耗时、其他对账异常都是仍待线上时间证据区分的原因，不能根据本地代码认定 CPU 饥饿就是唯一根因。任务书中的 80–87% 是用户提供的旧线上观测，本轮没有重新测量服务器。

本轮补齐明确的恢复规则：`ACCOUNT_RECOVERY_SNAPSHOTS=3`，账户刷新或对账观察连续三个**不同读取起点**、年龄 [0,10] 秒且相邻起点间隔不超过 10 秒的完整快照后，只解除自身快照锁，并将 ACCOUNT_SNAPSHOT_RECOVERED 与解锁放在同一事务。失败、陈旧或过长间隔重置计数；同一缓存重复使用不累计；审计写失败继续保留锁。不会解除 HALTED、仓位不匹配、裸露或其他原因的锁。

summary 增加 account_snapshot（captured_at/completed_at/age_seconds/refresh_seconds/fresh）、stop_locks、liabilities、equity_baseline_revision。refresh_seconds 包含该次读取、等锁与完整核验，重复读取缓存不覆盖原刷新耗时；halt_reason 保留真正的停机原因。后续线上核验应同时观察该耗时、当前年龄、ACCOUNT_REFRESH_FAILED/ACCOUNT_SNAPSHOT_RECOVERED、最近 RECONCILE 及 CPU/线程栈，才能区分慢刷新与协调器问题。

旧实现仅记录正 liability，没有用它直接拒绝开仓。本轮按用户指定的 spec 语义修复：`HYPERLIQUID / USDC / 28.5` 合成快照使 HardLimits/Engine 停开并写 ACCOUNT_LIABILITY/ALERT，SENT 提交后新出现负债也整对拒发。负债自身不另创强制平仓策略；已有减仓和原权益/保证金/累计亏损停机规则继续执行。三个不同且新鲜的无负债快照才清自身负债锁，HALTED 保持；rebaseline/release-halt 均拒绝有负债的账户。本轮没有确认线上这笔负债当前是否仍存在，也未执行还款或划转。

### TDD、原断言与全量验证

初始专项在旧实现上先红（`%TEMP%/crossex-r9-red-20261112.log`）；后续针对传输层签名延迟、风险发送窗口、历史核对期间快照过期、HTTP 金额/时间格式及重复缓存刷新观测继续先红后绿。最终新增三个第九轮测试文件合计 **45 项**，执行器总数 **398 = 原 353 + 新 45**。

对起点逐函数 AST 审计了两套测试的 33 个原文件、673 个函数、1,756 个 `assert*/fail/skipTest` 或 `assert`，全部原断言节点保留，测试方法无删除。仅新增 summary 四个键；旧夹具补 cached_turnovers、实际 on_send 回调及发送风险窗口 fallback。原 `test_liability_written_to_state_and_event` 的两个断言原文保留，在原开仓调用外新增 assertRaises(account_liability)，强化为用户要求的“拒开且记录”；未通过吞异常/跳过测试保持绿灯。没有修改 fill-sim 模块、随机种子、故障序列、N=1、任何原风控数值或验收阈值。

最终完整执行以下四条命令，没有筛选或跳过用例：

```powershell
python -m unittest discover -s tools/crossex_exec/tests -v
python -m unittest discover -s tools/fill_sim/tests -v
$py310 = 'C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/125141d8-f3d5-4518-98a0-6a4761963215/scratchpad/uvpy/cpython-3.10-windows-x86_64-none/python.exe'
& $py310 -m unittest discover -s tools/crossex_exec/tests -v
& $py310 -m unittest discover -s tools/fill_sim/tests -v
```

| Python | 执行器 | fill-sim | 退出码 |
|---|---|---|---|
| 3.12.10 | 398 tests / 117.586s / OK | 158 tests / 29.878s / OK | 两条均 0 |
| 3.10.20（用户指定解释器） | 398 tests / 128.016s / OK | 158 tests / 32.016s / OK | 两条均 0 |

每个解释器都实际完成三种子 `20261111/20261112/20261113`，各 **2,568 回合 = 2,400 随机 + 168 定向**。I1a–d、I2–I7 各项 checks 均为 2,568，exercised 全部非零；N 仍为 1。六次运行的 checks/exercised/coverage 分别保存在 [验收数据](crossex-exec-r9-validation-2026-11-12.json)，不把两版本的 wall-clock 轮询计数改成相同。原始 stdout/stderr 保存在本机 `%TEMP%`，完整 SHA-256：

| 日志文件 | SHA-256 |
|---|---|
| `crossex-r9-final-py312-crossex_exec-20261112.log` | `1a211ef88dbb36bd37cbaf3aed766c56d3195789d7883c295fc5189188bc259c` |
| `crossex-r9-final-py312-fill_sim-20261112.log` | `175eb0b7c6c79f4654fad3c84cbe89db9cffa0c0f65be3aacbc077d11a83d8f1` |
| `crossex-r9-final-py310-crossex_exec-20261112.log` | `9c5172285e83f15914dcf88b3c395da9dc19521443035bcb86e39eb3b1bc375b` |
| `crossex-r9-final-py310-fill_sim-20261112.log` | `37712819d68e430eaf3e6bfb515f9a441ec437779d07410f848726b6954a96e0` |

### CPU 实测与复现

第八轮代码在本机补测 187 币、6 所、24 小时历史的三轮采样，CPU 秒分别为 4.609375/4.515625/4.484375，平均 4.536458 秒。除以既有 30 秒周期，**仅采样**预算就约单核 15.12%，尚不包含执行协调器、账户和接口投影，故在此前优化上继续补足。原始结果为 `%TEMP%/crossex-r9-sampling-baseline-20261112.json`。

测量入口 `python -m tools.crossex_exec.benchmark_steady --bases 187 --dynamic-bases 40 --history-hours 24 --warmup 30 --seconds 180 --output steady.json`；使用整个进程（全部线程）的 `time.process_time()` 差值除以 `time.monotonic()` 实测区间，不除以宿主机核数。187 币历史预热，另外 40 币按成交额在 187/207/227 采样规模间变化，额外币在短测中尚未完成 3 小时预热。真实公共解析、请求队列、协调器、账户/行情后台和 API 投影运行；I/O 为合成数据，外部 DNS/连接被强制禁止。历史准备、30 秒预热、另行 cProfile 不计入稳态 CPU。

固定功能提交 `e24adc2` 的最终复测：Windows Python 3.12.10 / SQLite 3.49.1；1,362 原生合约，结束时 8,063,970 条历史样本；**22.718750 CPU 秒 / 180.125 墙钟秒 = 单核 12.6128%**，满足本机等价稳态 ≤15% 目标。五个完整约 30 秒窗口分别 13.53%/12.84%/12.81%/13.12%/11.39%，最后不足 30 秒的尾段计入总平均；该次完整窗口最大 13.53%。稳态工作集 47.37 MiB、进程峰值工作集 49.31 MiB，后台异常列表为空。912 次协调器 tick、45 次账户读取、43 次并发 API 投影、184 次机会轮询为含预热的总计数，采样数组含末尾单独 profile，不能将它们冒充稳态区间专用计数。

首轮同方案稳态平均 12.9578%，但有一个完整窗口为 22.28%，两次结果均保留在 [性能数据](crossex-exec-r9-performance-2026-11-12.json)，没有只保留最好一轮。采样前测与稳态测量的工作负载不同，不据此计算虚假的同比降幅；首个采样 JSON 中 mode=after 指第八轮探针相对它自己的旧实现，属于本轮继续优化之前的采样基线。短时局部测量不保证所有瞬时窗口、其他硬件或动态币完成长时间预热后的 CPU 上限。

最终原始 `%TEMP%/crossex-r9-final-steady-187-20261112.json` SHA-256 为 `b5adb1049aa60573af77a04a2262edcaf64729e70a2406dd6fa3a12b8c7ae688`；同名 `.log` SHA-256 为 `bec6a3cc25d697c9eed19854b139bc211843fc78eb2b5e378025f8c37d79d159`。仓库性能数据完整保存前测、首轮和最终复测、profile 及源文件校验值。

### 契约、线上边界与下一步

契约 [第九轮增补](live-exec-contract-2026-11-11.md#第九轮增补2026-11-12) 只追加原文，包含 POST /api/v1/equity/rebaseline、ADMIN/二次确认、400/409/503 原因、响应字段、CLI 前提、四个 summary 新字段、OKX 不可交易标志和发送时间语义。旧四个 GET 及所有旧字段保留。页面按钮、taoli1 登录态/CSRF/私有令牌转发仍按既有交接归 `live-exec-page` 模块，本轮用户明确列出的交付为 CLI + ADMIN 写接口。

公开字段依据：[OKX instruments](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments) 定义 ruleType；[Gate CrossEx 官方文档](https://www.gate.com/docs/developers/crossex/en/) 的 Query All Current Open Orders 为 GET `/crossex/open_orders`（交易所/业务/symbol 筛选可省略，本实现全量读取）。文档访问无鉴权，所有交易测试仅本地 fake/回环 HTTP，不将其视为真实交易所验收。

推送未执行；线上备份/校验、部署版本、运行服务和实际接口均未读取或修改；未 SSH/WSL/amend，未使用真实凭据。按用户提供的信息，线上影子版本是 abf8dba，当前状态没有重新确认。仓库无用户遗留改动被覆盖；测试与性能 Temp 日志、忽略的 Python 缓存保留，压测数据库仅由探针清理自己创建的临时目录。

下一任务另行授权 Linux/systemd/SQLite 3.37 与真实目录/账户的 LIVE=0 影子验证：部署前按当轮授权备份；核对新鲜快照耗时/恢复事件、负债币种和账户状态、真实 CrossEx 全量未完成订单响应；测 187 币及实际动态规模在长时间预热后的 CPU、cgroup 内存和 24h 恢复情况。Windows 3.10 是语法与行为兼容证据，不等于 Ubuntu 或真钱验收。不要删除数据库解锁，不把本地全绿或影子通过解释为开实盘授权。

## 第十轮

### 范围、基线与本地提交

工作树 `C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\crossex-exec-r8`，分支 `codex/crossex-exec-r8`，起点 `1b82eec6010ebc1cce9392914076054b1ea90d0e`；起始工作区干净。先读第九轮和接口契约。本轮只处理 baseline 故障、后台诊断和管理写接口乐观并发；外部持仓仅下述方案，没有行为改动。用户禁止 push/SSH/WSL/amend/真实鉴权，本轮完全保留该边界。

| 用途 | 提交 | 内容 |
|---|---|---|
| test | `2205230` | 从只读数据库副本提取最小公开夹具，新增故障与并发红灯测试；旧管理测试仅补请求前提 |
| test | `d040560` | 跨进程映射/范围竞争、发现线程竞争、审计失败回滚与原候选时效 |
| feat | `6fe888400a981f07277016a42a41ad08777d1e05` | 身份隔离、异常位置、原子比较更新及冲突审计 |
| docs | 本节所在独立提交 | 契约仅追加第十轮、README、next-task 和验收 JSON；用 `git log -1 --format=%H -- docs/task-handoff-2026-11-12-crossex-exec-r8.md` 查询 |

Git 仅显式暂存本轮文件；本地父仓库 worktree 索引写入的自动审批通过，没有拒绝。提交禁用 hooks/GPG，不访问远端。没有覆盖其他任务文件。

### baseline 故障：副本、真实公开 GET、旧版本与 HEAD

用户给定的 `proddb/crossex-exec-20261112.db` **先复制再用 SQLite mode=ro 打开**，原件从未被 SQLite 打开。复制到 `%TEMP%/crossex-r10-23sodv9x/crossex-exec-20261112.db`，6,037,504 字节，quick_check=ok；源文件与复制件 SHA-256 均为 `ddcc6431f17c364440448167cc14ac1118d450688f89786d765e6185f843c775`，完成后再次核验原件未变。复现写入使用另建的临时 DB，只导入公开目录；没有打开真实账户客户端或启动服务。

副本包含 4,364 个目录合约、18 个非 ASCII 基础币合约、19,984 条 baseline_samples，**baseline_state 为 0 行**；最近 BACKGROUND_FAILED 确实只有 `worker=exec-baseline,error_type=ValueError`。来自公开目录的 `牛来/哈基米/龙虾/币安人生` 等基础币能通过原始 Market/行情解析，并可能达到采样成交额门槛；`baseline.pair_key` 则要求 ASCII 大写字母数字。动态采样把这些身份传入后，使整轮中断，而非单币失败。

在起始 HEAD 上，使用副本全量公开目录和真实六所批量行情（共 **8 个公开 GET**，六组成交额加 Binance/Aster bookTicker，全部成功）复现：

```text
1b82eec: Engine.refresh_baselines (engine.py:372)
         -> Baseline.record (baseline.py:123)
         -> pair_key (baseline.py:38), ValueError
```

没有鉴权头或账户调用。原始公开响应保存在上述 Temp 目录的 `public-capture.json`。再用本地 `git archive 33933b1` 导出旧源码，对**同一公开快照、同一副本目录**回放：旧版本 `engine.py:321 -> baseline.py:123 -> baseline.py:38` 同样失败。因而该问题在 **33933b1 与起始 HEAD 均存在**，不是根据旧日志类型猜测。源代码归档、完整公开快照与复制 DB 都只留 Temp，未入仓。

实现与边界：

- `baseline.supported_base` 复用原严格身份条件；`pair_key` 仍拒绝非 ASCII、小写、分隔符、HL DEX 原名等不支持身份，没有扩展交易资格或将其改名。
- `MarketData.sample_quotes` 在生成候选前排除不支持的公共基础币；`Baseline.record` 再统一过滤 bases，覆盖样本构造、公开报价写入、无报价状态刷新和失败轮撤销引用。仅在其中一处过滤不足以修复：单所不支持身份也会在写报价或遍历空状态时抛错。
- 公开目录/market_tickers 保留原始市场；正常币种继续预热。24h 保留、6h 窗口、300 点且跨度 ≥3h、90 秒新鲜度、结构性锁/10 分钟复核、成交额/分类/映射/资金费等检查未改。
- 修复后回放同一全量快照成功：**4,958 个样本、13,170 条状态**；再做一轮新的 8 个真实公开 GET，也成功：3,934 条行情、5,020 个样本、13,200 条状态，账户调用 0。目录、成交额及时效会使轮次规模不同。
- 新鲜单轮 `ready=0` 是正常预热状态，不能声称已完成生产 3h 预热。确定性回归先验证单点不 ready，再用明确的测试历史满足原窗口，验证 BTC ready=1；没有伪造线上预热成功。
- `tests/fixtures/round10_public_baseline.json` 仅 4,221 字节：从副本抽取 Binance/Bitget 的 BTC 与牛来四个公开 Instrument，加三个公开响应的必要行情字段及原始时间。无账户、订单、权益、令牌或整库内容；unittest 禁止外部网络。

`background.exception_location` 从异常最内层取模块名与行号，日志和 `Engine.background_error` 的 BACKGROUND_FAILED 同存 `location`、`error_type`、worker。不记录异常正文、绝对路径、源码、局部变量或完整 traceback。每个 worker 按「类型+位置」限频 60 秒；同类型不同位置不会互相掩盖。

### 管理写接口与原子性

当前请求体以[契约第十轮](live-exec-contract-2026-11-11.md#第十轮增补2026-11-12)为准；原契约全文保留，仅追加。`Management.write` 在 Store 的 `BEGIN IMMEDIATE` 中读取当前值、比较、调用原业务更新并审计；进程内继续使用路径锁，跨进程由 SQLite 事务保证。没有把“先读后写”拆成两个事务。

| 路由 | 新必填前提 | 不匹配结果 |
|---|---|---|
| PUT config | 顶层 expected_version，严格非负整数 | 409 `{error:"version_conflict",current_version:N}`，版本不变 |
| mappings confirm/reject | expected_multiplier 字符串、expected_status | 409 mapping_conflict，附 current_multiplier/current_status |
| universe classify/exclude/unexclude | expected_status 字符串 | 409 status_conflict，附 current_status |

映射 GET 新增 multiplier 字符串，为 market_key 排序后第一腿/第二腿数量倍数的精确十进制表示；价格比 ratio 和原有腿字段保留。必须逐字符相等，`1000.0` 不能替代 `1000`；原 300 秒候选时效保留。后台 `Universe.discover` 也改用事务，避免另一进程发现旧 pending 状态后覆盖管理提交。

Universe 原本没有独立 status，因此 GET/写成功响应新增不透明 `u1:<64 hex>` 标记；原分类、所选市场和 excluded 保留。标记包含原始目录规则、管理决定及递增 revision；行情刷新不改变标记，决定改后再改回也会冲突。前端必须提交用户所见状态，409 后重新展示并由用户决定，不得自动补新前提重试。

冲突分别写 CONFIG_CONFLICT/MAPPING_CONFLICT/UNIVERSE_CONFLICT；事件提交后才返回 409。配置 audit GET 同时展示配置冲突，保留旧字段并追加 expected/current version 与 error。成功审计失败整笔回滚；冲突审计失败返回 503，没有假报已审计。原 ADMIN/只读隔离、有界 JSON、交易限制均不变。本轮未修改前端或 taoli1 转发；其写请求需要在独立页面任务适配新增字段。

### TDD、原断言和双版本验收

初始 13 项在旧实现上先红（9 failures/11 errors，包含 subTest 错误），日志 `%TEMP%/crossex-r10-red.log`；随后全部转绿。补齐为 17 项；跨进程发现竞争另在测试中临时恢复旧的仅 Python RLock 实现，明确得到失败，再使用新事务实现通过，见 `%TEMP%/crossex-r10-discovery-old-lock-red.log`。没有回退工作区、改随机种子、skip/xfail 或降低阈值。

相对起点，对两套测试的 **36 个原文件、881 个函数、1,934 个断言节点**做 AST 检查，原函数无删除、原断言无删改。唯一旧测试适配在 `test_round8_admin.AdminTests.write`：构造旧测试请求时带上新必填版本/状态；原每个 HTTP 状态码、范围和权限断言原文保留。新测试使用独立原始 HTTP helper，确保真正验证“缺少前提必须 400”，没有被旧 helper 自动补齐。fill-sim 源码和测试完全未改。

全量命令：

```powershell
python -m unittest discover -s tools/crossex_exec/tests -v
python -m unittest discover -s tools/fill_sim/tests -v
$py310 = 'C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/125141d8-f3d5-4518-98a0-6a4761963215/scratchpad/uvpy/cpython-3.10-windows-x86_64-none/python.exe'
& $py310 -m unittest discover -s tools/crossex_exec/tests -v
& $py310 -m unittest discover -s tools/fill_sim/tests -v
```

| 解释器 | 执行器最终全量 | fill-sim 全量 | 退出码 |
|---|---|---|---|
| Windows 3.12.10 / SQLite 3.49.1 | 415 项 / 113.912s / OK | 158 项 / 29.891s / OK | 全部 0 |
| 指定 Windows 3.10.20 / SQLite 3.50.4 | 415 项 / 121.152s / OK | 158 项 / 31.416s / OK | 全部 0 |

两个版本都实际执行种子 **20261111、20261112、20261113**，每个 **2,568 回合 = 2,400 随机 + 168 定向**；N=1，I1a–d、I2–I7 每项 checks=2,568、exercised 非零。完整六组计数、日志 SHA-256、数据库与夹具指纹、复现结果均在 [第十轮验收 JSON](crossex-exec-r10-validation-2026-11-12.json)。fill-sim 沿用本轮同一最终实现下的完整首轮结果；此后只补执行器测试，两版本执行器已完整重跑。78 个 Python 文件通过 3.10 AST 语法检查；`git diff --check` 通过。

代码自审重点覆盖：BEGIN/比较/更新/冲突审计的事务生命周期、不同进程竞争、后台发现覆写窗口、成功与失败审计回滚、严格原身份边界、异常敏感文本不输出、原风险行为和测试断言保留。未启动子代理。没有将本机 3.10 视作 Ubuntu/systemd/cgroup 验收；本轮未重新测 CPU/内存。

### 外部持仓：方案比较，尚未实施

用户说明该多单不是本程序开的；副本 `last_upnl.positions` 可核对到精确身份 **`crossex|HYPERLIQUID_FUTURE_io:OAI_USDC|long`**，并记录 position_mismatch 与 daily_loss 两个锁。这里只确认历史副本，未鉴权查询当前持仓或仓位归属。

现有链路：`Accounts.snapshot` 汇总全账户 positions/upnl/notionals；`Engine.reconcile_positions` 将全部远端持仓与本地流水持仓精确比较；`totals/roll_daily_upnl` 使用全账户浮亏；`reconcile` 还取 `min(流水损益, 整户权益差)`；`HardLimits` 使用整户权益、保证金与全远端名义敞口。因此“只在 mismatch 里忽略 HL”并不能去掉 daily_loss，也不应静默绕开这些保护。

| 维度 | (a) 保持专用账户要求 | (b) 允许永久不交易合约上的外部仓位 |
|---|---|---|
| mismatch | 任一非本程序仓位仍阻断；最易识别丢单/漏账 | 只排除经明确登记、精确身份匹配且与本程序永久禁止交易集合一致的外仓；其余仍阻断 |
| 日亏 | 账户所有浮亏/权益变化均影响当前日亏 | 程序日亏剔除外仓的当日贡献，需完整归属浮亏变化、已实现损益、费用与资金费；不能只去掉当前 upnl |
| 整户风险 | 当前保护全部保留 | 外仓继续计入原始整户权益基线/回撤和保证金倍数，仍可能触发停开、减仓或停机 |
| 总亏损/名义额 | 当前规则 | 本需求没有授权放宽，仍保留原累计亏损与总名义额检查；外仓可能继续占用 600 USD 总额度或触发累计亏损，不能承诺因此能开仓 |
| 恢复/运维 | 配置简单，需专用账户或由用户自行处置外仓 | 需要永久禁止交易注册表、收益归属、重启/日界线迁移及专门审计；外仓未平时现有 rebaseline/release-halt 仍拒绝 |
| 主要风险 | 外部人工操作会阻断执行，资金利用限制较多 | 分类错误会把本程序丢失的仓位藏起来；共享抵押使外仓亏损仍能危及本程序；收益漏归属可导致错误日亏放行 |

若选择 (b)，建议后续任务必须同时明确并验收以下边界：

1. 外仓登记按原始 route/exchange_type/business/symbol/quote/DEX 绑定。本例保留 `HYPERLIQUID`、`io:OAI`、`USDC` 和原始完整 symbol；不能按 `OAI/OPENAI` 归一 ticker、方向或“暂时不在候选中”自动豁免。永久禁止交易应覆盖两个方向、别名映射、重启、初始准入及最终发送关口。
2. 若该精确合约存在本程序历史归属、活动仓位、未终结/UNKNOWN 订单、待记账或恢复义务，不得改标外仓来解除 mismatch。未知身份、字段缺失、两类归属重叠或目录规则变化应保守停开，保留全部原始远端快照。
3. 分别保存“全账户风险快照”和“本程序对账/日亏投影”。对账只比较本程序集合，日亏使用可核实的本程序已实现损益、费用/资金费和当日浮亏差。`reconcile` 的原整户**日权益差**分支也必须按完整外仓当日净贡献扣除，否则外仓损失会再次流入 daily_loss。不能通过删掉整个权益兜底或把外仓当前亏损当成固定抵扣额实现。
4. 外仓开/平/增减、跨 UTC 日界线、结算资金费、转入转出与已实现收益必须能核对；未取得所需流水时不能假定为零。单独的原始整户 `account_equity_baseline`/权益回撤、保证金倍数、负债、累计亏损及总名义额维持保守规则；调整程序日亏口径不等于修改账户硬限额。
5. 本程序不得为消除 mismatch 自动平掉外仓。当前 `flatten_all` 的停机完成条件要求远端全空；若保留外仓，必须区分“程序仓位已平”和“全账户已平”，不能误报全户安全。现有管理重基线/解除停机要求全空，若未来要允许带外仓操作，需另行明确前提与审计，不能顺手放宽。
6. 验收需覆盖相同 ticker 不同 DEX、两方向、程序孤单/UNKNOWN、外仓盈利与亏损、外仓结算/平仓、跨日/重启、目录变化、外仓导致保证金或权益硬限额触发、审计故障，以及不能发单到永久禁止合约。

当前继续采用 **(a)**。方案 (b) 没有实现，没有增加忽略名单、重写任何账户基线、解除 position_mismatch/daily_loss 或操作该 HL 仓位；等待用户选择后新建外部持仓模块任务。

### 线上状态、产物和下一任务

未 push、未 SSH/WSL/amend、未真实鉴权、未部署、未生产备份或线上验收。线上影子 `33933b1` 来自用户说明；本轮只通过本地源码和数据库副本复现，没有确认服务器当前 HEAD 或服务状态。数据库复制校验是离线诊断证据，不是新做的线上备份。原临时副本、公开快照、旧源码归档、TDD/全量日志继续保留，未提交整个 DB。

本轮文件均进入 test/feat/docs 分组提交；交付后工作区应干净，没有新未跟踪文件，Python 缓存仍按既有规则忽略。下一任务：页面按新请求前提接入；生产升级、Linux 兼容与持续 ≥3h 的真实影子预热另行授权。外部持仓待用户选择后独立实施，不以本轮本地通过作为实盘授权。

## 第十一轮

### 目标、范围与交付

用户已选择第十轮“外部持仓：方案比较”中的 (b)。本轮模块为 crossex-exec 外仓登记与归属：手动登记同一 CrossEx 账户内由 Astro 等工具持有、且本程序永久不交易的精确合约，六项边界均纳入实现及离线验收。不存在“只忽略 mismatch”或 USDC 默认按 1 折算的捷径。

- 工作树仍为 `C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8`，分支 `codex/crossex-exec-r8`；基线 `adaf1736ebed29985c409b49c5bb8ba3ba9a9690`，开始时工作区干净。
- test 提交 `ce94569`：40 项专项测试及外仓不变量场景；保留原断言。
- test 收尾提交 `6088293`：新增管理读取失败撤销旧收益证明的两个回归测试，共 42 项专项。
- feat 提交 `d3edffa`：登记、永久禁单、证据归属、原硬风险保护和管理/停机路径。
- feat 收尾提交 `f51b248`：管理读取前撤销旧收益证明，失败证据保留；最终实现以此为准。
- docs 为包含本节、追加契约、README、next-task 与验收 JSON 的独立后续提交；不 amend。
- 只做本地交付。无 push、SSH、WSL、真实鉴权请求、真实订单、线上备份或部署；本地 Git 提交禁用 hooks/GPG，仅写本任务文件。

完整 API/CLI 与状态语义见[第十一轮追加契约](live-exec-contract-2026-11-11.md#第十一轮增补2026-11-12)，原契约只有新增行；[验收数据](crossex-exec-r11-validation-2026-11-12.json)保存四套全量结果、六次模拟计数、十三份 TDD 红灯日志指纹、源码/测试指纹和数据库副本证据。

### 关键代码与六项边界

| 边界 | 实现入口与最终行为 |
|---|---|
| 精确登记及永久禁单 | `external.py` 的 ExternalRegistry/ExternalAdmin。`meta.external_contracts` 为逻辑登记表，完整 route/exchange_type/business/symbol/quote/dex 与原始 rule 冻结；两方向永久禁止本程序发单，取消登记仍留 forbidden 墓碑。`engine.py` 初审/配置复核和 `execution.py` prepare、单腿、SENT 后共享发送屏障均检查原始身份，不依赖归一 ticker 或可选 Engine 回调。 |
| 归属和异常证据 | 任一匹配的程序订单/pair 历史均拒绝登记，包含孤单、UNKNOWN、待记账、已平历史；全局内存待写/正在处理订单也拒绝。目录不一致/缺失、原始身份不全、归属重叠停开。`accounts.py` 逐响应保留原始账户/仓位，后一个读取失败也保留前一个已读响应；Engine 持久化风险/失败快照。 |
| 程序对账和日亏 | `external_accounting.py` + Engine.reconcile/totals/roll_program_upnl。仅精确登记外仓从程序集合排除，其他远端仓位仍 mismatch；程序费用/已实现核对不可变订单，本程序资金费从完整账本核实，浮亏按程序 UTC 基线差分。原日权益兜底减同起点的完整外仓净贡献后仍取 min；未核实不记零。 |
| 全户硬风险 | 原 HardLimits 使用全户 equity/margins/liabilities/notionals；外仓计入 600 总额度，单所权益回撤、累计亏损、保证金及正负债停开规则保留。外仓归属失败也调用 enforce_account_protection，硬止损不能被流水失败遮蔽。 |
| 生命周期与管理 | flatten_all 永不发送外仓订单；program_flat 与 account_flat 分开，只有程序仓位/订单/记账义务全清才能 HALTED，有外仓明确报告仍在。EquityAdmin 允许仅已登记外仓的 rebaseline/release-halt，但原本地清空、无恢复锁、无远端活动订单、无负债、正权益/新鲜度前提保留，且保证金至少 3、流水与程序费用/资金费可核实，审计记录完整外仓快照。 |
| 验收 | 精确 DEX、两方向/别名、孤单/UNKNOWN/已平归属、正负收益、开平增减/资金费、跨日重启、目录漂移、硬限额、审计故障、不能发单均有专项覆盖；原模拟器追加 96 回合外仓场景，三种子双版本全过。 |

`market.py` 另存完整 CrossEx 公共规则目录，包含本程序不交易的 Hyperliquid DEX 合约；没有把它们加入本程序交易资格集合。目录新鲜度上限六小时；冻结规则变化需人工核对并以新的管理前提登记，不能静默迁移 ticker。

`management.py` / `api.py` 新增 GET external-positions 与 ADMIN POST register/unregister。全局 `x1:` 状态包含登记表及完整目录规则；比较、写入、成功/冲突审计由同一个 SQLite 写事务保护，两个不同进程使用同一状态也只允许一个成功。409 后不能自动重试。审计故障回滚并返回 503；没有 Engine 管理回调不允许离线写库兜底。

`__main__.py` 新增 external-list/register/unregister。运行中走 ADMIN API，停机走原 env/flock 与强制关闭交易写入的维护路径。取消登记须当前该合约两方向远端全空且无活动订单，永久禁单不会取消。首次目录刷新可能让刚读到的前提失效，应再次查看再决定。

每次登记/取消改变 attribution revision，旧收益证明失效，显式程序空仓 rebaseline 建立新起点。登记本身不重写权益/日亏基线、不清除任何旧损失锁。带外仓重基线记录 raw_accounts/raw_positions/完整流水及投影，保留原订单统计，以已核实程序资金费偏移建立新计量期；release-halt 只处理原权益停机原因，不清当日日亏或 STOP 文件。

### 收益证据、跨日和 USDC 限制

公开文档核实了 [CrossEx account_book/history_trades](https://www.gate.com/docs/developers/crossex/en/) 的毫秒窗口、分页、原始合约/交易所、账本类型、change、成交数量、fee/fee_coin、rpnl、position_mode/position_side；以及 [Bitget UTA financial-records](https://www.bitget.com/docs/catalog/account/assets-balance) 和[资金费枚举](https://www.bitget.com/docs/uta/enum)。没有调用这些真实鉴权端点。

实现使用全账户固定窗口流水，不能只查当前持仓或 funding：

1. 两次完整账户快照夹住流水读取，要求现金余额、持仓数量和累计结算未变化，且两边无活动订单；使用开始时刻计算十秒有效期。
2. 账本/成交 ID 唯一，窗口完整，起点保存历史 ID/时间/内容哈希；已见历史不能消失或改变，迟到的旧流水不能被当零忽略。同一毫秒新到账的资金费按 ID 处理，不因 `<=epoch` 被丢掉。
3. 逐交易所/币种现金余额差必须与新增账本金额一致，防止“空流水=没有资金费”的错误。外仓数量由成交历史逐笔演进，逐订单已实现/费用必须与账本相符；资金费、强平费只在账本计一次。
4. 转入转出、换币、利息、借还款、未知归属/类型、缺页、重复、账本与成交不符、账户读取中发生结算均视为不可核实并停开。页数/时间预算耗尽也不返回部分成功。
5. 跨 UTC 日先核对含停机期间结算在内的完整区间，再同步滚动外仓贡献和原日权益兜底的观测起点；程序浮亏使用上一份有效跟踪快照，已实现/资金费按当日 UTC 流水归属。重启保留基线、版本与历史索引。
6. 每次新账户缓存发布先撤销旧收益证明；开仓和最终发送不能用上一份缓存的证明授权新快照。身份/收益失败仍检查全户硬风险并平程序仓位。

**实际 io:OAI_USDC 尚不能据此恢复开仓。**公开文档未提供可核实的历史 USDC→整户权益计价币折算证据。登记、禁单、mismatch 隔离和只平程序仓位支持该原始身份；但非零 USDC 浮亏/现金贡献按“不可核实→停开”处理，未假定 USDC=USDT=USD=1。不同币种盈亏抵消为零也不能绕过。此处严格执行用户“取不到所需证据不得假定零”的要求，未把 USDT 离线归属用例冒充该真实账户收益验收。

当前程序日亏的已核实成功路径在 USDT 原生贡献离线场景验收；USDC 非零场景验收的是保守拒绝。后续要让真实 USDC 外仓与程序持续共存，需要能证明每个起点/结算时刻计价的官方数据及真实只读对账；不能仅补一个静态倍率或管理员确认开关。

### 负债建议：保持停开，等待 PM 决定

本轮**没有实现“外仓保证金借款仅告警”**。所有正负债继续停开告警，连续三份新鲜无负债观测才恢复该原因。负债不能因为登记归属而自动变成豁免项。

建议 PM 当前保持停开。即使借款起因完全属于外仓，CrossEx 共享保证金/权益仍存在强平传导；借款利息、自动借还款、换币/抵押折扣会改变整户现金与可用保证金，未必能持续、逐笔地证明负债只服务于外仓。外仓变更与流水时延还会让一次性的归属证明迅速失效。

如果 PM 将来决定评估只告警，至少需要可持续核实的逐笔借款归属/币种/利率与余额流水、独立负债上限、抵押折扣和强平压力测算、保证金安全余量及无归属证据立即停开的机制；原 600 总额、权益回撤、累计亏损和保证金硬线仍不得随之豁免。这是后续决策输入，本轮没有开关、白名单或代码例外。

### TDD、原断言与最终验证

先写外仓登记失败测试，旧实现缺少接口时 11 项、23 errors；依次补 API、收益、发送关口、资金费、异常快照、新缓存撤销、活动订单、现金连续性和 UTC 跟踪用例，每个新增失败路径先红再实现。最后追加重基线 409 语义、部分账户读取证据与管理失败撤销旧证明测试，均先红后绿；十三份红灯日志与最终全量日志保留 Temp 并记录 SHA-256。

收尾自审发现登记/带外仓重基线读取失败后，原 `_equity_admin_active` 临时标志解除时可能沿用旧 RUN/收益证明。两项反例先验证 can_open 错误返回 true；补丁在管理读取前撤销收益证明并停开，只有新对账能恢复。管理完整观测保存在 external_admin_snapshot，失败读取的原始部分保存在 external_failed_snapshot；失败不重写登记或权益基线。

相对 `adaf173`，38 个原测试文件、910 个函数和 98 个类均保留。最终确定性 AST 审计核对 **2,037 个原断言调用**，包括属性调用、直接辅助断言调用和 assert 节点，原 owner 与调用次数逐项一致，无删除/放宽。此最终计数更正中途报告的 2,082，定义与更正记录写入验收 JSON。旧测试唯一替换行为是模拟回合常量从 2,568 增至 2,664；原随机种子、前 2,400 随机回合、原 168 定向回合和 I1a–d/I2–I7 断言原文不变。fill-sim 源码和测试完全未改。

| 解释器 | 执行器最终全量 | fill-sim 全量 | 退出码 |
|---|---|---|---|
| Windows 3.12.10 / SQLite 3.49.1 | 457 项 / 121.766s / OK | 158 项 / 29.423s / OK | 全部 0 |
| 指定 Windows 3.10.20 / SQLite 3.50.4 | 457 项 / 136.072s / OK | 158 项 / 31.608s / OK | 全部 0 |

执行器最终实现两个版本完整重跑。fill-sim 结果来自同轮完整首跑；之后只有执行器 API 拒绝语义、原始证据保留和管理失败撤销证明的小修，fill-sim 未改。命令仍为本交接第十轮所列两条 discover，各用 3.12 和用户指定的 3.10 执行。

两个版本均实际执行种子 **20261111、20261112、20261113**，每种子 **2,664=2,400+168+96** 回合。原十项不变量 checks 全为 2,664、exercised 均非零、N=1。另有每种子 96 次外仓检查，四个场景分别为外仓剧烈亏损 -250、外仓亏损后结算/平仓、外仓保证金跌至 1.5、目录规则变化；所有场景发生计数非零，禁止外仓发送、外仓数量不被程序改变、程序/账户全平报告均核对。未用降低阈值或跳过原断言制造通过。

83 个 Python 文件通过 3.10 AST 语法检查；`git diff --check` 通过。代码自审覆盖跨进程登记事务、发送与管理锁顺序、永久禁单最终关口、旧缓存证明撤销、现金/数量/跨日证据、失败仍保留全户硬退出、审计回滚和原始响应保留。未启动子代理；未把 Windows 3.10 结果当作 Linux/systemd 验收，也未重新宣称 CPU/内存指标。

### 数据库副本集成证据

用户指定源文件仅按文件读取并复制，所有 SQLite 打开和变更在 Temp 复制件上执行。首次 `copy.db` 和源文件前后 SHA-256 均为 `ddcc6431f17c364440448167cc14ac1118d450688f89786d765e6185f843c775`；只读副本与最终可写集成副本 quick_check 均为 ok。

产物保留在 `%TEMP%/crossex-r11-db-ct9enxiq/`：`copy.db` 为原样只读诊断副本；`integration2.db` 为通过的可写集成结果；前一次脚本参数错误产生的 `integration.db` 保留，未覆盖或删除。集成使用副本中真实存在的原始 io:OAI_USDC 身份、旧浮亏证据和既有锁；当前数量、标记价格、现金响应和目录是明确的离线夹具，不是当前账户鉴权证据。

副本登记后：原五项风险/日亏基线均未改；三次新鲜一致程序观测恢复 position_mismatch；原 daily_loss 仍在；收益证据为未核实、can_open=false；订单从 0 维持 0；永久禁单和原始风险快照均落库。未删除数据库、篡改历史亏损或自动解锁。原库、临时完整数据和账户快照不入仓；仓库只记录哈希、布尔验收与测试合成数据。

### 线上状态、未验证项与下一任务

本地 test/feat/docs 完成后工作区应干净；无起始 WIP 或不明归属产物被删除，新增 Temp 红绿灯日志、数据库副本与 Python 缓存保留。没有 push/SSH/WSL/amend/鉴权/部署，未确认服务器 HEAD/服务状态，未产生新的生产备份。历史影子 `33933b1` 仍只是旧轮用户提供的信息，本轮不能当作当前线上版本。

明确剩余边界：真实流水权限/分页/结算延迟、USDC 历史权益估值、Linux POSIX 权限/flock/systemd、实际账户风险与 24h 影子未验收。CrossEx 每种历史最多 20 页，Bitget 单次范围最多 30 天；数据过多或超过十秒采样预算时保守停开，不能称已解决实盘性能/连续性。永久墓碑不会自动清理，长历史归属扫描和全窗口流水成本需在真实影子验收中测量。

本模块本地交付结束；下一模块请新建任务。建议先授权真实账户只读流水/估值证据与 Linux 影子验证，再决定是否生产发布；页面按钮/ADMIN 转发适配单独进入 live-exec-page。负债告警豁免等 PM 决定，不随部署顺手实现。任何下一任务都保留本节六项边界及已有风控，不以本轮通过作为实盘开仓授权。

## 第十二轮

### 范围、基线与本地提交

本轮仅处理 crossex-exec 外仓结算币估值及原锁恢复说明。先读第十一轮；工作树 `C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8`，分支 `codex/crossex-exec-r8`，起始 HEAD `bfb28615dc4f6d3d385d6af86fc960faa0646afe`，起始工作区干净。PM 三项裁决均已落地：公开现货保守折算、任何正负债保持停开并告警、不自动清旧亏损锁并交付既有流程的线上命令。

| 类型 | 本地提交 | 内容 |
|---|---|---|
| test | `0abce17` | 18 项专项先红，覆盖正负贡献、证据不足、极端偏离、流水/混币/跨日和原硬保护 |
| test | `91bcdeb` | 迟到队列回调的币种捕获红测、缓存不得延长源报价寿命 |
| test | `1b25560` | 区间上界不变量、HALTED+mismatch 三快照/release-halt 顺序、跨日失败保留旧锁 |
| feat | `0fe7941` | 新 `external_fx.py`，并接入 market/account snapshot/external accounting/final admission |
| docs | 后续独立提交 | 本节、追加契约、README、next-task 和本轮验收 JSON；自身 SHA 用下方命令读取 |

```powershell
git log -1 --format=%H -- docs/task-handoff-2026-11-12-crossex-exec-r8.md
```

仅逐项暂存任务文件，本地提交禁用 hooks/GPG。首次普通 git add/commit 因父仓库 worktree 索引写权限失败，随后按用户已授权的本地提交范围取得自动审批；没有自动审批拒绝。未 push、SSH、WSL、amend、真实鉴权、真实订单、线上备份或部署。没有调用子代理。

### PM 折算模型、实现与边界

完整新增契约见[第十二轮增补](live-exec-contract-2026-11-11.md#第十二轮增补2026-11-12)。关键入口：

- `external_fx.py`：三个固定公开现货 GET URL、精确币对解析、至少两家中位价、±0.2% 区间、源时间 60s 限制与保守贡献上界。检查成功码、现货、原始 symbol、买卖价、有限数和交易所/接收时间。所有有效来源的 bid/ask 必须在同一不确定带内，否则拒绝整币种，不修剪极端值。
- `MarketData.spot_fx`：复用现有公开 RequestQueue/Client/Limiter/pool，优先级 0；每币种三个来源，30s 内只复用仍有效证据，超过源时间 60s 立即刷新/拒绝。失败不刷新旧价格时间。回调捕获各自币种，超时后迟到的旧任务不能按后一个币种解析。
- `Engine.read_account_snapshot`：在账户 worker 读取完整原账务快照后附加公开 FX。报价失败仍返回账户风险证据，原全户硬退出不被遮蔽。没有公开方法的旧离线夹具不假定 FX=1，非零非 USD 贡献仍拒绝。
- `ExternalAccounting._evaluate`：所有原现金、账本、成交、费用、数量、历史索引检查保留。先以原始结算币分别算贡献，再折算到原 USD 报告口径；起点仍保存原币金额，非 USD 起点/当前/现金非零项即使净抵消也必须有价格。新增原币贡献/净现金和报价证据，不删除原成功字段。
- `Engine.require_external_income`：开仓和最终发送关口重复核验原来源时钟；原收益证明的 10s TTL 不能延长 FX 的 60s TTL。此处不联网。

对每个币种 `c`：

```text
x_c = 同一起点后完整外仓净现金_c + U_c(now) - U_c(anchor)
p_c = median(至少两家不同交易所的 COIN/USDT 现货 (bid+ask)/2)
L_c = p_c * 0.998; H_c = p_c * 1.002
C_c_upper = x_c * H_c  (x_c >= 0)
          = x_c * L_c  (x_c < 0)
          = x_c*p_c + |x_c|*p_c*0.002
equity_daily = E_now - E_anchor - sum_c(C_c_upper)
程序日损益 = min(原已核实程序日损益, equity_daily)
```

USDT/USD 沿用原报告口径，不加 FX；其他币种只用直接现货币对，不推断 peg、别名或间接路径。`p=1` 时，外仓盈利 `+50` 扣 `+50.1`，外仓亏损 `-50` 扣 `-49.9`；若整户分别变化 `+30/-70`，两者均得程序权益兜底 `-20.1`，比自身 `-20` 更严格。区间内任意 `r` 都满足 `C_upper>=x*r`，因此扣除不会减轻自身亏损。不同币种分别折算；USDC 两家共同报 0.8 时使用 0.8，若一报 0.8、一报 1 则拒绝。

原登记 `symbol_identity` 仍只允许 USDT/USDC；本轮实际新增的登记收益成功路径是 USDC，没有顺手放宽币种身份白名单。通用币种的 FX 函数/离线报价测试不等于其他币种获准登记，USD 也仅为报告单位兼容分支。

该证明限定于 PM 授权的当前公开估值区间，**不是历史结算时点汇率或平台抵押折扣的重建**。没有把 ±0.2% 宣称为真实价格永不越界的保证。原完整流水核实和程序日损益 min 兜底保留，全户总额 600、分所权益回撤、累计亏损、保证金和任何正负债停开继续独立检查；本轮不降低任何阈值，不实现负债豁免，不修改 `limits.py/equity.py/stops.py/execution.py`。

公开文档仅核实 [OKX](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-ticker)、[Bitget](https://www.bitget.com/legacy-docs/classic/spot/market/Get-Tickers)、[Bybit](https://bybit-exchange.github.io/docs/v5/market/tickers) 的 GET、现货身份、买卖价与时钟字段。实际适配器只通过离线响应和原队列验收，没有联网试账户或声称当前三家均可用。

### TDD 与最终验证

首批 18 项红测实际为 9 failures / 23 errors（含 subTest），原实现无法核实非零 USDC 且无公开 FX 方法。实现后全绿；随后回调迟到反例实际出现 1 error，冻结 lambda 的币种参数后通过。最终共 23 项专项；最后三项是既有恢复路径/代数上界验证，未声称它们修改前失败。两份红灯及专项绿灯日志保留 `%TEMP%`。

| 解释器 | 执行器最终 discover | fill-sim discover | 退出码 |
|---|---|---|---|
| Windows Python 3.12.10 / SQLite 3.49.1 | 480 项 / 124.878s / OK | 158 项 / 29.191s / OK | 全部 0 |
| 指定 Windows Python 3.10.20 / SQLite 3.50.4 | 480 项 / 134.661s / OK | 158 项 / 31.248s / OK | 全部 0 |

两条命令均在两个解释器实际运行。执行器增加最后三项验证后双版本完整重跑；fill-sim 本轮全量结果保留，之后只增加执行器测试和文档，其源码与测试始终未改。

```powershell
python -m unittest discover -s tools/crossex_exec/tests -v
python -m unittest discover -s tools/fill_sim/tests -v
$py310 = 'C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/125141d8-f3d5-4518-98a0-6a4761963215/scratchpad/uvpy/cpython-3.10-windows-x86_64-none/python.exe'
& $py310 -m unittest discover -s tools/crossex_exec/tests -v
& $py310 -m unittest discover -s tools/fill_sim/tests -v
```

两个版本的原模拟器均运行种子 `20261111/20261112/20261113`，每种子仍为 **2,664 = 2,400 + 168 + 96** 回合，N=1，I1a–d/I2–I7 checks 各 2,664、exercised 全部非零，原 96 次外仓检查保留。本轮模拟器和原有测试文件完全未改，没有删改/放宽旧断言或改变种子/回合常量。

[验收 JSON](crossex-exec-r12-validation-2026-11-12.json)记录四套最终结果、六次模拟器计数、TDD 日志 SHA-256、源码/测试指纹、旧文件保持和只追加契约检查。最终 `git diff --check` 与 Python 3.10 AST 语法检查通过；Windows 3.10 结果不替代生产 Linux/systemd 验收。

保留检查以基线 Git 树为准：34 个原执行器 Python 测试文件及整个 fill-sim 共 86 个文件在统一 CRLF 后逐文件相同，67 个执行器 Python 文件通过 3.10 AST 解析。文中三个 Python heredoc 已逐字提取，在 DNS/socket 守护及合成数据库下执行备份/只读检查；HALTED 维护段 mock env、flock、适配器读取、公开目录和时钟，验证三次匹配、零订单、HALTED/日亏/权益锁与外仓基线保留。它证明命令与当前方法兼容，不替代 Linux 权限或线上账户验收。

### Claude 线上操作顺序：登记、对账和旧锁

**以下为待 Claude 在线上执行的操作单，本轮没有执行。**先核对实际 systemd `WorkingDirectory/ExecStart/User` 与已审阅部署 SHA；示例使用当前仓库模板的 `/home/ubuntu/wubin/crossex-exec`、`var/crossex-exec.db`、`crossex-exec.env` 和 `crossex-exec.service`。若实际路径不同，应按受控配置替换，不能操作 taoli1 的 `/data/radar.db` 来解执行器锁。不要把 env/令牌内容打印出来。

1. 核对部署和运行状态，保留执行器数据库备份。SQLite backup 包含已提交 WAL，不用在线 `cp` 单独主文件冒充一致备份。下面生成独立目录，打印备份路径、quick_check 和 SHA-256；若任何检查失败停止后续步骤。

```bash
systemctl show crossex-exec.service -p WorkingDirectory -p ExecStart -p User -p ActiveState
cd /home/ubuntu/wubin/crossex-exec
git rev-parse HEAD
umask 077
python3 - var/crossex-exec.db <<'PY'
import hashlib, pathlib, sqlite3, sys, uuid
source = pathlib.Path(sys.argv[1]).resolve()
if not source.is_file():
    raise SystemExit('database_not_found')
folder = source.parent / ('r12-backup-' + uuid.uuid4().hex)
folder.mkdir(mode=0o700)
target = folder / source.name
with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
    with sqlite3.connect(target) as dst:
        src.backup(dst)
        result = dst.execute('PRAGMA quick_check').fetchall()
        if result != [('ok',)]:
            raise SystemExit('backup_check_failed')
print(target, 'quick_check=ok', hashlib.sha256(target.read_bytes()).hexdigest())
PY
```

2. 读取最新登记前提，核对原始 `io:OAI_USDC` 身份与完整目录。已正确登记则跳过写入，不能再次登记来清锁。尚未登记时把本次输出的 **完整 x1:...** 手工填入，运行中 CLI 走 ADMIN API，停止时走原 env/flock 维护路径。409 后重新查看并审阅变化，不自动重试；停止状态首次目录刷新也可能导致 409。

```bash
python3 -m tools.crossex_exec external-list --db var/crossex-exec.db
python3 -m tools.crossex_exec external-register --env-file crossex-exec.env \
  --route crossex --exchange-type HYPERLIQUID --business FUTURE \
  --symbol HYPERLIQUID_FUTURE_io:OAI_USDC --quote USDC --dex io \
  --expected-status 'x1:<本次读出并核对的完整64位摘要>' --reason r12_reviewed_external_ownership
```

登记后原风险基线/daily_loss_day 不会被改写；归属 revision 变化意味着必须在程序空仓等前提满足后显式建立新权益起点。查看锁和证据可使用以下只读命令，账户 ID、余额原始响应无需输出：

```bash
python3 - var/crossex-exec.db <<'PY'
import json, pathlib, sqlite3, sys
path = pathlib.Path(sys.argv[1]).resolve()
with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
    for key in ('state', 'halt_reason', 'stop_locks', 'daily_loss_day',
                'position_mismatch', 'position_match_streak', 'external_income'):
        row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        print(key, json.loads(row[0]) if row else None)
PY
```

3. 先恢复 `position_mismatch`。若服务仍在运行，保持现有停开条件，等后台至少三次**不同、足够新鲜、无非终态/待记账订单且程序远端等于本地**的匹配快照，核对 `POSITION_MATCH_RECOVERED`、`position_mismatch=false`。登记外仓本身不直接清此锁，同一份缓存反复读不计三次。

若已经 **HALTED 且 mismatch 尚在**，不能直接启动（`assert_startable` 会拒绝），也不能先 release-halt（`local_flat` 会拒绝 position_or_order_lock）。此时执行下方停机维护分支；它只调用现有读取/三次仓位对账方法，所有交易 Transport 强制 `live=False`，不调用 startup/tick/全平、不直接删除或写空任何锁。若有程序仓位、未知单、待记账、目录漂移、负债或不完整/过期证据，则中止，由原恢复流程处理，不能把此命令当作强制解锁。

```bash
# 仅在已确认 HALTED、程序仓位已收尾的分支执行。
# 正在运行时 systemctl stop 会进入原 shutdown 收尾，可能减程序仓位；不要强制 kill。
sudo systemctl stop crossex-exec.service
python3 - crossex-exec.env <<'PY'
import sys, time
from pathlib import Path
from tools.fill_sim.core import dec
from tools.crossex_exec.__main__ import maintenance_lock
from tools.crossex_exec.security import load_env, Credentials
from tools.crossex_exec.transport import Transport
from tools.crossex_exec.adapters import CrossEx, Bitget
from tools.crossex_exec.accounts import Accounts
from tools.crossex_exec.storage import Store
from tools.crossex_exec.market import MarketData, RULES_URL
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.equity import EquityAdmin
from tools.crossex_exec.external_accounting import read_snapshot, program_activity
from tools.crossex_exec.limits import DEFAULT_VAR

config = load_env(sys.argv[1])
path = Path(config.get('CROSSEX_DB', DEFAULT_VAR + '/crossex-exec.db'))
if not path.is_file():
    raise SystemExit('database_not_found')
with maintenance_lock(path) as acquired:
    if not acquired:
        raise SystemExit('service_still_running')
    with Store(path) as store:
        if store.meta('state') != 'HALTED':
            raise SystemExit('use_running_reconciliation')
        cross = CrossEx(Transport('gate', Credentials(config.get('CROSSEX_API_KEY', ''),
            config.get('CROSSEX_API_SECRET', '')), live=False), config.get('CROSSEX_POSITION_MODE', 'DUAL'))
        bitget = Bitget(Transport('bitget', Credentials(config.get('BITGET_API_KEY', ''),
            config.get('BITGET_API_SECRET', ''), config.get('BITGET_API_PASSPHRASE', '')), live=False),
            config.get('BITGET_POSITION_MODE', 'hedge_mode'))
        accounts = Accounts(cross, bitget)
        market = MarketData(store)
        engine = Engine(store, market, accounts, live=False, config=config,
            stop_file=config.get('CROSSEX_STOP_FILE', DEFAULT_VAR + '/STOP'))
        try:
            if not engine.external.records():
                raise SystemExit('register_exact_external_contract_first')
            rules = market.fetch('gate', RULES_URL, priority=2)
            if not isinstance(rules, list):
                raise SystemExit('external_catalog_unavailable')
            store.set_meta('external_catalog', dict(at=time.time(), rules=rules))
            for n in range(3):
                snapshot = read_snapshot(accounts, store, time.time)
                if snapshot['activity']['complete'] is not True or not 0 <= time.time()-snapshot['captured_at'] <= 10:
                    raise SystemExit('account_evidence_unavailable')
                projection = engine.external.split(snapshot)
                if projection['positions'] or snapshot['liabilities'] or any(m is None or dec(m)<3 for m in snapshot['margins']):
                    raise SystemExit('program_positions_or_account_risk')
                program_activity(store, snapshot['activity'], snapshot['accounting_at'])
                if not 0 <= time.time()-snapshot['captured_at'] <= 10:
                    raise SystemExit('account_snapshot_stale')
                if not engine.reconcile_positions(snapshot):
                    raise SystemExit('position_or_order_recovery_required')
                if n < 2:
                    time.sleep(1)
            EquityAdmin(store, accounts).local_flat()
            print('position_mismatch', store.meta('position_mismatch'),
                  'state', store.meta('state'), 'stop_locks', store.meta('stop_locks'))
        finally:
            engine.close()
            market.close()
PY
```

该维护步骤仅为已有三次对账方法的调用单，无新产品 CLI 或特殊管理豁免；最终必须仍是 HALTED，且 daily_loss 原样保留。当前流水太多/结算不稳定导致十秒预算耗尽时，应停止操作并排查，不能改脚本跳过检查。

4. 解除权益 HALTED 或建立新归属起点。先复核程序空仓、无未知/待记账/恢复义务、无远端活动订单、无负债、保证金至少 3、正权益及新鲜完整证据。HALTED 服务须停止，`--confirm` 必须是刚才读取的真实 halt_reason；下面 `account_equity_drop` 只是示例，真实为 `total_loss` 等时按原值填写。非 HALTED 情况用 rebaseline，不用 release-halt。

```bash
# 分支 A：HALTED，先完成上述 mismatch 恢复，再精确确认真实原因。
python3 -m tools.crossex_exec release-halt --env-file crossex-exec.env \
  --confirm account_equity_drop --reason r12_reviewed_external_epoch
# 分支 B：未 HALTED、程序空仓、登记后需要新起点。A/B 只选适用的一条。
python3 -m tools.crossex_exec rebaseline --env-file crossex-exec.env \
  --confirm --reason r12_reviewed_external_epoch
```

成功会写 `HALT_RELEASED/EQUITY_REBASELINED`、保存原/新基线和完整外仓审计。release-halt 只移除原 `account_equity_drop/total_loss` 原因，**不清 daily_loss、不删 STOP、不启动服务**。不要传 `--confirm daily_loss` 试图清当日日亏，也不要 SQL 修改 `stop_locks/daily_loss_day`、删除 DB 或篡改系统时间。

5. **daily_loss 没有同日人工豁免 CLI。**登记、重基线、FX 变成 verified 或权益 HALTED 被解除，都必须保留原当日日亏锁；等待原 UTC 次日成功对账自然进入新日周期，UTC 00:00 为韩国时间 09:00。本轮没有新增“自动清旧锁”逻辑：源代码中原有 UTC 日界线规则不变；若新日 FX/流水核实失败，旧锁仍不清（专项已验证）。若停止时跨日，后续在已授权的原影子配置下启动，让新鲜完整对账处理日界线，不能手改基线日期。

```bash
date -u
# 仅在部署/服务启动已获授权、确认仍是计划中的影子配置时执行；不更改 live 开关。
sudo systemctl start crossex-exec.service
python3 -m tools.crossex_exec external-list --db var/crossex-exec.db
journalctl -u crossex-exec.service --since '10 minutes ago' --no-pager
```

验收以新账户观测对应的 `income.verified`、两家以上源时间 ≤60s、完整原币贡献/上界、`POSITION_MATCH_RECOVERED`、所有独立 stop_locks、负债告警及程序/账户全平区分为准；不以服务 active 或单个 `stop_reason` 消失判断准许开仓。实际解锁记录、备份路径/SHA、部署版本和线上证据由 Claude 回填后续交接，本轮不能预填成功。

### 交付状态与下一任务

本地 test/feat/docs 分组完成；工作区任务文件已显式纳入提交，起始无 WIP、没有删除其他任务文件。新增 Temp 红/绿/全量日志保留；本轮没有读取或复制旧账户数据库，没有新增真实账户数据入仓。原第十一轮副本不受本轮影响。

推送/线上数据库备份/部署版本/容器或服务/API 线上验收均未执行，未读取服务器 HEAD。真实行情可用性、账户流水权限与时延、外仓实际 USD 估值、Linux POSIX/flock/systemd 和持续影子运行仍未验收。源码本地通过不构成实盘授权。

下一模块请新建任务，由 Claude 在已授权的线上任务中按本节验证部署、公开 FX、账户证据和锁恢复；页面/ADMIN 转发仍属 live-exec-page。负债裁决已确定为“任何正负债停开并告警”，不再作为待实现豁免项。

## 第十三轮

### 范围、基线与本地提交

本轮模块为 crossex-exec 终审安全修复和远端持仓只读契约。先阅读第十一、十二轮及 [r12 只读终审](crossex-exec-r12-final-review-2026-11-12.md)，修复全部 5P1/1P2，并交付 `GET /api/v1/remote-positions`。页面登记/取消按钮实现仍交由 live-exec-page，不混入本工作树模块。

- 指定工作树 `C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8`，分支 `codex/crossex-exec-r8`，起始完整 HEAD `c7654f4a376e067031d57b8bfad80b59c61b2103`；起始工作区干净。
- test `052315a`：26 项专项先红，包含真实文件 SQLite、双线程竞态、流水连续性、USD 名义额、负债、停机、审计和 HTTP 只读接口。
- test `dac6afe`：停机审计故障先红后绿；补迁移/管理/失败快照验证，以及三个旧测试文件的严格证据夹具，原断言不变。
- test `0c4608b`：新增坏债务响应不能继续显示可开仓的先红后绿回归；最终共 31 项专项。
- feat `7159167`：六项终审修复及新只读接口；docs 为后续独立本地提交，准确 SHA 见下面命令及 [r13 验收 JSON](crossex-exec-r13-validation-2026-11-12.json)。文档自身 SHA 不写入自身内容。

```powershell
git log --oneline c7654f4..HEAD
git log -1 --format=%H -- tools/crossex_exec/engine.py
git log -1 --format=%H -- docs/task-handoff-2026-11-12-crossex-exec-r8.md
```

Git 按 test/feat/docs 分组，显式逐文件暂存；关闭本地 hooks/GPG。父仓库 worktree 索引不在可写根内，普通暂存返回 Permission denied 后，仅为已授权的本地提交使用自动审批扩展写权限；没有自动审批拒绝。不 push、SSH、WSL、amend、不做真实账户鉴权或订单，不改变线上版本，不做线上数据库备份或部署。不调用子代理。

### 六项终审修复与关键入口

| 问题 | 实现与验收行为 |
|---|---|
| P1-1 快照竞态 | `background.SnapshotCache` 使用 Store 同一 RLock，发布事务分配持久化 `snapshot_version`、保存最新完整快照并撤销旧证明。`ExternalAccounting._evaluate` 在提交事务内比较快照/权益/归属版本、原起点、原已核实历史和十秒新鲜度。`Engine.require_external_income/check_order/check_release` 要求证明与缓存同版本，最终发送重复检查。双线程 A 对账/B 发布单源报价反例被拒绝；同一时间戳和重启也不重用版本。 |
| P1-2 已核实流水消失 | `external_accounting.verified_history` 持续保存三类流水 ID 的时间和 SHA-256 内容摘要，覆盖窗口内缺失/改写即失败；旧索引保留，不通过滚动计量起点修补。同币种外仓 +5/程序 -5 消失不再把已知 -5 日损益变成零，文件库重开后仍拒绝改写。rebaseline/release-halt 也不能丢弃该证据。 |
| P1-3 非 USD 全户名义额 | `position_values.py` 保留精确身份和原币，`Engine.read_account_snapshot` 为登记外仓及快照内任意非 USD 持仓读取第十二轮 FX。`notionals` 保留原币，新增 `notionals_usd/notional_valuation_verified`；`HardLimits.exposure/check_order` 在当前时刻重新用上界折算远端、本地已成交与未决名义额，SENT 后共享关口也检查。500 USDC 在 p=1.01 时计 506.01 USD，加两腿 100 为 606.01，拒开。缺价/过期/来源不足拒开，减仓与硬退出仍可进行。 |
| P1-4 Bitget 债务 | `accounts.account_liabilities` 统一 CrossEx `assets[].liability` 与 UTA `assets[].debt`，保留原币及来源。任何正负债停开，管理前提拒绝；只有三份不同的新鲜无负债快照恢复该锁。缺字段、非法/负数/非有限金额或重复身份拒绝。新读取失败立即撤销缓存、置 risk=STOP 并触发重新对账。 |
| P1-5 停机结果 | `Engine.flatten_all` 先保存本次未核实状态，独立 attempt_id；shutdown 无登记也核实，忽略 HALTING 重试退避。读取/核实/审计失败不留成功状态。`__main__.finish_shutdown` 仅用本次返回的 verified + program_flat 判断退出 0，否则非 0；不读旧 flatten_status。 |
| P2 审计 old | `equity.BASELINE_KEYS/apply` 补齐旧 `external_equity_anchor/program_daily_upnl/program_last_upnl` 及历史索引、跟踪值、损益/证明；old 的读取与新基线、审计写入同事务。rebaseline 和 release-halt 都覆盖，审计失败原基线不变。 |

原 600 全户/150 单腿名义额、分所权益回撤、总亏损、保证金、正负债、日亏、STOP、永久禁单、目录核对、未知单/待记账和三快照恢复规则均保留。原 UTC 日界线规则不变；本轮无即时清旧日亏或负债豁免。

新增历史索引在升级时合并数据库仍保存的 anchor history 与最后 `external_activity`。它不能重建旧版本已经丢弃的历史证据；对真实旧库是否已发生旧版误计量，本轮没有账户或库证据，不能自动修正既有亏损。后续真实只读验收须核对原流水，不可删库或直接改基线绕过。

### 远端持仓接口与页面接续

完整字段、类型、失败码及页面按钮前提见 [第十三轮追加契约](live-exec-contract-2026-11-11.md#第十三轮增补2026-11-12)。入口为 `api.py` → `JournalView.get` → `remote_view.remote_view`；通过既有只读 SQLite authorizer、读取时限与大小预算，不导入交易引擎或调用适配器。

- 最近发布快照的所有非零远端行均可见，包含 program、external_registered、unregistered；完整保留 route/exchange_type/business/symbol/quote/dex 和方向，不按 ticker 合并。
- 返回原始数量、开仓价、标记价、原币浮盈亏和 USD 浮盈亏下界；盈利 × L、亏损 × H，USDT/USD 沿用原报告口径。与收益扣除使用上界是不同目的，不能互换。
- 无价格为 null、valuation_verified=false；账户过期与 FX 源过期独立报告。没有快照是 available=false，不冒充已核实空仓。刷新失败保留最后完整行并标记 stale，不返回部分失败响应冒充完整快照。
- 每行及顶层均有 snapshot_at/snapshot_version/stale。读取不会延长任何时间或重新估计源报价。除明确投影字段外，不暴露余额、user_id、原始流水、响应正文或秘密。
- Bitget 行仅供展示，原登记白名单未扩展。取消按钮须结合现有 external-positions 登记列表及本接口非过期的全量空仓证据；登记/取消的写前提仍是原 `expected_status=x1:...`，不能用快照版本替代，409 不自动重试。
- 数据覆盖以账户快照实际读取的现有产品范围为准：CrossEx futures 和 Bitget UTA USDT-FUTURES。没有另外扫描股票、现货或其他未订阅产品，不把“全远端行”表述成已查全交易所所有产品。

公开官方字段在本轮核实：[Bitget UTA debt](https://www.bitget.com/docs/catalog/account/assets-balance)、[UTA avgPrice/markPrice](https://www.bitget.com/docs/catalog/trading/position-management)、[CrossEx entry_price](https://www.gate.com/docs/developers/crossex/en/)。未发真实鉴权请求；所有测试账户、报价和本机令牌均为合成夹具。

### TDD、原断言与双版本验证

先执行新增 26 项专项，旧实现结果为 22 failures / 12 errors（含 subTest），再实现到全绿。后续停机审计写失败回归先得到 1 failure，修复后绿；最后追加坏债务响应的 can_open 回归，先得到 1 failure，再让失败刷新即时停开。另三个迁移/管理/快照保留检查用于验证已实现的不变量，不声称它们曾先红。红灯、绿灯、首轮失败及最终日志均保留 Temp，验收 JSON 记录指纹。

旧测试只更新三处夹具文件，无删改或放宽任何已有断言：`test_round2.py` 为 Bitget 成功响应补明确 debt=0 资产，并将抽象 external 名义额标签改为精确 USDT 合约；`test_round11_evidence.py` 给原 601 USD 边界提供两源显式 FX，选 p=1/1.002 使保守上界恰为 1，保留原 601 数值断言；`test_round12_fx.py` 的重启直接对账先经过正式快照发布，保留原跨日/收益断言。未使用生产“缺字段默认零”或“USDC 默认 1”兼容这些测试。

相对 c7654f4，AST 按原 owner/内容/次数核对全部旧断言：执行器 35 个 Python 测试文件、480 个 test_ 定义、1,685 个断言；fill-sim 18 个 Python 文件、146 个 test_ 定义、587 个断言。原函数/类定义全部保留；fill-sim 全部源码/测试与原模拟器文件保持原样。动态/继承测试使实际 discover 计数与定义数不同，未混用统计口径。

最终在两个解释器运行原两条命令，执行器包含全部真实文件库及 **5 项跨进程 SQLite 测试**，没有替换成内存库、skip 或过滤测试：

```powershell
python -m unittest discover -s tools/crossex_exec/tests -v
python -m unittest discover -s tools/fill_sim/tests -v
$py310 = 'C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/125141d8-f3d5-4518-98a0-6a4761963215/scratchpad/uvpy/cpython-3.10-windows-x86_64-none/python.exe'
& $py310 -m unittest discover -s tools/crossex_exec/tests -v
& $py310 -m unittest discover -s tools/fill_sim/tests -v
```

执行器最终 **511 项（480 + 31）**；fill-sim **158 项**。最终通过状态、耗时、Python/SQLite 版本、日志 SHA-256 见 [验收 JSON](crossex-exec-r13-validation-2026-11-12.json)。最后两行即时停开补丁后，执行器双版本再次完整重跑；fill-sim 从未修改，保留本轮已经通过的全量结果，不重复制造性能数字。

| 解释器 | 执行器最终 discover | fill-sim discover | 退出码 |
|---|---|---|---|
| Windows 3.12.10 / SQLite 3.49.1 | 511 项 / 131.689s / OK | 158 项 / 29.743s / OK | 全部 0 |
| 指定 Windows 3.10.20 / SQLite 3.50.4 | 511 项 / 138.545s / OK | 158 项 / 30.842s / OK | 全部 0 |

三种子 20261111/20261112/20261113 在两个解释器均实际执行，每种子 2,664=2,400+168+96 回合，N=1；I1a–d/I2–I7 checks 各 2,664、exercised 全部非零，原 96 次外仓检查和所有故障覆盖断言保留。六份最终种子输出写入验收 JSON。3.10 AST 检查 89 个执行器/fill-sim Python 文件，git diff --check 通过；接口契约对基线做 startswith 检查，只有追加。

本轮自审覆盖发布/核实/最终发送锁序，跨进程版本变化，流水迁移与管理失败回滚，非 USD 远端及待发送额度，缺失/非法负债，旧停机状态/审计失败，以及只读 API 完整性和秘密投影。没有重新声称 Linux、systemd 或稳态 CPU/RSS 验收；本轮新增快照/索引持久化成本仍需实际环境测量。

### 线上状态与下一任务

仅完成本地提交；未推送、未读取服务器 HEAD、未做线上备份、部署或真实 API 验收。没有读取/复制真实账户数据库。工作区起始无 WIP，仅显式纳入本轮文件；Temp 日志、合成测试副产物及原有文件未清理。

下一模块请新建 live-exec-page 任务，依据新增契约启用登记/取消按钮、保留 taoli1 会话/CSRF 和 ADMIN 转发边界；实际页面和线上端到端行为尚未验收。真实行情源、资金流水权限/分页/时延、历史证据迁移、Linux 文件锁/systemd、持续影子及发布必须在后续明确授权的任务中完成。本轮测试通过不授权实盘交易，也不自动解除旧损失锁。

## 第十四轮

### 目标、范围与本地提交

执行 [第十四轮任务书](crossex-exec-round14-2026-11-12.md)：程序与用户已登记外仓彻底隔离。此次风险归属变化来自用户明确指示和 PM 决定，覆盖旧轮次整户损失/额度口径；其余风险门槛不放宽。开工先读第十一至十三轮交接和契约。指定工作树为 `C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8`，分支 `codex/crossex-exec-r8`，起始 HEAD `c6f5884`，起始工作区干净。

| 类型 | 提交 | 内容 |
|---|---|---|
| test | `d1ac1c7` | 首批 17 项外仓隔离专项，修改实现前实际 13 failures |
| test | `f22f940` | 专项扩至 25 项、旧测试口径适配、原模拟器追加 48 回合 |
| feat | `ca9b15d` | 可核实程序损益、外仓债务分类、程序额度和持久化告警去重 |
| docs | 本节后续独立提交 | 追加契约、本交接、README、next-task 与验收 JSON；自身 SHA 由 git log 查询 |

只显式暂存本轮路径；本地提交关闭 hooks/GPG。首次普通 git add/commit 因父仓库 worktree 索引不在可写根内失败，之后仅为用户已授权的本地提交取得自动审批扩展权限；没有自动审批拒绝。无 push、SSH、WSL、amend、鉴权请求、真实订单、划转、借还款、线上备份或部署。没有调用子代理。

### 已完成行为与代码入口

1. **程序损失统一。** `external_accounting.py` 使用既有双快照、固定窗口全账本、现金/成交数量连续性和不可变历史索引，追加 `program_daily/program_total/program_funding_total/program_upnl/program_journal`。已实现和费用来自核对后的不可变程序订单；资金费累计通过起点 `program_funding_carry` 跨 UTC 日和重启保留；程序逐仓浮盈亏必须完整且汇总相符。`engine.py` 的 totals/reconcile/check_new_risk 和 `limits.py` 默认都消费同一份证明，整户权益差不再用于程序日亏、累计亏损或 150 回撤停机。
2. **证据不降级。** `program_risk.py` 在计算开始、提交及最终发送核对订单摘要，且要求当前账户快照版本、权益/归属版本、当前 UTC 日、十秒有效期和原 FX 时钟一致。零成交的新 INTENT/SENT 仍由原两腿共享屏障管理；任何已成交/终态记账变化撤销旧证明。缺费用、缺逐仓浮盈亏、现金不符、缺页、旧流水丢失/改写或旧库缺累计资金费起点均停开，不把未知当零。新账户发布总会撤销旧证明；默认无外仓时也走完整证明路径。
3. **出入金分离。** 仅明确 `TRANSFER_IN/TRANSFER_OUT`、空合约、正确金额符号的本金流，在完整流水及逐交易所/币种现金连续性通过后剔除；Bitget 还要求该转账 fee=0。未知类型、借还款/利息/换币、非零但无法归属的转账费用继续停开。旧 `equity_daily` 与外仓保守 FX 贡献仍保留作观测，不用于程序损失。外仓非零非 USD 收益的旧 FX 证明未删减。
4. **程序额度。** `HardLimits.exposure` 经 `program_notionals` 只排除已登记、目录和原始快照均可核实的外仓；程序远端/本地成交与 INTENT/SENT/ACK/UNKNOWN 剩余名义额继续计入 600。原单腿 150、默认单笔 50、配置更小值、资金费/成交额/深度/市场倍率、UNKNOWN 不重发、SENT 后共享检查不变。
5. **外仓债务。** `debt_attribution` 用精确 route/exchange/coin 分类；同域非零远端仓位必须全部是登记外仓、没有程序待发或未知单，逐仓原生 `initial_margin` 汇总须等于原资产 `futures_initial_margin`，原 liability 相符且不超出外仓保证金需求，才仅 `EXTERNAL_LIABILITY` 告警。Bitget debt、超额、混合归属、身份或金额缺证据仍停开。不跨币种净额抵销。阻断债务恢复仍需三份不同新鲜“无阻断债务”观测，重复读缓存不增加计数。
6. **唯一整户风险。** 两边维持保证金倍数仍低于 3 停开、低于 2 减程序仓位；收益证明失败也不能掩盖该保护。外仓永久禁单、原始 DEX 身份、只减程序仓位和 program_flat/account_flat 区分保留。未登记远端持仓仍 mismatch，恢复保留原三次匹配规则。
7. **告警。** `alerts.py` 按原因/owner 保存 `alert_states`，持续原因不随金额和时间刷新重复刷屏，小时提醒、状态变化/恢复后重发及重启去重都有测试；外仓告警状态与审计同事务提交。外部 sink 失败仍记录失败事件，不宣称已送达。阻断原因锁与只告警的外仓债务分开。
8. **管理与兼容。** `EquityAdmin` 默认无外仓也读取收益证据；允许可核实的外仓负债建立显式新起点，但无活动订单/程序空仓/无恢复义务/新鲜证据/正权益/保证金和确认前提保留，不清旧日亏锁。原专用账户测试显式使用 `CROSSEX_RISK_SCOPE=dedicated_account` 保持整户旧断言；默认 `program`，只要存在登记或墓碑则强制程序证明，不能用兼容配置绕过外仓核实。真实共享账户使用默认口径。

完整字段与优先级见 [第十四轮追加契约](live-exec-contract-2026-11-11.md#第十四轮增补2026-11-12)。原 summary 账户权益/回撤字段仍是整户观测，前端不能将它们展示成程序停机依据；程序风险展示读取 income 的新增字段并检查 verified/版本/时间。

### 用户明确改变机制的旧测试适配

本轮没有宣称“所有旧断言原文不变”。按 AST 的 assert 节点及属性 assert 调用、原 owner/次数审计，基线共有 2,401 个断言（执行器 1,816，fill-sim 585）；8 处原调用改变如下，其余原调用及全部原类/函数定义保留。原测试文件现在同口径 2,404 个断言。完整旧调用文本见 [验收 JSON](crossex-exec-r14-validation-2026-11-12.json) 的 `tests.explicitly_changed_assertions`。

| 文件 / 用例 | 适配及依据 |
|---|---|
| `test_round11_external.AccountingTests.test_profit_loss_both_sides_excluded_from_daily_but_total_preserved` | 1 处：原累计损益不得高于整户权益差，改为外仓盈亏变化时程序累计为 0。保留程序日亏为 0、无 mismatch 和程序投影空仓断言。新的程序资金费/费用/已实现损失测试确认自身损失仍计入。 |
| 同文件 `test_equity_and_margin_hard_limits_include_external_catastrophe` | 保留 HALTED 和 account_flat=false 原断言，夹具新增可核实的程序 -150 资金费及对应资金余额。1 处原“订单列表为空”改为“无减仓订单”，因为合成程序所有权订单必须存在；新增 I8/专项检查外仓从不发送或改变。 |
| `test_round12_fx.FXAccountingTests.test_external_profit_upper_and_loss_lower_never_hide_own_loss` | 3 处：只由整户权益与 FX 差额推断的 -20.1 不再作为程序日亏，改为程序日亏 0、无 daily_loss、可开。旧外仓贡献、旧 equity_daily=-20.1 与空订单断言保留，明确它们现在只是观测。新增真实程序已实现/费用/资金费总计 -20 的日亏锁测试，以及外仓盈利不能抵销程序 -60/-101/-150 的测试。 |
| 同文件 `test_missing_public_provider_never_blocks_full_account_protection` | 2 处：外仓亏损且 FX 不可核实的场景，改为停开而非仅凭整户权益 HALTED；不再要求不存在的 flatten_status.account_flat。新增保证金 1.5 时仍调用 flatten 的断言，保留无外仓发送。 |
| `test_round13_review.NotionalTests.test_external_500_usdc_plus_two_50_usd_legs_exceeds_hard_600` | 1 处身份键变化：用未登记的 `xyz:OAI_USDC` 构造原 506.01+100=606.01 的超限场景；金额和拒开断言不变，未按同名 io 合约豁免。新增 60000 外仓不占程序额度，而程序 600+新增订单仍拒绝的隔离测试。 |

其余夹具修改没有改变旧断言：

- `test_engine/test_round3/test_round4/test_round5/test_round8_completion/test_round10_baseline/test_api` 的原专用账户 Engine 构造显式配置 `dedicated_account`；`test_round2` 的 HardLimits 和较小上限构造同样显式配置。阈值、拒绝、发送屏障与 UNKNOWN 断言未改。
- `test_invariants.World.make_engine` 原无外仓世界显式专用账户口径；`round11_simulator.enable` 登记后自动切换强制程序证明。原随机种子、前 2,400 随机回合、原 168 定向回合及 96 外仓回合保留，所有原 I1a–d/I2–I7 和原覆盖断言保留；追加 48 回合及 I8/I9 检查。
- `test_round11_evidence.test_external_notional_liability_and_margin_keep_original_limits` 将 601 名义额放入未登记的精确 `BINANCE_FUTURE_ETH_USDC`，保留显式两源 FX、601 超限、无法归属负债停开与保证金减仓原断言。
- `test_round13_review` 两个 FX/时效用例改用未登记精确合约，保留缺价/到期拒开和减仓可用性；`real_accounts` 合成夹具提供完整空账本，使无外仓默认程序路径也具备可核实证据，坏 debt 响应仍立即撤销可开状态。

未修改任何 fill-sim 源码或测试，基线下全部 51 个跟踪文件按统一换行后逐文件相同。没有 skip、过滤原测试、降低金额阈值或更改原故障随机序列制造通过。

### TDD、最终验收与证据

首批 17 项实际先得到 13 failures 后实现到绿。后续三个自审缺口均补反例后先红再修：默认无外仓时逐仓浮盈亏覆盖缺失；损益计算过程中订单变化可能把旧结果绑定新摘要；外仓告警审计和状态非原子提交。另 5 项为补充边界验证，没有声称它们在实现前失败。25 项专项最终通过。

首轮完整执行器 528 项有 3 个模拟器失败，均是新增失败保护分支提前消费 HALTING 重试，使原 I7 本 tick 重试次数断言失败；修正调用时机，保留原 I7，不改阈值或重试断言。第二轮 530 项通过后，补齐上述竞态/原子性及边界测试，再在 feat `ca9b15d` 上用两个解释器完整重跑：

| 解释器 | 执行器 unittest discover | fill-sim unittest discover | 退出码 |
|---|---|---|---|
| Windows 3.12.10 / SQLite 3.49.1 | **536 项 / 153.737s / OK** | **158 项 / 33.020s / OK** | 全部 0 |
| 指定 Windows 3.10.20 / SQLite 3.50.4 | **536 项 / 164.745s / OK** | **158 项 / 34.202s / OK** | 全部 0 |

```powershell
python -m unittest discover -s tools/crossex_exec/tests -v
python -m unittest discover -s tools/fill_sim/tests -v
$py310 = 'C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/125141d8-f3d5-4518-98a0-6a4761963215/scratchpad/uvpy/cpython-3.10-windows-x86_64-none/python.exe'
& $py310 -m unittest discover -s tools/crossex_exec/tests -v
& $py310 -m unittest discover -s tools/fill_sim/tests -v
```

fill-sim 双版本在本轮已完整运行，此后只改执行器专项/证明绑定和告警事务，其源码测试始终未变；不重复运行以制造不同性能数字。所有执行器文件库和跨进程测试均包含在 discover 中。

两个版本均实际运行种子 **20261111 / 20261112 / 20261113**，每种子 **2,712 = 2,400 + 168 + 96 + 48** 回合、N=1。原十项不变量 checks 各 2,712，exercised 均非零；原外仓检查各 96。每种子新增检查 48 次，外仓剧烈盈亏变化 4 次、入金/出金各 2 次；40 次明确检查保证金充足且程序自身无损失时仍可开、程序日亏/累计为零，后续收尾只平程序并保留外仓。

[验收 JSON](crossex-exec-r14-validation-2026-11-12.json) 保存四份全量日志的路径、SHA-256、六份模拟器完整计数、四份有效红灯日志、旧断言逐项差异和源码指纹。日志保留 `%TEMP%/crossex-r14-*.log`，全部为离线合成数据；首个“缺逐仓浮盈亏”尝试曾被夹具现金差异提前挡住，未将该次通过误报成红灯，后续修正夹具得到独立失败反例。

93 个执行器/fill-sim Python 文件通过指定 3.10 AST 解析。`git diff --check` 通过；契约相对 c6f5884 的规范化文本 startswith 检查通过，只追加。自审覆盖证据读取/计算/事务/发送的版本绑定、跨日资金费进位、外仓负债域/额度、持久化去重、未知资金流和失败不遮蔽保证金保护；没有重新声称 CPU/RSS、Linux 或真实账户验收。

### 线上状态、遗留边界与下一任务

本轮只有本地 test/feat/docs 提交，未推送、未读取线上 HEAD、未备份/部署、未鉴权或触碰真实账户数据库。代码提交后工作区干净，随后新增的文档和验收 JSON 仅属本任务；最终 docs 提交后再检查状态。未清理原有文件、Python 缓存或 Temp 日志。

升级旧库不能默认累计资金费为零：缺 `program_funding_carry` 或已有程序历史却缺新起点时会停开，需原有带审计的程序空仓 rebaseline/release-halt；不自动清旧 daily_loss。真实资产是否包含可核实的逐仓/资产初始保证金、账本是否及时完整、外仓借款相关现金流能否持续证明、公开 FX 是否新鲜，仍需后续明确授权只读核对。本轮不把字段名字匹配当真实账户验收；证据不齐的分支明确停开。

默认无外仓也增加了完整流水核实，因此旧分页上限、十秒账户采样预算和全窗口读取成本仍需真实影子环境测量。窗口过长/数据过多会保守停开，未扩大预算或用部分数据放行。Windows 双版本结果不替代 Linux/systemd、实际网络/资金费和持续影子验收。

本模块本地验收结束。页面展示/按钮、线上只读核对与发布请分别在新任务继续，先读本节及第十四轮契约；共享账户不要沿用旧“整户跌 150 即程序停机”文案，也不要把外仓只告警扩展为未知负债豁免。后续发布必须另获授权，本次测试通过不是实盘开仓授权。

## 第十五轮

### 范围、基线和本地提交

从终审报告提交 `65747b2` 开始，工作树 `C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8`、分支 `codex/crossex-exec-r8`，起始工作区干净。先读第十三、十四轮交接、接口契约及 [r14 终审](crossex-exec-r14-final-review-2026-11-12.md)，修复全部 4P1/1P2，并定位和修复 HL DEX 外仓登记 409。范围仅执行器及其离线测试，不包含页面或部署。

| 类型 | 提交 | 内容 |
|---|---|---|
| test | `075b03a` | 首批身份、HL DUAL、HALTING、明确拒单反例 |
| test | `6f4fddf` | 首次资金费缺失、生命周期、独立历史接口反例 |
| test | `c81fd32` | 默认 program 开平仓/拒单/流水延迟/重启模拟器，旧夹具补独立证据及并发边界 |
| test | `42d339f` | 本机小数秒与整数毫秒窗口边界反例 |
| feat | `e8c77ba` | 本轮完整实现，完整 SHA `e8c77ba8cf2ed851db6d62d198da86490a268186` |
| docs | 本节后续独立提交 | 契约追加、第十五轮交接、下一任务入口、README、验收 JSON；用 git log 查询自身 SHA |

只显式暂存本任务文件；Git hooks/GPG 在本地提交命令中关闭。普通 git add 因父仓库 worktree 索引不在可写根内失败，随后仅为已授权本地提交使用自动审批扩展权限，没有自动审批拒绝。没有 push、SSH、WSL、amend、真实鉴权/订单、线上备份或部署；没有调用子代理，实施后按锁序、证据和恢复路径自审。

### 五项修复和代码入口

1. **首次资金费缺失。** 新 `funding.py` 在原现金连续性之外，按精确合约/计价币核对当前与完整关闭仓位的累计资金费变化。CrossEx 使用 funding_fee、position_id、create_time；UTA 使用 totalFunding、createdTime。`adapters.py` / `accounts.activity` 增加两个只读历史仓位请求，固定窗口、有界页数、重复响应拒绝。`external_accounting` 保存 funding_positions 起点和 funding_verified 证明；`program_risk.require_program_proof` 要求此证明。已平仓位缺历史、原生命周期消失/冲突、本程序期间已成交但无生命周期证据、缺累计值或账本不符都停开。`+25/-25` 首次同时漏报不再被现金净变化零掩盖，补齐后计入程序 -25。UTC 滚日和重启保留累计证据；真正的小数秒时钟使用固定整数毫秒窗口边界。
2. **严格身份共享。** `position_values.position_identity/verify_external_rows` 共用于登记和风险投影，展示使用同一解析器。任何显式 exchange_type/business_type/tag/dex/quote/route 冲突均不能排除仓位或豁免负债；对应 UI 行为 identity_verified=false、unregistered。身份字段在官方响应中省略时仍由精确原始 symbol 和唯一有效目录相互核对。负债金额、同域混合仓位、保证金和 Bitget debt 原门槛未变。
3. **HALTING 后台。** `Engine.startup` 在 HALTING 正常读取、读取失败及非本程序 Bitget 活动订单分支都保留后台线程，原账户、目录和采样周期继续运行；主协调器独立执行原有减仓重试。实际线程阻塞测试确认 tick 仍进入 flatten；目录过期恢复后可核实程序已平，外仓保留。HALTING/HALTED 和 halt_reason 不因刷新被释放，正常启动仍拒绝非本程序订单。
4. **明确拒单记账与旧记录恢复。** `execution.py/rejection.py` 在明确 placement rejection 的 REJECTED 事件中一次保存完整零成交字段和摘要。旧缺 fee 的同类记录由 recover 校验完整 SENT→REJECTED 审计后事务修复，保留原事件、追加 REJECTION_ACCOUNTING_RECOVERED。UNKNOWN、ACK/终态证据冲突、正成交、订单身份改变或缺审计均不能套用。测试覆盖文件库重开、拒单率独立限制、事务失败回滚和幂等，不通过 rebaseline 猜 fee=0。
5. **默认模式覆盖。** 新 `round15_simulator.py/test_round15_program.py` 通过真实 Engine/Executor、合成 venue 和独立累计资金费状态实际发送/成交/减仓；包含文件 SQLite 重启、单腿明确拒单后的另一腿收尾、资金流水/关闭仓位历史延迟、UTA 缺字段/身份冲突。默认 engine_config={}，明确断言 program_scope，未禁用证据门禁。旧 dedicated_account 模拟器的原种子、调度及断言全部保留。

单腿 150、程序总额 600、日亏 20、累计亏损 100、程序回撤 150，以及更严配置均未改；保证金 <3 停开/<2 减程序、UNKNOWN 不重发、外仓永久禁单、旧损失锁、三份新鲜观测恢复和最终发送检查仍在。

### 线上副本诊断：HL DEX 登记 409

用户报告影子运行 `679791a`。仅复制所给 `crossex-exec-20261112-1725.db` 到本机 Temp，并以 `mode=ro&immutable=1` 读取；`PRAGMA quick_check=ok`，原件和复制件 SHA-256 均为 `945421b8a2881e0c8b0e4a2ab65baa4b3fbd5c317e773bab9f25f3dd2e708c2c`。没有直接写该库，也没有把整库、账户标识、余额或密钥带入仓库。

- external_catalog 有 12,863 条规则，目标 `HYPERLIQUID_FUTURE_io:OAI_USDC` 恰好一条；HYPERLIQUID / FUTURE / tag=io / live / support_cross=true / delist_time=0 均匹配。
- 目标规则 lot_size/min_size/tick_size 均为 0.001，**max_limit_size 与 max_market_size 为 null**。原 `ExternalRegistry.rule` 对 max_limit_size 强制 dec()>0，故目录存在仍返回 external_identity_unverifiable。
- raw_positions 与归一化 positions 一致：同合约 **long 8.107、short 13.196**，两个方向独立。没有发现目录缺失或双向净额合并；BINANCE_FUTURE_OPENAI_USDT 的 max_limit_size=4000，因此该错误条件不触发。
- 修复只允许外仓身份目录保留显式 null 的订单上限；缺字段、非法非空数值及其他原严格规则继续拒绝。没有给未知上限填默认无限值，没有改变交易目录或下单定量规则。
- 最小夹具仅使用该精确 symbol、身份/规则字段和两方向数量，回归通过登记、双方向排除、双方向/减仓永久禁单及有仓不能取消。其他 DEX、错误 exchange/business/tag、重复目录、未知规则和程序所有权原回归继续通过。

这证明本次已找到并修复该副本历史状态中的登记校验原因；没有调用当前线上 ADMIN 接口，不能表述为已替用户完成线上登记、已解除停机或已恢复交易。

### TDD、原断言和最终双版本验证

首批 10 项在旧实现实际得到 19 failures / 2 errors（含 subTest）；资金费首批 9 项在资金费实现之前实际得到 10 failures / 2 errors。后续非本程序活动订单 HALTING 分支和小数秒边界也各先红再修。默认 program 5 项回归另在 Temp 的隔离 `65747b2` 源码中回放，实际 4 failures / 1 error；这是基线回放证据，不冒称它发生在首批实现之前。最终新增 29 项定义。

旧测试只有证据夹具和模拟器配置注入调整，原断言不删改、不放宽：

- `test_round11_external.py` / 新 `funding_fixtures.py`：由旧用例的完整合成场景事实生成当前/关闭仓位累计值，再选择可见账本窗口；生产代码没有补零回退。第十五轮缺失/延迟用例使用另外独立维护的累计值，不从返回的账本生成证明。
- `round11_simulator.py`：给原场景补当前/已关闭生命周期和空资金费证据。
- `test_round13_review.py`：给原真实 Accounts 的空仓夹具补两份明确空历史响应。
- `test_invariants.py`：增加可选 engine_config 注入，原默认仍是 dedicated_account；新增 ProgramWorld 才使用空配置触发真实默认 program。

相对 `65747b2` 按 AST owner、内容及次数核对执行器/fill-sim 共 46 个原 Python 测试/夹具文件：**2,491 个原断言内容和次数未删改，1,212 个原类/函数入口均保留**；上述夹具函数体有补充证据的改动。fill-sim 源码和测试未改。没有把文件库换成内存库，没有过滤或跳过原 discover 测试。

在 feat `e8c77ba` 上最终结果：

| 解释器 | 执行器 unittest discover | fill-sim unittest discover | 退出码 |
|---|---|---|---|
| Windows Python 3.12.10 / SQLite 3.49.1 | **565 项 / 134.794s / OK** | **158 项 / 29.289s / OK** | 全部 0 |
| 指定 Windows Python 3.10.20 / SQLite 3.50.4 | **565 项 / 148.218s / OK** | **158 项 / 31.540s / OK** | 全部 0 |

```powershell
python -m unittest discover -s tools/crossex_exec/tests -v
python -m unittest discover -s tools/fill_sim/tests -v
$py310 = 'C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/125141d8-f3d5-4518-98a0-6a4761963215/scratchpad/uvpy/cpython-3.10-windows-x86_64-none/python.exe'
& $py310 -m unittest discover -s tools/crossex_exec/tests -v
& $py310 -m unittest discover -s tools/fill_sim/tests -v
```

每个解释器都完整执行原 **5 项跨进程 SQLite 测试**。旧模拟器种子 20261111/20261112/20261113 各 **2,712 回合**、N=1；原十项不变量 checks 各 2,712、exercised 均非零，外仓原 96 次和第十四轮 48 次检查均保留。新增默认 program 模拟器每种子 **12 个场景**，包括 8 次实际开平仓、4 次明确拒单、8 次资金费延迟、20 次重启和 40 次发送不变量检查；两版三个种子均通过。两版各 100 个 Python 文件语法解析通过，git diff --check 通过。

完整计数、两个模拟器共 12 份种子记录、跨进程测试名、日志路径及 SHA-256 见 [第十五轮验收 JSON](crossex-exec-r15-validation-2026-11-12.json)。3.12 fill-sim 在本轮较早已完整通过，之后只改执行器及其测试，未重复运行未变化的 fill-sim；其日志和指纹仍为本轮实际结果。契约对基线规范化 startswith 检查，只有追加。

### 交付边界与下一步

本轮只完成本地分组提交，未推送；未核实当前线上 HEAD，未做线上备份、部署或真实 API 验收。用户报告的影子版本仍只作为所给事实记录，未被本轮修改。Temp 保存副本、隔离基线、红绿灯日志和测试产物；保留原缓存和其他产物，不清理无法确认归属的文件。本轮最终仓库应只包含上述提交，不带未跟踪整库。

旧 external_equity_anchor 缺 funding_positions 时不会直接恢复开仓；需要按原人工管理路径在程序空仓、无恢复义务且新证据齐全后建立明确新起点，不能自动填旧资金费零。新历史接口没有实测鉴权权限、交易所累计值在部分/完整平仓时的真实语义、响应时延、分页量和十秒采样预算；这些未验证条件会继续停开，不能通过缩小请求窗口、忽略缺页或删库绕过。Windows 双版本离线结果不替代 Linux/systemd 或真钱验收。

本模块本地交付结束。后续请在新任务继续线上只读/持续影子验收与独立终审，先读本节及[第十五轮追加契约](live-exec-contract-2026-11-11.md#第十五轮增补2026-11-12)。页面登记按钮仍使用原 ADMIN 请求和 x1 expected_status，不需要新权限或自动重试。本次未授权推送、部署、解除旧 halt_reason 或开真钱交易。

## 第十六轮

### 范围、基线和本地提交

指定 worktree `C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8`、分支 `codex/crossex-exec-r8`，起始 HEAD 为第十五轮终审报告提交 `b61662d`，起始工作区干净。先读 [r15 终审](crossex-exec-r15-final-review-2026-11-12.md)、第十五轮交接和契约。本轮仅处理：A 跨日相反资金费首次漏报 P1；B 大流水历史读取及诊断；C 已登记外仓下停机退出和分类报告。

| 类型 | 提交 | 内容 |
|---|---|---|
| test | `c151116` | 首批跨日、分页、失败诊断及真实停机函数回归 |
| test | `bd18407` | 扩至 24 项专项，补事务/迁移/权限/大账本/三仓停机及旧证据夹具修正 |
| feat | `f9c88e6` | UTC 独立检查点、完整历史分页、诊断与停机报告 |
| docs | 本节后续独立提交 | 契约仅追加、第十六轮交接、README、下一任务及验收 JSON；自身 SHA 用 git log 查询 |

只暂存本轮列明的文件，本地提交关闭 hooks/GPG。普通 git add 因 worktree 索引位于父仓库的只读范围失败后，仅对已授权本地提交使用自动审批扩展权限；没有自动审批拒绝。无 push、SSH、WSL、amend、真实鉴权/订单、线上备份或部署。没有调用子代理；完成后自行审查证据、事务、发送门禁和安全诊断路径。

### A：跨日资金费 P1

入口为 `funding.py:funding_positions/daily_continuity`、`external_accounting.py:_evaluate`、`program_risk.py:require_program_proof`、`equity.py:read_flat_accounts/apply`。

- 保留生命周期累计值与账本核对，再加入独立收取时间和持久化 `funding_checkpoint`。每个生命周期先确定累计增量只能落在哪个 UTC 日，再按精确 route/symbol/quote/day 合计与新增流水比较；不能先跨日净额抵销。
- CrossEx 当前 `funding_time=0` 按官方语义证明未收取，非零值作为收取时间上界；非法/未来/倒退/与累计值冲突均拒绝。午夜后采到“最新收取仍在前一日”的仓位，可证明该日界累计值。完整关闭仓位及 UTA 无同等时钟时，使用创建/关闭/观测边界，不能证明单日归属就停开。
- 成功 income 新增 `funding_daily_verified=true`，最终开仓和发送门禁要求此字段。检查点、日界起点、历史和收益证明同事务提交，比较原检查点及原有快照/权益/归属/订单摘要版本；失败及重启不丢旧区间。显式 rebaseline 也先验证已有区间，检查点纳入旧/新审计和回滚。
- 回归复现同合约前日 +25、当日 -25、间隔八小时首次都漏报：原实现放行，新实现停开，不滚动起点；文件重开及补一条/两条流水都不能伪造日界证据。独立观察到前日累计 +25 的日界后，当日 -25 迟到再补齐可精确核实 daily=-25、total=0，实际 Engine 触发原日亏 20 停开锁。
- 不同生命周期各自被创建/关闭时间限制在单日的对照组，可在跨日补齐流水后核实 daily=-25；同日相反结算允许净额核对。UTA 跨日净额为零、跨日关闭但缺收取时钟、检查点落库失败均继续停开。

**保留的可用性限制：** 若一个有结算的生命周期跨多日且没有独立日界观察，完整流水回来也不足以认证原缺口，必须继续停开；未增加可人工填写金额的证据接口或绕过重基线条件。不能把此限制描述为“所有跨日账户都会自动恢复”。原单腿 150、总额 600、日亏 20、累计亏损 100、程序回撤 150 及更严配置、整户保证金 <3/<2、外仓禁单和 UNKNOWN 不重发均保留。

### B：18:15 线上库副本与历史读取

用户报告影子 `c85ff7c`，三笔仓位均登记、mismatch 自动解除、外仓负债仅告警。源文件为用户给定的 `crossex-exec-20261112-1815.db`，只复制到本机 Temp，以 `mode=ro&immutable=1` 查询复制件。`PRAGMA quick_check=ok`，源文件及复制件 SHA-256 为 `326bdf7c69572487b59e7a3d87c2f08b80de06a2fc71b2f59318dadbd9d347d8`，结束前再次核对源指纹未变。未将整库、用户 ID、账户余额或密钥写入仓库。

**已证明：**

1. orders=0、pairs=0，当前两份合约登记覆盖三笔远端持仓。失败 activity 只有 `from_ms=1794441600000/to_ms=1794478452271/complete=false`。源代码只有读取异常分支会将 activity 压缩成这三个字段；双快照变化分支会保留已经读取的历史列表。因此可定位到读取异常路径。
2. 库中没有原始异常、页号、响应 code 或接口名，事件只有通用 external_income_unverifiable；**不能从本副本唯一判定当时是 20 页截断、游标、响应形状、权限还是限频**。没有调用真实鉴权接口进行反推。
3. 库中没有 `external_equity_anchor`。即便历史读取恢复完整，仍须满足原显式、带审计的程序空仓建基线前提，不能自动把当前账户损益归零。未知现金流、未登记历史交易、借还款/利息等仍可能独立阻断。

**已复现并修复的代码缺陷：**

- CrossEx 三个历史接口和 Bitget 两个历史接口原固定 20 页用尽后只能报通用 invalid_response；新 `history.py` 读取到明确末页，保留有界请求/行数/时间预算，达到预算即停开。
- Bitget financial-records 原遇 `len(list)<100` 就结束，未消费非空 cursor；现在短页有 cursor 仍续读，重复/缺失/非法 cursor、异常空页和重复 ID 均有明确拒绝原因。
- 两个 UTA 历史接口原窗口超过 30 天直接拒绝，现在对完整区间分段，各段最大 30 天，闭区间边界不丢毫秒、不重复计数；跨段 ID 仍唯一。没有只拉最近部分来逃避历史核实。
- `Accounts.activity` / `read_snapshot` 保存准确 endpoint、page、子窗口、已读行数、cursor_used、condition、error_kind 和白名单 HTTP/code；后续活动订单/账户读取、双快照变化、跨日及过期有固定 stage/condition。`ExternalAccounting.evaluate` 另提供安全的证据失败原因。
- Engine 将诊断写入 external_failed_snapshot、失败 income 和 ALERT 审计；CLI 服务 alert 输出带相同诊断。去重比较接口/页/条件/错误码，不因时间窗口或行数刷新刷屏；无响应正文、异常正文、账户 ID、签名或原始 cursor。
- 21001 行固定 CrossEx 账本完整经过真实适配器、现金/归属/资金费核验，income.verified=true；2100 条 UTA 记录超过 20 页仍完整读取。权限错误经 localhost 真 Transport 测试保留安全 code，不输出模拟秘密文本。

每接口预算为 1024 请求、250000 行、8 秒；账户整体原十秒新鲜度和原传输限频保持。大量流水超过实际网络/时间预算、第三方在双快照期间持续交易或交易所权限不足，仍会停开并记录具体原因。本轮证明离线大账本路径成立，**没有证明线上当前 complete 已变为 true，也没有证明历史真实故障必然是分页上限**。

仅查官方公开文档：[CrossEx 账本/成交/历史仓位与收取时间](https://www.gate.com/docs/developers/crossex/)、[Bitget 财务流水窗口/游标](https://www.bitget.com/legacy-docs/uta/account/Get-Financial-Records)、[Bitget 仓位历史权限](https://www.bitget.com/docs/catalog/trading/position-management)。后者仍标注 UTA trade read & write；本地代码只发 GET，没有实测或扩大账户权限。

### C：停机退出的实际证据

副本中的 shutdown 发生于 **2026-11-12 10:12:45.367617 UTC**，当时 flatten_status.verified=true、program_flat=false，external_positions 只有 BINANCE_FUTURE_OPENAI_USDT 一笔。HL `HYPERLIQUID_FUTURE_io:OAI_USDC` 两个方向对应的合约登记事件在 **10:13:19.959049 UTC**，晚 **34.591432 秒**。当时存在尚未登记仓位，旧退出 1 符合约定；不能把这一历史 flatten_status 当成登记齐全之后的新 stop 结果。

旧代码本已在“程序已平、全户有已登记外仓”时退出 0；首批实测正向用例在修改前即通过，本轮没有假称修复了可复现的错误退出分支。新增 2/3 笔外仓、收益不可核实、之前失败停机、未登记仓位、内存未决订单回归，调用真实 flatten_all/finish_shutdown，确认已登记外仓不触发残余程序敞口，外仓从不被发送订单。

实现补全可观测性：成功及有返回状态的失败均输出 `shutdown_positions`，增加 remaining_positions/local_active_pairs；失败仍输出 shutdown_residual_exposure。新鲜原始仓位、身份目录、本地活动 pair/未决订单/待记账/待写条件保持。登记齐全且程序空仓返回 0，并明确 program_flat=true/account_flat=false 和外仓快照；未登记或程序未平仍非 0。Linux 的 systemctl 和真实信号退出本轮未执行。

### TDD、原断言、双版本全量结果

首批 15 项在旧实现先运行，原日志记录 11 failures/8 errors（含 subTest；一项次生断言上下文错误随后修正，事件 JSON 的新测试读取方式也纠正）。真正的跨日漏报反例明确显示原实现未抛异常并滚动起点；分页上限/30 天窗口、短页 cursor 和缺诊断分别有独立红灯。后续“检查点需进入管理审计”和“旧终结记录缺可选时钟”各在相应实现前单独跑红。没有将实现后的补充边界测试冒称为首次红灯。

首轮完整执行器 580 项有 1 failure/1 error，来自旧合成夹具：用采样时刻当最终关闭时刻，以及新增结算时改写已经终结的生命周期。修正 `funding_fixtures.py`，从完整合成场景事实提供收取时间，以最后成交/结算时刻关闭，终结记录保持不可变，终结之后的独立合成结算使用另一生命周期。`round15_simulator.py` 由 venue 自己的 settle 操作记录收取时刻，和被延迟的账本响应分离。未让生产代码补零、忽略历史改写或跳过日界门禁。相关 85 项随后通过。

相对 `b61662d`，按 AST owner/内容/次数检查执行器及 fill-sim 的 **51 个原测试/夹具文件、2631 个原断言、1281 个类/函数入口**，均保留；没有删改/放宽原断言，没有 skip、过滤 discover 或内存库替代文件库。fill-sim 源码和测试未修改。新增专项共 24 项。

在 feat `f9c88e6` 上最终完整运行：

| 解释器 | 执行器 unittest discover | fill-sim unittest discover | 退出码 |
|---|---|---|---|
| Windows Python 3.12.10 / SQLite 3.49.1 | **589 项 / 175.579s / OK** | **158 项 / 30.301s / OK** | 全部 0 |
| 指定 Windows Python 3.10.20 / SQLite 3.50.4 | **589 项 / 186.784s / OK** | **158 项 / 32.030s / OK** | 全部 0 |

```powershell
python -m unittest discover -s tools/crossex_exec/tests -v
python -m unittest discover -s tools/fill_sim/tests -v
$py310 = 'C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/125141d8-f3d5-4518-98a0-6a4761963215/scratchpad/uvpy/cpython-3.10-windows-x86_64-none/python.exe'
& $py310 -m unittest discover -s tools/crossex_exec/tests -v
& $py310 -m unittest discover -s tools/fill_sim/tests -v
```

两版都包含原 **5 项真实文件 SQLite 跨进程测试**。旧模拟器三个种子 20261111/20261112/20261113 每版各 **2712 回合**，十项原不变量 checks 各 2712、exercised 全部非零；外仓原 96 次和隔离 48 次检查保留。默认 program 模拟器每版三个种子各 **12 场景**，每种子包含 8 次正常开平仓、4 次明确拒单、8 次资金费延迟、20 次重启、40 次发送检查，全过。

两版各 **103 个 Python 文件**通过 AST 解析；git diff --check 通过，契约与基线规范化 startswith 检查通过，只追加。[第十六轮验收 JSON](crossex-exec-r16-validation-2026-11-12.json)保存四份全量日志路径/SHA-256、两套模拟器 12 份种子记录、跨进程测试名、红灯/首轮失败日志、源文件指纹及审计。双解释器任务并发运行，耗时不是性能验收；未重新声称 CPU/RSS、真实网络或 Linux/systemd 验收。

### 线上状态、文件与下一任务

本轮只有本地 test/feat/docs 提交；未推送、未读取当前线上 HEAD、未线上备份或部署、未调用真实鉴权接口，影子版本仍仅按用户报告记录为 c85ff7c。Temp 保留只读副本、全部红绿灯日志和测试产物；没有清理原有缓存/产物或更改其他模块。最终 docs 提交只纳入本轮五个文档路径，不包含数据库或账号数据。

下一任务先进行独立终审，再在明确授权的只读/影子任务中收集新 activity.failure，确认究竟哪个接口/页/条件阻断；若历史完整但缺起点，按原 ADMIN 及程序空仓流程显式建基线。不能删库、缩短遗留窗口、伪造 settled_ms、自动重基线或放宽时效来恢复开仓。真实权限、跨日收取时钟的线上语义、持续第三方交易时稳定窗口及大流水耗时仍需验证。发布/实盘授权、页面工作和运维分别新建任务继续。

## 第十七轮

### 范围、基线与提交

指定 worktree `C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8`，分支 `codex/crossex-exec-r8`，起始 HEAD `c93183a`，工作区干净。先读 [r16 终审](crossex-exec-r16-final-review-2026-11-12.md) 的 1P1 及第十六轮交接。本轮范围是起点分日证明来源、Bitget 空财务页和账户历史相关 CPU/RSS；没有页面、交易策略或线上运维改动。

| 类型 | 提交 | 内容 |
|---|---|---|
| test | `491f3e5` | r15 错误滚日起点、升级/备份、空页/游标回归与离线资源负载 |
| test | `db558d9` | 历史索引重复计算、签名排序、流水消失/改写与失败回归 |
| test | `45dcb68` | JSON 类型变更与摘要重复树反例 |
| test | `8c9795f` | 长文本内存边界、未变历史写入、真实 r15 funding 实现与签名环负载 |
| feat | `40fe7f2` | 完整 SHA `40fe7f2f1edffde17d3741e46b4d5807305091b8`；证明来源、空页及资源修复 |
| docs | 本节所在独立提交 | 契约只追加、交接、README、下一任务、验收 JSON；自身 SHA 用 git log 查询 |

普通 git add 因索引位于父仓库只读范围失败，之后只为已授权的本地提交使用扩展权限；无自动审批拒绝。显式暂存任务路径，提交关闭 hooks/GPG。没有 push、SSH、WSL、amend、真实鉴权、线上备份/部署、真实订单或子代理调用。实施后自行审查恢复、事务、发送约束、缓存及脱敏；不把自审称为独立终审。

### A：旧净额起点不得自动成为分日证明

入口：`funding.py:certify_daily/require_daily_proof/daily_continuity`、`external_accounting.py:_evaluate`、`equity.py:apply`。

- 起点与检查点保存 `funding_daily_proof={version:1,source,equity_revision,payload_hash}`，摘要绑定完整内容，包括历史索引、生命周期、累计 carry 和 offset。来源限新程序首次空仓、显式审计基线、已经通过逐日核验的连续区间；没有数据库自动补标记迁移。
- 所有核验先检查起点证明；检查点缺失/不属于当前起点和基线时，只能退回有有效分日证明的起点。已有适用检查点证明损坏也拒绝。无来源、未知版本、摘要不符、旧净额起点均停开，保留旧起点及检查点。
- 新起点/检查点仍与收益、历史及原管理审计同事务提交，继续比较快照、归属、equity_revision、原起点/检查点和订单摘要。失败回滚，旧日亏/停机锁不清。
- 回归实际读取本地 `b61662d` 的 `external_accounting.py` 和 `funding.py` 并执行，生成前日 +25、当日 -25 首次漏报、r15 已错误滚到当天的文件库。经真实 startup/reconcile 升级与 SQLite backup 恢复，两种路径在空流水、只补一条、全部补回时均保持未认证，真实 can_open/check_order 不能放行。
- 正向对照覆盖新认证起点检查点丢失后的核验、正常 UTC 滚日后证明来源、事务失败不留下证书；原 r16 的单日/跨日/迟到资金费回归原文保留并通过。

**升级边界：** 无版本证明的 r16 起点/检查点也不能自动视为可信，因为它们可能已经经过原 P1 错误迁移。若不能提供独立历史分日/来源证据，会持续停开；本轮没有提供人工填写金额、直接导入证明或补造日界观察的接口。不要通过删起点、改 marker、删库、缩短窗口或默认累计为零恢复开仓。已有未证明区间不能借 rebaseline 绕过。诊断沿用 `funding_utc_boundary_unproven` / `external_income_unverifiable`。

### B：明确空页、官方接口核实及分页

用户报告影子版本为 `f559f95`，Bitget financial-records 在指定时间段无记录时返回 `data={"list":null,"cursor":null}`，原诊断为 page=1、rows_read=0、response_list_invalid。本轮没有读取线上服务；该输入事实来自用户，本地通过真实 Transport 的 localhost 成功/错误响应及适配器回归验证修复。

`history.py:page_rows` 仅对 financial-records 开放明确 null/null 终止页；Transport 必须先确认 HTTP/`code="00000"` 成功。两个键必须存在。`list=null,cursor=""` 的原失败断言仍成立，缺键、null data、非成功/缺失 code、布尔/对象/非法游标仍拒绝。第一页和正常非空页之后的明确 null/null 都可结束分页。

官方 [financial-records](https://www.bitget.com/legacy-docs/uta/account/Get-Financial-Records)、[history-position](https://www.bitget.com/legacy-docs/uta/trade/Get-Position-History)、[trade/fills](https://www.bitget.com/legacy-docs/uta/trade/Get-Order-Fills) 定义 list 为数组、cursor 为字符串；未查到这些页面对专门 null 空页的承诺。因此后两项保持 `list=[],cursor=""` 空页，拒绝 null/null，不外推财务流水的实测例外。[CrossEx account_book/history_trades/history_positions](https://www.gate.com/docs/developers/crossex/) 为顶层数组，继续接受 []、拒绝 null/对象；这是按 Schema 解释的空数组，并非文档提供专门空响应示例。

共享分页解析也修复 Bitget fills/unfilled-orders 的“短页先返回、忽略 cursor”分支，短页非空游标必须继续，畸形终止页和重复游标继续拒绝。CrossEx 订单成交页增加明确数组验证。正成交仍必须有匹配的成交数量、币种和费用，空列表不能将正成交记成零费用。没有增加真实接口权限或改读写 API 路径。

### C：热点、缓存及实测

源码证明账户 worker 每 5 秒读取从 UTC 日初/未完成旧起点到当前 cutoff 的完整历史；原正常核算一轮重复构建历史索引三次，并重复写入未变化的 `external_verified_history`。离线 profile 同时定位到完整对象复制/持久化、脱敏、目录读取和历史处理，**没有取得线上 profile，不能把报告的 71% 全部归因于某一项**。

最明显的随运行增长热点是 `Redactor.clean`：原实现对每个字符串都重建并排序最多 2,048 条签名集合。固定 1,000 行/4,000 字符串、满签名环的单次 CPU 对照，由基线约 **0.81719s** 降为 **0.00297s**（旧 10 次、新 100 次均取每次均值；真实旧源码离线运行，无真实秘密或鉴权）。新实现每文档快照一次集合、按长度跳过不可能匹配、只保存实际匹配项，保留敏感键过滤；长随机文本回归将额外内存从失败的约 14.8 MB 限定到测试要求的 <4 MiB。敏感键缓存 1,024 项，签名环容量不变。

核算现在同轮共用一份历史索引；跨轮只复用规范 JSON 字节完全相同的已读取行摘要，新增/变化行重新计算。避免 Python `1==True` 造成缓存误认；缓存最多 25,000 行、8 MiB 行内容，超限不影响完整性判断。摘要计算不再额外复制整棵 plain 树；规范编码与旧摘要一致。已核实历史未变不再重写索引，有新增时仍按原事务提交。缓存不替代响应、不供给缺失行、不缓存可开仓结论；部分读取、消失、改写、迟到回填和失败恢复保持原检查。

**与“只追加拉取”的边界：** 当前公开接口没有可用的旧页版本/摘要/删除游标，纯追加网络读取不能同时保证检测任意旧流水消失。本轮优先遵守“不降低风控”，只实现增量计算/写入缓存，保留原全窗口网络复核和频率，未实施纯追加网络拉取。没有把这一项写成已完成的网络优化；未来若要减少请求，须先有可验证的旧历史完整性机制。

`tests/round17_resource_probe.py` 使用真实 Engine、SQLite、Accounts、适配器和分页，5 Hz tick、每 5 秒刷新账户、3 笔登记外仓、12,863 条目录、21,001 条合成流水，默认 program 风控，income.verified=true。固定时间段先准备/暖机，再测墙钟稳态；无真实网络/密钥。最终测量独占运行测试负载，无同时执行 unittest。

| 测量 | 时长 | 单核平均 CPU | 末次 RSS | 稳态最大 RSS | 进程峰值 RSS |
|---|---|---|---|---|---|
| 基线 c93183a，签名环为空 | 65.046s | 32.116% | 104,681,472 B | 104,681,472 B | 122,486,784 B |
| 最终实现，2,048 条模拟签名 | 65.168s | **19.733%** | **107,999,232 B** | **108,265,472 B** | 123,363,328 B |

最终末次 RSS 约 **103.0 MiB / 108.0 MB**，稳态最大约 103.25 MiB；满足这个指定负载的 CPU ≤20%、稳态 RSS ≤120 MB。两次签名环条件不同，不宣称是完全相同输入的整进程 A/B；签名器微基准单独采用相同输入。另一次与 unittest 并行的初步结果未作为性能验收，profile 数据只用于定位、不充当稳态数字。

负载没有模拟真实网络延迟、全部市场采样 worker、并发 API 客户端或 Linux/cgroup，**不是整套线上服务 71%→19.733% 的证明**。现有 `MemoryMax=192M` 保留；本地数字不足以据此调低上限，也未证明需要抬高上限。后续获准影子复测时应记录完整服务的启动峰值与稳态，若仍接近用户报告的 158 MB，再根据实测峰值评估独立资源配置；本轮没有通过扩大内存上限掩盖开销。

复现命令（Windows PowerShell，在本 worktree 内）：

```powershell
$env:PYTHONPATH='tools/crossex_exec/tests;.'
python -m round17_resource_probe --seconds 65 --rows 21001 --signatures 2048
python -m round17_resource_probe --seconds 15 --rows 21001 --signatures 2048 --profile
```

### TDD、原断言及最终验收

初始 10 项先红。首份红灯有两个恢复夹具问题（缺 validate_startup、SQLite backup 目标连接未关闭），在实施前修正后重新得到 **8 failures / 3 errors**；真实 r15 错误滚日起点在升级/恢复后 verified=true 的失败已确认。资源、JSON 类型、摘要重复树、长字符串内存、未变历史写入分别有先红再修的独立日志。后续将 r15 funding helper 也替换为原提交实际源码，最终仍通过；不把这次夹具增强冒称初次红灯。新专项最终 **19 项**。

相对 `c93183a`，执行器/fill-sim 原 **53 个 Python 测试/夹具文件逐字未变**（仅规范 CRLF 比较）：2,731 个断言、1,318 个类/函数定义保留。没有删除、修改或放宽旧断言，没有 skip、缩小 discover、内存库替代文件库。契约规范化 startswith 检查通过，只追加。

| 解释器 | 执行器 unittest discover | fill-sim unittest discover | 退出码 |
|---|---|---|---|
| Windows Python 3.12.10 / SQLite 3.49.1 | **608 项 / 128.403s / OK** | **158 项 / 29.672s / OK** | 0 |
| 指定 Windows Python 3.10.20 / SQLite 3.50.4 | **608 项 / 137.759s / OK** | **158 项 / 30.888s / OK** | 0 |

```powershell
python -m unittest discover -s tools/crossex_exec/tests -v
python -m unittest discover -s tools/fill_sim/tests -v
$py310 = 'C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/125141d8-f3d5-4518-98a0-6a4761963215/scratchpad/uvpy/cpython-3.10-windows-x86_64-none/python.exe'
& $py310 -m unittest discover -s tools/crossex_exec/tests -v
& $py310 -m unittest discover -s tools/fill_sim/tests -v
```

两版包含全部原 **5 项真实文件 SQLite 跨进程测试**。旧模拟器每版种子 20261111/20261112/20261113 各 **2,712 回合**，十项 checks 全为 2712、exercised 全非零，外仓检查 96、隔离检查 48；默认 program 模拟器每版三个种子各 **12 场景**，8 次正常开平仓、4 次明确拒单、8 次资金费延迟、20 次重启、40 次发送检查全过。两版各 **107 个 Python 文件**语法解析通过，git diff --check 通过。

全部四份测试日志、十二份模拟器计数、五项跨进程用例名、红灯与 profile/稳态日志的路径和 SHA-256、原断言审计、源码指纹见 [第十七轮验收 JSON](crossex-exec-r17-validation-2026-11-12.json)。fill-sim 双版本在本轮已完整运行，其源码/测试未变，后续只改执行器，未重复运行未变化的套件。

### 线上状态、文件与下一步

用户报告的线上影子仍记为 `f559f95`；本轮未读取或更改实际线上 HEAD，未 push、备份或部署。没有接触线上数据库/密钥。Temp 保留全部离线日志和度量，未清理旧产物；新测试的临时 SQLite 由自身 fixture 清理。最终提交只包含本轮源码、四个新增测试/负载文件及五个文档文件，无整库、秘密或无关未跟踪文件。

本轮本地代码与验证完成，**纯追加网络读取和线上资源验收未完成**，原因及保守选择见 C。后续请在新任务进行独立终审；获准线上只读/持续影子验证后，再确认完整服务的 CPU/RSS、实际空页行为及升级旧库停开状态。无法取得旧日界/来源证据时继续停开，不通过人工改 marker 放行。发布、解除旧停机锁和实盘需另有明确授权。

## 第十八轮

### 范围、基线与本地提交

指定 worktree `C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8`，分支 `codex/crossex-exec-r8`，起始 HEAD `4e75e5df6ede95c69e3da378d4b71334b1140913`，起始工作区干净。范围仅包括真实分页响应、致命异常安全定位及完整生命周期离线冒烟。用户报告 4e75e5d 影子启动即崩溃、systemd 重启 12 次，已回退 f559f95；本轮没有读取或修改线上状态。

| 类型 | 提交 | 内容 |
|---|---|---|
| test | `c2aa306` | 真实形态合成夹具、所有分页入口、真实 Accounts/Engine 生命周期及致命诊断先红测试 |
| feat | `bb6cbf4` | 空数组+null 游标兼容、FATAL 落库和安全标准输出 |
| docs | 本节所在独立提交 | 第十八轮交接、追加契约、README、下一任务及验收 JSON；自身 hash 用 git log 获取 |

**当前尚未满足“双版本 unittest 全过”：有且仅有一处旧测试要求与本轮新需求直接冲突，等待用户明确是否允许修正该旧反例。** 旧测试未改，未跳过或过滤 discover；不得把以下 625/626 结果表述为全过。异步确认已发出，未把未回复当作批准。

### 根因与分页修复

用户 traceback 与本地红灯一致：`Engine.startup → Accounts.validate_startup → Bitget.open_orders → paged → page_rows`。第十七轮将 fills/unfilled-orders 统一接入严格分页解析后，原先按空短页直接返回的 `list=[],cursor=null` 被字符串类型检查拒绝；第十六轮 history 读取也有相同的空数组/null 游标缺口。此前 fake 账户的 validate_startup 直接返回快照，或使用 `cursor=""`，没有覆盖这个实际启动路径。

`history.py:page_rows` 只新增“已验证数组为空、cursor 键存在且明确为 null”终止分支，供四个 UTA 分页入口共用；无需改动 `adapters.py` 的业务路径。financial-records 的成功响应 `list=null,cursor=null` 继续保持原接口限定，不外推其他接口的 null 列表。Transport 仍先验证 HTTP 与成功 code。非空页/null 游标、空页/非空游标、缺键、错误类型、满页无游标、重复游标、预算用尽均拒绝。

新增 `round18_fixtures.py` 按用户 2026-11-12 报告提供四个响应形态：unfilled-orders 空数组/null；fills 与 history-position 各 100 条/字符串 cursor；financial-records null/null。只有形态来自报告，所有记录、ID、游标和账户金额均合成，不保存真实账户内容。

- 四个 Bitget 接口分别验证第一页空终止、满页续读、短页非空游标连续两页后终止、短页空字符串终止、请求 category/limit/orderId/时间窗口保留、重复游标及耗尽预算。正成交遇空明细仍拒绝；费用仍精确核对。
- localhost 真 Transport 验证成功码、缺失 code/data、非成功 code 和 HTTP 403。无真实鉴权；仅测试凭据签名发送到受 DNS/socket 守护的 127.0.0.1。
- CrossEx account_book/history_trades/history_positions/单笔 fills 验证满 1000 条后按页码续读、短页/空页结束；open_orders 验证完整顶层数组。全部拒绝 null、对象及 Bitget 包装结构。
- 第十六、十七轮的短页 cursor 逻辑保留并经正反测试覆盖。1024 请求/250000 行/8 秒历史预算、订单读取 20 页上限、30 天分段、账户新鲜度、重复 ID、现金/资金费来源和日界证明均未变。

本轮只读核实官方 [Bitget 活动订单](https://www.bitget.com/legacy-docs/uta/trade/Get-Order-Pending)、[成交](https://www.bitget.com/legacy-docs/uta/trade/Get-Order-Fills)、[历史仓位](https://www.bitget.com/legacy-docs/uta/trade/Get-Position-History)的游标参数，以及 [CrossEx](https://www.gate.com/docs/developers/crossex/) 顶层数组/page/limit。financial-records 旧文档页本轮访问失败，替代旧地址重定向到无关账户设置页，未当作新证据。null 形态依据用户实测，不宣称这些文档明确承诺 null 终止语义。

### 致命诊断与完整生命周期

`background.py:exception_details` 复用已有最内层 `exception_location`，返回校验过的 error_type/location，完全不调用异常 str/repr、不遍历异常链。`__main__.py:entrypoint` 保留失败退出 1 和原 reason，fatal JSON 加两个字段；`fatal_events` 在已打开 Store 关闭前写同字段的 FATAL 事件。数据库未打开或事件写入失败时仍输出原异常定位，不让写事件异常覆盖原故障。可恢复 tick 告警、后台异常及风控语义不改。

`test_round18_lifecycle.py` 两条正向冒烟分别运行真实 Engine 的 startup → tick → finish_shutdown → close，以及真实 CLI 循环的模拟 SIGTERM 停机。真实 Accounts、CrossEx/Bitget 适配器、三个后台线程、文件 SQLite、程序收益核验和停机分类均参与；仅替换远端 I/O、公共行情及 Windows 不具备的 POSIX 锁/信号操作。验证 activity.complete、收益/资金费证明、程序和整户为空、退出 0、实际 tick 执行、线程退出和重开数据库后停机状态仍在。没有 mock validate_startup、跳过 startup 或用内存库代替文件库。

负向测试包括畸形活动订单启动失败、运行循环致命失败、FATAL 写入失败、打开数据库前失败及异常正文不可格式化/携带异常链。验证日志与事件只含白名单字段、异常类型/最内层模块及精确行号，失败退出 1且无订单。此冒烟覆盖离线启动集成；不等同 Linux/systemd、真实账户权限、真实 SIGTERM、API 服务或实盘验收。

### TDD、全量结果与唯一待确认冲突

所有实现修改之前先跑新增测试，第二份独立红灯为 **18 项、22 errors（含 subTest）**；真实 startup 冒烟准确失败于 adapters.py 的 invalid_response。运行期故障用旧已接受的空字符串游标隔离分页问题，确认独立触达 RuntimeError。实现后新增 18 项在 Python 3.12.10 和指定 3.10.20 均通过，耗时 1.987s/2.055s。红灯、专项和全量日志及 SHA-256 见[验收 JSON](crossex-exec-r18-validation-2026-11-12.json)。

| 解释器 | 执行器全量 discover | fill-sim 全量 discover |
|---|---|---|
| Windows Python 3.12.10 | 626 项 / 230.772s / 1 failure | 158 项 / 32.817s / OK |
| 指定 Windows Python 3.10.20 | 626 项 / 244.853s / 1 failure | 158 项 / 33.500s / OK |

两个版本唯一失败均为 `test_round17_history.EmptyHistoryTests.test_financial_null_requires_both_keys_and_exact_terminal_shape` 的 `value={'list': [], 'cursor': None}` 子用例：旧要求抛 APIError，新需求要求正常结束。不能同时保持此反例语义和实现新需求。已请求仅把这一处过时反例迁为正例，保留其余畸形/风控断言；**尚未修改该文件**。确认后只需单独 test 提交修正该输入的预期，并重跑双版本完整执行器 discover；fill-sim 本轮已有完整通过结果。

原 **57 个 Python 测试/夹具文件逐字未变**（规范 CRLF 比较），**2,791 个断言、1,353 个类/函数定义**均保留。两版均完整运行并通过原 5 项真实文件 SQLite 跨进程测试。两套模拟器均通过：旧三种子 20261111/20261112/20261113 各 2712 回合、N=1、十项不变量 checks 各 2712 且 exercised 非零；默认 program 三种子各 12 场景，原 8 次开平仓、4 次明确拒单、8 次资金费延迟、20 次重启、40 次发送检查保留。完整 12 份种子统计入验收 JSON。

110 个 Python 文件通过 3.10 语法解析，git diff --check 通过，接口契约仅追加。实施后自行审查分页拒绝分支、致命事件写入时机、脱敏和冒烟覆盖；没有调用子代理，不把自审称为独立终审。未测 CPU/RSS，未改变 MemoryMax、风控阈值、资金费证明、UNKNOWN 处理、外仓禁单或任何旧锁。

复现命令仍为项目根目录 `python -m unittest discover -s tools/crossex_exec/tests -v` 和 `python -m unittest discover -s tools/fill_sim/tests -v`；3.10 使用用户指定的 `C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/125141d8-f3d5-4518-98a0-6a4761963215/scratchpad/uvpy/cpython-3.10-windows-x86_64-none/python.exe` 执行相同参数。未过滤失败测试。

### 交付边界与接续

本轮只做本地 test/feat/docs 提交，显式暂存任务文件，提交关闭 hooks/GPG；父仓库 worktree 索引写入使用已授权本地提交的扩展权限，无自动审批拒绝。未 push、SSH、WSL、amend、真实鉴权、线上备份或部署。线上当前版本仅按用户报告记为回退 f559f95，未独立核实。

Temp 保留红绿灯、双版本全量日志；新测试临时数据库由自身 fixture 清理，没有删除旧诊断、WIP 或其他任务产物。最终仓库只包含本轮提交，无新未跟踪整库或凭据。

接续先取得上面单一测试冲突的明确决定并完成全量绿灯，之后再做独立终审。线上只读/影子发布应在新任务另行授权，先备份并验证真实空账户启动和持续运行；不要直接部署当前尚有测试冲突的交付，不通过删库、改证明、解锁或降低风控恢复服务。后续页面或其他模块另建任务。
