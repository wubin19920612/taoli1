# M1-C 内存定点修复：设计与任务书（Claude，2026-10-25，待用户确认）

前置：M1-A 探针已上线（`f794324`）；B（`MALLOC_ARENA_MAX=2`）实验无效，已回滚。

## 1 证据

### 1.1 线上（探针，每 10 秒采样）

| 指标 | 基线 1h（08:09–09:09） | B 1h（09:12–10:12，arena=2） |
|---|---|---|
| OOM 次数 | 4 | 7 |
| 启动时 rss_anon | 254MB | 254MB |
| 启动后 60 秒内 | 跳到约 580MB（一次采样间隔 +230~330MB） | 同左 |
| 之后 | 约 +7MB/分钟 | 约 +7~9MB/分钟 |
| 被杀前最后一次采样 | 670–690MB | 4 次在启动后 1–3 分钟就被杀，最后采样只有 560–605MB |

结论：
- 致命的是**10 秒内的瞬时尖峰**（≥160MB，采样抓不到峰顶），叠加持续上升的基线。
- B 无效 → 内存碎片（H4）不是主因。
- 价差实时缓存最多 2 个键 → H1 排除。

### 1.2 本机复现（tracemalloc，只统计 Python 层分配；线上 RSS 约为其 1.5–2 倍）

每轮采集（凑齐 11 个交易所、约 1.05 万市场后）峰值约 **100MB**，按阶段拆分：

| 阶段 | 每轮峰值 | 结束后仍占用 |
|---|---|---|
| `MarketValidationService.prepare`（`market_validation_review.py:174`） | **57–59MB** | 约 29MB |
| `build_all_opportunities` | 25–29MB | 约 29MB（就是输出本身） |
| 轮询与 JSON 解析（差额） | 约 15MB | — |
| 别名、过滤、合并、发布 | < 0.2MB | — |

另外：`OfficialMarketIdentityCatalog.refresh()`（启动时跑一次，之后每 86400 秒一次）峰值 **96MB**，结束后仍占 37MB；公告补全峰值仅 13MB（排除）。

### 1.3 `prepare` 为什么占这么多

每一行市场在一轮里最多被 `model_copy` 5 次：
1. `registry.annotate(row, now)`；
2. `_apply_fx(...)`；
3. `annotate()` 末尾 `model_copy(update={"identity_collision": ...})`（用的是**上一轮**的 `collision_symbols`）；
4. `_cached_book()` → `_book_row()`（有新鲜缓存盘口时）；
5. 第 204–207 行对**整个列表**再 `model_copy(update={"identity_collision": ...})`（用本轮重新计算的 `collision_symbols`）。

第 3 步的结果总会被第 5 步覆盖，完全多余。第 5 步执行时，新旧两份约 1 万行的列表同时存活，这是峰值的主要来源。

## 2 修复项

### C1 去掉 `prepare` 里的重复拷贝（主修，预期每轮峰值降低约 30–40MB）

文件：`backend/app/services/market_validation_review.py`

- `annotate()` 不再在末尾做 `identity_collision` 拷贝。为了不影响其他调用方，拆出内部方法 `_annotate_base(row, now)`（= 原 `annotate` 去掉末尾拷贝）。`annotate()` 保持原有公开行为（仍按当前 `collision_symbols` 设置 `identity_collision`），供其他调用方使用。
- `prepare()` 改为调用 `_annotate_base`。重新计算 `collision_symbols` 之后，只做**一次**最终拷贝，并且**只对 `identity_collision` 需要改变的行拷贝**：`row if row.identity_collision == (row.symbol in collisions) else row.model_copy(update=...)`。
- 第 204 行之后的冲突配对逻辑（`combinations`、`pair_allowed`、`invalid` 等）一行不改。
- **等价性要求**：同样输入下，新 `prepare()` 的输出与旧实现逐行 `model_dump()` 完全相等（包括 `identity_collision`、报价、盘口字段），`self.collision_symbols` 与 `discovered` 等副作用也一致。用改动前的实现作为测试里的参照（把旧函数体复制进测试的 helper，或用 git 里的旧版本对比），覆盖：无冲突、本轮新出现冲突、上轮冲突本轮消失、有缓存盘口、FX 换算这几种情况。

### C2 身份目录刷新错开启动高峰（预期消除启动后约 100MB 的叠加）

文件：`backend/app/services/market_identity_catalog.py`、`backend/app/core/config.py`

- `OfficialMarketIdentityCatalog.run()` 增加 `startup_delay_seconds`（默认 300 秒，环境变量 `MARKET_IDENTITY_CATALOG_STARTUP_DELAY_SECONDS`，允许为 0 以恢复旧行为），首次 `refresh()` 等这段时间再执行，期间响应 stop。之后仍按每 86400 秒一次。
- 理由：它每天只需要跑一次，但现在每次 OOM 重启都会在启动最初几十秒内和首轮全量采集同时跑。
- 需在交接里确认：启动后 5 分钟内身份目录还是上一次落库的数据（`MarketValidationService.initialize` 会从数据库加载），不会出现空目录。如果代码确认依赖“启动即刷新”，就停下来在报告里说明，不要擅自改业务逻辑。

### C3 持续增长（本轮不改代码，只定位）

- 本机连续 60 轮“采集 + 身份校验”：tracemalloc 当前占用在 80–123MB 之间波动，**没有上升趋势**。第 10 轮→第 60 轮的快照差异里，只有盘口缓存（`market_validation_review.py:347-372`）增加约 1MB，而它已经通过 `_bounded_put` 设了上限。所以采集链不是线上 +7MB/分钟增长的来源。
- 来源应该是本机没有复现的其他后台任务（告警、FS、负基差、squeeze、second-level-sampler 等），只能在线上定位。
- 做法：C1、C2 上线、平台期降下来之后，使用 M1-A 已有的 `MEMORY_TRACEMALLOC_SECONDS=1200`，开一次 20 分钟的 tracemalloc 窗口，看第 1 分钟和第 20 分钟快照之间增长最多的代码行，再写 M1-D 修复任务书。tracemalloc 会额外占用 20–40% 内存，需要 C1、C2 先腾出余量。是否开这个窗口，到时候再由用户确认。

## 3 文件范围与测试

- 修改：`backend/app/services/market_validation_review.py`、`backend/app/services/market_identity_catalog.py`、`backend/app/core/config.py`、`backend/app/main.py`（只为传入 C2 配置）
- 新建：`backend/tests/test_market_validation_prepare_copies.py`、`docs/task-handoff-2026-10-25-memory-fix.md`
- 禁止修改：`spread_engine.py`、`collector.py`、告警/Astro/FS 逻辑、前端、compose。

测试（先写测试）：
1. C1 等价性（见上），以及 `model_copy` 调用次数：无冲突时每行在 `prepare` 中被拷贝的次数比改动前少 2 次（用 monkeypatch 计数）。
2. C1 峰值：构造 1 万行合成市场，tracemalloc 测得 `prepare()` 峰值低于改动前实现的 70%。
3. C2：延迟期间不调用 refresh；stop 能立即退出；delay=0 时立即刷新（旧行为）。
4. 回归：`test_market_validation*.py`、`test_market_identity*.py`、`test_collector.py`、`test_collector_pipeline.py`、`test_alert_loop.py`、`test_snapshot_consumers.py`、`test_memory_probe.py`。

## 4 上线验收（Claude 执行，部署需用户批准）

- 探针保持开启，上线后采 1 小时，与基线对比：OOM 次数、启动后 60 秒的跳涨幅度、平台期水平、增长斜率。
- 预期：平台期下降，启动后 3 分钟内被杀的情况消失；OOM 次数明显减少。如果增长斜率不变，则 C3 另起任务。
- 回滚：revert 提交；C2 可单独设 `MARKET_IDENTITY_CATALOG_STARTUP_DELAY_SECONDS=0`。
