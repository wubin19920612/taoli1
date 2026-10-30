# S3-B 实时序列缓存加上限（2026-10-30）

## 背景
`backend/app/services/pair_spread_query.py:519` 起四个模块级 dict（_REALTIME_PAIR_SPREAD_CACHE、_REALTIME_PAIR_FUNDING_CACHE、_REALTIME_PAIR_OPEN_INTEREST_CACHE、_REALTIME_SYMBOL_SPREAD_CACHE）。键由用户输入组成（交易所/DEX/symbol/倍率/周期），单序列已限 4000 点、访问时按 hours 裁剪当前键，但**键从不删除、没有总量上限**。例如 50 个组合、三类各填满就是 60 万点。生产目前只有 0–1 个键，这是防护性改动，不减少上游请求。

## 方案
1. 新增 `backend/app/services/realtime_series_cache.py`：有界缓存类，按键保存序列，支持：每类最大键数（默认 128）、四类合计最大点数（默认 200000，跨实例共享一个预算对象）、闲置 TTL（默认 1800 秒，按最后访问时间）、超限时按最久未访问淘汰（LRU），再不够就淘汰最旧的键直到满足点数预算。时钟可注入（测试用）。淘汰发生在写入时和读取时顺便清理过期键，不加后台任务。
2. 四个 dict 换成这个类的实例，尽量保持现有读写调用形状（get/set/裁剪）。现有健康检查、memory probe、census 里的 keys/points 统计接口保持字段名不变。
3. 键被淘汰后再次查询：行为等同首次查询（从当前点开始累积），并在该次响应现有的 warnings/notes 字段（先读代码确认有哪个字段）里加一条明确说明“实时序列缓存已过期或被淘汰，历史从现在重新累积”。如果响应模型没有合适字段，只记 debug 日志，不改响应模型，在报告里说明。
4. 键隔离：不同 DEX、倍率、周期、交易所的键不能串线（除下述 PM 批准的 symbol 交易腿 DEX 例外外，现有 key 构造不改）。
5. 上限值做成模块常量，不新增环境变量。

## 测试
- 灌入 10000 个不同键后每类键数 ≤128、合计点数 ≤200000。
- 闲置超过 TTL 的键被清理；被访问的键刷新 TTL。
- LRU 顺序正确：最近访问的不被先淘汰。
- 淘汰后再查询有说明（或按第 3 条报告）。
- DEX/倍率/周期不同的两个查询结果不串。
- 现有 pair_spread_query、premium_index、health/memory probe 相关测试不放宽。

## 范围
只改 pair_spread_query.py、新增 realtime_series_cache.py、必要时读取这些缓存统计的那一个模块（只改导入/调用，不改字段）、测试、本任务书。commit 信息 `perf: bound realtime pair spread series caches`。

## 实现与验收记录

- 工作分支：`codex/s3b-realtime-cache-bound`；基线：`235a7732e84f7a8a05ba0bf124ac6ac859477194`；开始时工作区干净。
- 缓存实现：`backend/app/services/realtime_series_cache.py`。四个实例共享 `RealtimeSeriesBudget`，按访问顺序淘汰整键；读写时清理所有实例的闲置过期键，无后台任务、无环境变量。
- 模块常量：`REALTIME_CACHE_MAX_KEYS = 128`、`REALTIME_CACHE_MAX_TOTAL_POINTS = 200_000`、`REALTIME_CACHE_IDLE_TTL_SECONDS = 1_800`。原单序列 `PAIR_SPREAD_REALTIME_MAX_POINTS = 4000` 保持不变。
- 读写复制列表/嵌套列表容器，调用方追加和裁剪后必须写回，避免原地增长绕过预算；响应中的历史列表不会因后续缓存操作而改变。
- 淘汰说明使用 `PairSpreadQueryResult.warnings` 和 `SymbolSpreadQueryResult.warnings`，包括历史 K 线查询附带的实时 funding/OI。已知淘汰返回“实时序列缓存已过期或被淘汰，历史从现在重新累积。”，同一响应去重。
- 每类额外保留最多 `REALTIME_CACHE_MAX_EVICTION_MARKERS = 128` 条近期淘汰记录。首次查询不新增告警；淘汰记录溢出后，无法区分首次查询与较早淘汰的未命中返回“实时序列缓存未命中（首次查询或较早的过期/淘汰），历史从现在重新累积。”。这避免用无界淘汰记录重新引入内存增长，同时保证旧键重访仍有说明。
- `realtime_cache_stats()` 保留 keys/points 字段；census 仅更改 getter 调用，读取单个缓存的只读映射视图，避免沿共享预算遍历全部实例。诊断不触发 TTL 清理或刷新访问顺序。
- 测试新增在 `backend/tests/test_realtime_series_cache.py`，使用可注入时钟及离线行情，现有测试断言不放宽。锁定虚拟环境已确认 FastAPI `0.162.0`、Starlette `1.10.0`、pydantic `2.14.1`。
- 本次不访问生产服务器、WSL 或真实 Astro/交易所，不做部署；其他模块留待独立任务处理。

### 全量验证与已批准例外

在 `backend` 目录用任务书指定的锁定虚拟环境执行：

```powershell
& 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\7f276a81-4df2-4527-bc65-b410f9d557b6\scratchpad\venv-lock\Scripts\python.exe' -m pytest -q -p no:cacheprovider
```

首次全量结果：

```text
1 failed, 2334 passed, 6 deselected, 13 warnings in 105.78s (0:01:45)
```

完整命令与输出保存在本机 `%TEMP%\s3b-realtime-cache-bound-full-pytest-20261030.log`。默认 pytest 配置排除 network/benchmark；离线网络保护保持启用。新增缓存模块和测试文件 Ruff 检查通过。其余改动文件的 4 条 Ruff 告警均已确认存在于基线，不在本次修复范围。

唯一失败：新增测试 `test_query_cache_identity_does_not_mix_dex_multiplier_interval_exchange[variant0-symbol]`。原 `_symbol_spread_cache_key()` 的交易腿片段只有 exchange/symbol/multiplier，没有 DEX；同一标的 `io` 和 `xyz` 会共用序列。pair 路径包含 DEX，相关隔离测试通过。

PM 已批准第 4 条的最小例外：只在 `_symbol_spread_cache_key()` 的交易腿片段补入 DEX，从 `exchange:symbol:multiplier` 改为 `exchange:dex:symbol:multiplier`；非 Hyperliquid 时 dex 为空，其他键及格式、行情计算保持不变。原因是原 key 缺少 DEX，会使同一标的不同 Hyperliquid DEX 的序列串线，这是已有缺陷。现有隔离测试断言保持不变。

部署后旧键会自然失效：这些缓存只存在于内存中，进程重启即清空，无需迁移。本轮仍遵守不访问生产服务器、不部署的范围限制。

批准例外修复后的全量结果（同一锁定虚拟环境、同一命令，退出码 0）：

```text
2335 passed, 6 deselected, 13 warnings in 112.31s (0:01:52)
```

完整命令与输出保存于 `%TEMP%\s3b-realtime-cache-bound-full-pytest-20261030-approved-dex.log`。6 项仍按默认配置排除 network/benchmark；13 条为 Starlette/httpx 和 `datetime.utcnow()` 弃用告警。DEX/倍率/周期/交易所隔离测试全部通过，未放宽现有断言；新增缓存模块和测试文件 Ruff 检查再次通过。

## 测试环境（必须）
CI 用 backend/requirements.lock 的锁定依赖（FastAPI 0.162、Starlette 1.10、pydantic 2.14.1），本机全局 Python 是旧版本。全量测试必须用锁定依赖的虚拟环境跑：`C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\7f276a81-4df2-4527-bc65-b410f9d557b6\scratchpad\venv-lock\Scripts\python.exe -m pytest -q -p no:cacheprovider`（在 backend 目录下）。日志里保留命令和汇总行。不要运行 WSL，不要访问生产服务器或真实 Astro/交易所。
