# crossex-exec 第八轮交接（2026-11-12，本地验收完成）

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
