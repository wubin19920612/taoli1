# Gate WS book_ticker 交接（2026-11-10）

## 状态与范围

分支 `codex/gate-bbo`；基线 `f3668bd1aa09f9905b275e659d1a7ade32923c62`。
已完整阅读本仓库任务书 `docs/gate-bbo-spec-2026-11-10.md` 和引用的
`D:/吴彬工作/claude/套利/research/small-lot-quant/quote-freshness-design.md`。

已实现并验证子进程、双 WS、批量 IPC、合并规则、配置、JSON 观测和关闭。
**本任务尚有一个待确认范围项，不能标为完整验收通过**：
实际 `/api/health` 定义位于 `backend/app/api/routes_health.py`，任务书仅授权
`main.py` 的 lifespan 和 health，并要求只改白名单文件。已提出仅扩展此文件的范围确认，
未收到答复前没有修改该文件。`GateBboRuntime.status()` 已提供可直接接入的摘要。

另有外部冒烟限制：JCT_USDT 现货的两个公开 REST 接口均返回
`400 INVALID_CURRENCY_PAIR`。已按要求订阅全部六个市场，五个收到更新；
六市场覆盖断言保留并失败，未替换市场、未把它记成全通过。

用户明确要求本地交付：没有 push、SSH、WSL、部署、线上数据库备份或修改 `.env`。
没有更改影子账本、告警判定、`spread_engine.py`、`orderbook_validator.py`、compose 或前端。
没有修改任何原有测试文件或放宽断言。

## 本地提交

- test：`f51aaa4d7714ac39c6a8e1cb8789c9f2c90b4fd9`。
- feat：`b2cc1d9bcb6c837a0085613b33baacbb433e3de0`。
- docs：本交接文件单独提交；用 `git log -1 --format='%H %s' -- docs/task-handoff-2026-11-10-gate-bbo.md` 查得该提交，避免自引用 hash。

test 提交先于实现，因此单独检出 test 提交会因尚不存在实现模块而失败；与随后 feat 组合验收。
提交均仅保存在本地分支，不推送。

## 文件清单与入口

实现仅修改/新增：

- `backend/app/exchanges/gate_bbo.py`：spawn 入口、双连接、逐条解析、订阅/取消订阅、心跳、去重、批量发布。
- `backend/app/services/gate_bbo_runtime.py`：订阅筛选、spawn、IPC reader、缓存、RSS/崩溃守护、关闭、状态和 300 秒日志。
- `backend/app/exchanges/gate.py`：`_merge_bbo`，现货/合约采集接入。
- `backend/app/core/config.py`：七项配置及校验。
- `backend/app/main.py`：lifespan 创建/绑定/启动，Gate 与 shadow 并行关闭。

新增测试与证据：

- `backend/tests/test_gate_bbo.py`：解析、真实/合成夹具、合并、可执行判定、完整基线回滚、配置、订阅、主进程 AST 守护。
- `backend/tests/test_gate_bbo_process.py`：真实 spawn IPC、上限和背压、崩溃/RSS 重启、完整退避、正常/强制/并行关闭、ping、重连代次、600 秒刷新、lifespan。
- `backend/tests/gate_bbo_harness.py`：离线子进程替身，子进程内也禁止外网。
- `backend/tests/gate_bbo_legacy_reference.py`：从基线冻结的 GateAdapter，只供回滚逐字段比较。
- `backend/tests/gate_bbo_smoke.py`：显式运行的联网冒烟/录制工具，不会被 pytest 自动收集。
- `backend/tests/fixtures/gate_bbo/recorded.json`：公开 WS 原始 ack 与更新，含接收时间；合约三市场、现货 AKE/BTC。
- 同目录 `edge-cases.json`：明确标注为合成的空侧、非法数、ack、error 消息。
- 同目录 `smoke-child.json`、`smoke-result.json`：30 秒观测原始结果。
- 同目录 `jct-spot-rest.json`：冒烟后两个公开 REST 的返回记录。
- 本交接文件。

## 配置

| 环境变量 | 默认值 | 校验/用途 |
|---|---:|---|
| `GATE_BBO_ENABLED` | `0` | 严格布尔；禁用不 spawn，但批量报价仍标 estimated |
| `GATE_BATCH_QUOTES_EXECUTABLE` | `0` | 严格布尔；`1` 完整返回旧批量采集对象，即使 WS 已启用且新鲜 |
| `GATE_BBO_MIN_VOLUME_USD` | `5000000` | 有限正数，Gate 单市场成交额门槛 |
| `GATE_BBO_MAX_FUTURES` | `300` | 整数 1..1000 |
| `GATE_BBO_MAX_SPOT` | `300` | 整数 1..1000；两侧合计硬界 ≤2000 |
| `GATE_BBO_QUOTE_MAX_AGE_SECONDS` | `120` | 有限正数，使用交易所时间而不是本机接收时刻 |
| `GATE_BBO_CHILD_MAX_RSS_MB` | `120` | 有限正数，父进程每秒检查实际 RSS，超限退避重启 |

布尔环境变量接受 `0/1`、`false/true`、`no/yes`、`off/on`；拼写错误报错。
`Settings` 直接构造时要求真正的 bool，不能传字符串或整数。
当前未设置任何实际环境变量；默认关闭 WS。

## 业务与运行规则

1. 名单取 `SnapshotStore.get_all_markets()` 最新采集结果，首次非空后每 600 秒重算；
   启动空表每秒重试，不空等十分钟。Gate 成交额 ≥门槛、同 symbol 至少存在非 Gate 市场；
   现货/合约独立限额。仍满足门槛的既有订阅优先，再按成交额降序、原始市场名排序。
   只用 symbol 筛选订阅，不据此做交易身份判断；缓存键始终为 `(market_type, raw_symbol)`。
2. WS 每 100 市场一批，批间至少 200 ms；应用层 ping 每 30 秒，协议 ping 由 websockets 处理。
   WS 和进程退避均为 1/2/5/10/30 秒，持续正常至少 60 秒后重置退避。
3. 子进程每约 200 ms 发一个 IPC 包，含变化字典及两连接的状态/最后消息时间、CPU 秒；
   单包 ≤2000。队列均 `maxsize=1`；背压时保留最新 dirty，静默市场的最后一条不会被随意丢弃。
4. 主进程 reader 线程处理 IPC 反序列化并合并待处理字典，最多挂一个事件循环回调。
   主循环只更新批量字典，不调用原始 WS JSON 解析。行情、dirty、mailbox 各有订阅数量边界。
5. 连接重建递增代次并清旧报价；旧代次 IPC 不能恢复为 fresh。
   同连接丢弃旧 `u`/旧 `t`；非法的新报价发送 tombstone，仍记住有效的新序号以阻止旧包恢复。
6. 新鲜报价必须满足：进程存活、启用、仍在名单、当前连接代次、连接最后消息距今 ≤5 秒、
   `now - t` 在 `[-2, max_age]`、两侧有限正价格与数量、`bid < ask`。
7. 新鲜时覆盖 bid/ask/两侧数量，写 UTC `upstream_timestamp=t`、`quote_time_source="ws"`。
   Gate 合约 WS 数量是张数，合并时乘现有 `quanto_multiplier` 转为基础资产数量；
   倍率缺失/非有限、换算溢出或下溢则回退，避免生成虚假盘口深度。现货数量直接使用。
   成交额、资金费率/周期、mark/index 仍取原批量接口。
8. 回退保留批量价格，标 `is_estimated=True`、`estimated_fields` 包含 bid/ask、
   `quote_time_source="received_batch"`。现有 `_has_executable_book` 与 `prepare_markets` 自动拒绝其可执行性。
   没有修改这两个消费者或 MarketSnapshot 字段。
9. `GATE_BATCH_QUOTES_EXECUTABLE=1` 优先于 WS，完整恢复基线每个字段；用冻结适配器覆盖
   spot/future × 有/无 WS 共四种完整采集比较。
10. 每 300 秒日志形如 `{"gate_bbo": {...}}`，包含每连接 connected/live、请求/已发送订阅数、
    消息数与最近至多 300 秒的消息速率、重连次数、错误类，以及 pid、RSS、CPU 秒、
    重启数、IPC 批次数/最近批大小、合并命中/回退数。计数为当前 runtime 生命周期，
    连接消息/重连计数随子进程重启重置；RSS/CPU 是最后一次测量值。

## 验证命令与原始汇总

指定解释器：
`C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/435b98f6-0522-4055-9b28-46a94def1380/scratchpad/venv-lock/Scripts/python.exe`

Windows PowerShell，在 `backend` 下设置 `$env:PYTHONPATH='.'` 后执行。
临时目录父目录 `.pytest-tmp` 已创建；首次缺失父目录的 setup error 已解决，没有改测试断言。

全量（最终版本）：

```text
-m pytest tests -q -p no:cacheprovider --basetemp=../.pytest-tmp/gate-bbo-full
2848 passed, 9 deselected, 13 warnings in 200.53s (0:03:20)
```

新增测试单跑（随后关闭计数修复还通过了下述 111 项合跑）：

```text
-m pytest tests/test_gate_bbo.py tests/test_gate_bbo_process.py -q -p no:cacheprovider --basetemp=../.pytest-tmp/gate-bbo-new
110 passed in 17.90s
```

完整原始输出保留于本机被忽略的 `.pytest-tmp/gate-bbo-full-verified.log`、
`.pytest-tmp/gate-bbo-new-run.log`。前一轮全量为
`2830 passed, 9 deselected, 13 warnings in 190.62s (0:03:10)`；后续增加了测试，因此以前一行最终汇总为准。
原有 warnings 来自 Starlette/httpx 弃用提示及 Pydantic 使用 utcnow；未压制。
中间一轮新增并行关闭的异常日志处理漏记原有 shadow `shutdown_lifespan` 计数，
原有测试抓出了此回归；已仅在 main.py 恢复计数，原测试未改。
该原测试与全部新测试合跑的原始汇总为 `111 passed in 18.23s`。
目标实现/新增测试的 Ruff 检查输出 `All checks passed!`；`git diff --check` 通过。
未改前端，未运行前端构建。

关闭专测原始输出（同一锁定解释器、真实 Windows spawn）：

```text
gate_graceful_shutdown_seconds=0.083955
gate_forced_shutdown_ignore_terminate=False seconds=0.713354
gate_forced_shutdown_ignore_terminate=True seconds=1.110322
gate_shadow_parallel_shutdown_seconds=4.029599
11 passed in 15.09s
```

这些是关闭专项运行时的测量；最终新增测试也保留 `<2s`、并行 `<6s` 与事件循环不阻塞断言。
正常关闭等待约 0.7 秒，然后 terminate/kill 两级处理；整体 Gate deadline 为 1.8 秒，留出调度裕量。
与 shadow 使用 `asyncio.gather(..., return_exceptions=True)`，避免 Gate 错误使 shadow 尚未关闭就释放 DB。

## 30 秒联网冒烟

执行：`python tests/gate_bbo_smoke.py`，只访问指定公开 WS；事后只查询两次公开 REST。
冒烟工具使用真实 GateBboWorker，仅在子进程增加录制观测，运行真实 runtime/IPC/缓存。
两侧均发送 AKE_USDT、JCT_USDT、BTC_USDT 订阅并收到成功 ack。

| 观测 | 现货 | 合约 |
|---|---:|---:|
| 子进程收到 update 数 | 573 | 2229 |
| update/秒（实际观测 29.672 秒） | 19.3111 | 75.1213 |
| 子进程接收时 `now-t` 中位 ms | 197.254 | 196.474 |
| 子进程接收时 `now-t` p90 ms | 226.652 | 222.026 |
| 主进程见到新值时 `now-t` 中位 ms | 266.364 | 248.438 |
| 主进程见到新值时 `now-t` p90 ms | 369.700 | 363.885 |

共 143 IPC 包，末包 5 个市场；RSS 32.7109 MB，CPU 1.125 秒；无重连、无子进程重启。
两连接结束时均 live；关闭 0.144310 秒。主进程样本是批量后的最新值，子进程样本是全部逐条 update，两者统计口径不同。

原始 `smoke-result.json` 保留冒烟当时的状态，**其中 status.messages_per_second 在当时还错误地用 300 秒作启动期分母**；
实际速率以 `smoke-child.json` 的上述实测为准。随后已修复生产计数窗口，并新增专门的 2 秒窗口离线测试；
没有修改原始冒烟文件来伪装修复前结果。按“一次”冒烟要求没有重复录制。

退出码为 1：严格的六市场数据覆盖断言失败，仅缺 `spot:JCT_USDT`。
REST 对 `spot/currency_pairs/JCT_USDT` 和 `spot/order_book?currency_pair=JCT_USDT&limit=1`
均返回 `INVALID_CURRENCY_PAIR`；即便批量 subscribe ack 成功，也不能证明每个 symbol 实际存在。
这项外部限制需要用户接受或另指定有效现货；不能声称六市场冒烟全通过。

## 待确认的 health 最小接入与下一步

建议仅授权 `backend/app/api/routes_health.py` 的两处变更：

1. `/health` 认证成功后，将已有返回字典命名为 result，在存在
   `request.app.state.gate_bbo_runtime` 时添加 `result["gate_bbo"] = runtime.status()`。
   匿名响应仍为 `{"status":"ok"}`，不会泄露进程指标。
2. 可在受保护的 `/health/detail` 同步添加相同摘要；不存在 runtime 时保持现有字段合同。

新增离线测试应同时覆盖：正常 lifespan 中 WS 关闭也显示摘要、启用/回退计数、匿名不暴露摘要、
未启动 lifespan 时现有 exact-fields 测试仍通过；不编辑既有测试。
收到文件范围授权后，完成此接入，再以同一解释器跑新增和全量测试，另按 test/feat/docs 分组追加提交，不 amend。

生产没有改变。2 小时/24 小时灰度、主事件循环 CPU/卡顿变化、生产 quote 对齐和 fill-sim 落差
均未验证；这些需要另一个获准部署/运维任务。本任务不扩展到 D5 的 fill-sim 归档。
当前没有原有 WIP；本任务生成的 pytest 日志/临时目录被 `.gitignore` 忽略并保留。

## PM 补充决定执行记录（2026-11-10，本轮追加）

### 范围、状态与提交

分支仍为 `codex/gate-bbo`；本轮起点为
`e1c7b6b6146dc72ac4b2a42ff2ee325fe3507ba7`，开始时工作区干净。
PM 已明确授权 health 文件，并将冒烟改为现货 AKE/BTC、合约 AKE/JCT/BTC。
**本轮两项本地验收均已通过**；前文 health 待授权及必须覆盖 JCT 现货的待确认项已由本轮决定解决。
前文记录作为历史保留，旧六市场冒烟失败仍是当时的真实结果。

- test：`07be23247ee6978d4397ee16c3789b69f02cd402`。
- feat：`4b164a5bfabd2c0c92da506112e4e6dc9736d60b`。
- docs：本追加记录与任务书的 PM 补充决定单独提交；执行
  `git log -1 --format='%H %s' -- docs/task-handoff-2026-11-10-gate-bbo.md` 可查，避免自引用 hash。

test 在 feat 之前，因此单独检出 test 提交时，新增摘要用例预期失败；与后续 feat 组合验收。
均为追加本地提交，没有 amend 或推送。

### 本轮文件与行为

- `backend/app/api/routes_health.py`：认证成功后，runtime 存在时增加
  `gate_bbo = GateBboRuntime.status()`。直接复用既有状态和计数，不重复计算或改变计数口径。
  匿名及无效凭据仍只返回 `{"status":"ok"}`，不会调用 `status()`；保留 `Cache-Control: no-store`。
  runtime 缺失或为 None 时保留旧响应的精确字段；WS 关闭但正常 lifespan 已创建 runtime 时仍显示摘要。
- `backend/tests/test_gate_bbo_health.py`：新增 13 个离线用例，覆盖真实会话/API token 认证、
  连接/进程/消息速率和合并命中/回退计数、读取不修改计数、匿名/错误凭据/开发绕过不泄露、
  无 runtime 的旧字段合同，以及正常 lifespan 中关闭 WS 的摘要。继承全局禁止外网夹具。
- `backend/tests/gate_bbo_smoke.py`：明确区分现货两市场、合约三市场，使用不同文件名保存新证据。
  覆盖断言仍要求订阅集合与收到数据的集合完全相等；批次数、连接 live 和 `<2s` 关闭断言保留。
- `backend/tests/fixtures/gate_bbo/recorded-2026-11-10-pm.json`：本轮公开 WS ack 与更新录制。
- 同目录 `smoke-child-2026-11-10-pm.json`：本轮子进程统计。
- 同目录 `smoke-result-2026-11-10-pm.json`：本轮完整结果，新增订阅清单以便核对。
- `docs/gate-bbo-spec-2026-11-10.md`：仅追加本轮 PM 决定。
- 本交接文档：仅追加本轮记录。

现有 HL BBO 的统计入口是 `HyperliquidBboStream.stats()`，health 没有单独的 HL 字段；
本轮按任务书约定，在同一 health 摘要位置接入 Gate 的现有状态，沿用现有认证保护。
没有扩大到 `/health/detail` 或修改 HL 模块。
没有更改 Gate 生产计数实现、配置、报价合并规则或其他业务模块，也没有编辑既有离线测试。

### 锁定 venv 验证及原始汇总

全部使用任务书指定解释器：
`C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/435b98f6-0522-4055-9b28-46a94def1380/scratchpad/venv-lock/Scripts/python.exe`。
在 `backend` 目录设置 `$env:PYTHONPATH='.'`，执行以下参数。

全量：

```text
-m pytest tests -q -p no:cacheprovider --basetemp=../.pytest-tmp/gate-bbo-full
2861 passed, 9 deselected, 13 warnings in 189.87s (0:03:09)
```

本轮新增 health 测试最终单跑：

```text
-m pytest tests/test_gate_bbo_health.py -q -p no:cacheprovider --basetemp=../.pytest-tmp/gate-bbo-health-pm-final
13 passed in 7.06s
```

全部 Gate BBO 测试合跑：

```text
-m pytest tests/test_gate_bbo.py tests/test_gate_bbo_process.py tests/test_gate_bbo_health.py -q -p no:cacheprovider --basetemp=../.pytest-tmp/gate-bbo-pm-new
123 passed in 21.49s
```

新测试曾先在未接入 health 时得到预期的 `3 failed, 10 passed in 7.09s`；随后完成接入。
全量包含原有 health 精确字段/过滤和认证回归测试；未放宽原有测试或断言，未压制 warnings。
9 项 deselected 来自既有默认 `not network and not benchmark` 配置，执行参数未改变。
Ruff 检查新增测试及修改后的冒烟工具为 `All checks passed!`。
`routes_health.py` 的基线和本轮均有相同 5 条既有告警（I001、两处 S110/BLE001），未为此扩展修改。
本轮 diff 检查通过。

原始日志保留于被忽略的本机目录：

- `.pytest-tmp/gate-bbo-full-pm-2026-11-10.log`。
- `.pytest-tmp/gate-bbo-health-pm-final.log`。
- `.pytest-tmp/gate-bbo-pm-new.log`。
- `.pytest-tmp/gate-bbo-smoke-pm-2026-11-10.log`。

### 本轮 30 秒公开 WS 冒烟

使用同一锁定解释器执行 `tests/gate_bbo_smoke.py` **一次**，退出码 0。
只访问公开 WS，不请求私有接口；与全量 pytest 串行运行。
现货 `AKE_USDT`、`BTC_USDT`；合约 `AKE_USDT`、`JCT_USDT`、`BTC_USDT`，五市场全部收到数据。
修复后的生产消息速率来自既有 worker 的实际启动窗口，不再以 300 秒作启动期分母。

| 观测 | 现货 | 合约 |
|---|---:|---:|
| 结束前最后 IPC 的 messages | 1238 | 2815 |
| 结束前最后 IPC 的 messages_per_second | 42.144681 | 95.829787 |
| 子进程全部 update 数 | 1243 | 2841 |
| update/秒（子进程实际观测 29.766 秒） | 41.759054 | 95.444467 |
| 子进程接收时 `now-t` 中位 ms | 212.291 | 213.366 |
| 子进程接收时 `now-t` p90 ms | 251.499 | 255.306 |
| 主进程见到新值时 `now-t` 中位 ms | 258.567 | 255.285 |
| 主进程见到新值时 `now-t` p90 ms | 367.529 | 373.055 |

共 144 IPC 包，末包 5 个市场；RSS 32.746094 MB，CPU 1.359375 秒。
两连接结束时均 connected/live，发送/请求订阅数为现货 2、合约 3，无重连、无子进程重启。
本轮关闭实测 `0.24495409987866879` 秒，严格 `<2s`。
冒烟直接验证 worker/runtime/IPC，没有运行适配器采集合并，故其 merge_hits/merge_fallbacks 均为 0；
合并路径由原离线测试覆盖，health 对计数的原样展示另有新增用例。

status 是结束前最后 IPC 的计数和启动窗口速率，消息口径包含 ack 等 worker 认可的消息；
child 是关闭完成后汇总的全部逐条 update，统计截止点不同；parent 只采样批量后的新值，
因此三者样本数及速率不能直接视为相同口径。

旧 `recorded.json`、`smoke-child.json`、`smoke-result.json`、`jct-spot-rest.json`、
`edge-cases.json` 共 5 个原文件，运行前后 SHA256 全部相同，Git diff 也为空。
旧速率错误与缺失 JCT 现货的证据没有被覆盖或修改。

### 线上状态与剩余边界

本轮按明确限制仅本地交付：未 push、SSH、WSL、部署、备份线上数据库或改变线上版本。
本轮开始没有其他 WIP；新增证据全部随 test 提交，日志与 pytest 临时文件继续被忽略并保留。
本轮本地验收已完成，生产 2h/24h 灰度、CPU/卡顿及 fill-sim 对齐仍未验证。
下一步如需部署和灰度，应在新的部署/运维任务中使用本交接继续；不在本任务扩展范围。
