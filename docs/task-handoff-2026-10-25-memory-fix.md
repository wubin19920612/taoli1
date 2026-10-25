# M1-C 内存定点修复交接（2026-10-25）

## 范围和状态

- 规格：`docs/m1c-memory-fix-spec-2026-10-25.md`，仅 C1、C2；C3 未开展。
- 原分支：`codex/memory-fix`；基线和当前 HEAD：`f7943243a7380091f7ef77f394b83f012bbc0160`。未切换或新建分支。
- 本任务仅本地代码、测试、本文档。**未暂存、未提交、未推送、未 SSH、未备份线上数据库、未部署。** 未验证当前线上运行状态。
- 无新增第三方依赖。所有 pytest 使用 `s1_offline_guard`，新增异步网络入口均为 fake/AsyncMock。

## 改动文件和实现入口

| 文件 | 实现位置与用途 |
| --- | --- |
| `backend/app/services/market_validation_review.py` | 第 131 行 `_annotate_base`；第 135 行公开 `annotate`；第 177 行 `prepare`、第 207–212 行条件拷贝 |
| `backend/app/services/market_identity_catalog.py` | 第 473 行 `run(stop, startup_delay_seconds=300)`；先等待 stop 或延迟超时，再进入原每日循环 |
| `backend/app/core/config.py` | 第 23 行配置默认值；第 76 行有限非负校验；第 187 行环境变量解析 |
| `backend/app/main.py` | 第 980–988 行仅向目录任务传入 C2 配置 |
| `backend/tests/test_market_validation_prepare_copies.py` | 新增 46 项测试；冻结旧实现、等价性、拷贝计数、峰值、C2 时序与持久化加载 |
| `docs/task-handoff-2026-10-25-memory-fix.md` | 本文档 |

C1 在批量准备时跳过使用上一轮冲突集合的中间拷贝，算出本轮集合后，仅对 `identity_collision` 需要改变的行执行最终拷贝。公开 `annotate()` 仍按当前 `collision_symbols` 设置该字段。没有直接修改输入对象。

从原 `if self.collision_symbols:` 开始到 `prepare()` 返回的源码与基线逐行完全一致，包括 `combinations`、`pair_allowed`、失效机会、FX 刷新、分组、候选排序和后台复核调度。Hyperliquid DEX/raw symbol、报价、资金费率和交易判断规则均未改变。

## C1 等价性证据

测试内 `LegacyValidationService` 的 `annotate()` 和 `prepare()` 原样复制自 f794324；通过 `git show f794324:backend/app/services/market_validation_review.py` 加 AST 定位，核对两段函数文本完全一致，参照未随生产代码修改。

- 固定时间，在 shadow/enforce 两模式各运行 12 个场景，每个场景连续执行两轮。
- 场景：无冲突、新资产冲突、旧冲突消失、资产种类冲突、新鲜缓存盘口、过期缓存盘口、FX、FX 加缓存盘口、FX 后台刷新、非法 DEX/raw 配对、未绑定市场、reference 报价后台复核。
- 逐行比较完整 `model_dump()`，同时比较 `collision_symbols`、`discovered`、发现入库调用、`invalidated_ids`、机会失效调用、复核尝试记录、任务键、复核/FX 调用，验证输入不被修改、发现及失效操作跨轮去重。
- 公开 `annotate()` 覆盖有/无当前冲突以及默认时间、有时区、无时区时间。
- 对冲突配对及后续整个函数尾部额外做源码一致性断言。
- monkeypatch `MarketSnapshot.model_copy` 按精确 MarketKey 计数：无冲突的普通、缓存盘口、FX、FX 加缓存四场景，每行相较旧版少两次拷贝。普通行由 3 次降为 1 次。

### 峰值测量

`test_prepare_10000_markets_peak_below_seventy_percent` 使用 10,000 个唯一 symbol/raw symbol 的合成未绑定市场，无冲突、无盘口缓存、无 FX。预填 discovered 模拟稳定采集轮次，排除输入构建和数据库操作；旧版和新版分别在 GC 后启动 tracemalloc，测量整个 `prepare()`，结果列表保留到取峰值结束。

| 测量 | 旧实现 | 新实现 |
| --- | ---: | ---: |
| Python 分配峰值（字节） | 58,101,729 | 25,673,035 |
| 十进制 MB（1 MB = 1,000,000 字节） | 58.102 | 25.673 |

新版是旧版的 **44.1864%**，下降 **55.8136%**（32,428,694 字节），满足低于旧版 70% 的门槛。RED 阶段旧版对旧版均为 58,101,729 字节，比例 100%，门槛确实失败。

这是合成负载的 Python 分配峰值，不是生产 RSS 或全采集链峰值；没有测量 C2 的实际线上节省量，也没有据此宣称 OOM 已解决。

## C2 启动依赖结论

允许实施 C2：没有发现业务依赖“启动即官方刷新”。

1. `main.py:822–826` 构建数据库 repository 后，先 `await market_validation.initialize(seed_reviewed=start_collector)`，随后才创建官方目录对象；采集和官方目录后台任务在后面启动。
2. `MarketValidationService.initialize()` 导入本地 reviewed seeds，然后调用自身 `refresh()`；后者通过 `repository.settings()`、`repository.list_bindings()` 从数据库加载到 registry。这是本地加载，不是官方目录网络刷新。
3. `MarketIdentityRepository.list_bindings()` 读取 `market_identity_bindings.payload`。官方目录的 `refresh()` 才会发网络请求、导入新绑定并在有更新时重载 registry。
4. 离线测试先写入临时 SQLite 并关闭，再重开并执行生产同样的 `initialize(seed_reviewed=True)`；断言已有非种子绑定保持 verified。用虚拟超时检查整个首次 300 秒等待阶段不刷新、已有注释结果不变，随后才运行 fake refresh。

环境变量：`MARKET_IDENTITY_CATALOG_STARTUP_DELAY_SECONDS`，默认 `300`；设为 `0` 恢复立即刷新。拒绝负数及 NaN/Infinity，支持有限非负小数。延迟期间使用 `asyncio.wait_for(stop.wait(), timeout=...)`，实际 Event 测试确认 stop 后 1 秒测试上限内退出且不刷新。超时后首次刷新，之后继续每 86400 秒；零延迟测试确认立即刷新。

边界：保留的是初始化加载的已有数据，不保证其尚未过期；原有证据有效期检查不变。全新/空数据库仍只有原本的本地 reviewed seeds，新增官方补全延迟最多 300 秒。延迟期间官方目录状态仍为原有 `not_started`；未新增状态或改变业务逻辑。

## 测试命令与结果

均在 Windows PowerShell 的 `backend` 目录执行。每次独立 pytest 使用新的 basetemp 字母，不复用、不清理旧目录。命令模板（下表对应实际字母及文件）：

```powershell
$env:PYTHONPATH='tests'
python -m pytest -q -p no:cacheprovider --basetemp=.pytest_m1c_<字母> -p s1_offline_guard tests/<文件>
```

| 字母 | 文件/选择 | 结果 |
| --- | --- | --- |
| a | `test_market_validation_prepare_copies.py`（改生产代码前） | 31 passed / 15 failed；包含首次持久化 fixture 与已有 PRL 种子冲突，随后改用独立合成市场 |
| b | 同文件，`-k database_bindings`（仍为旧生产代码） | 1 failed / 45 deselected；已验证重开数据库加载成功，失败只因旧版缺少 300 秒等待 |
| c | `test_market_validation_prepare_copies.py`，额外 `-rP` 输出峰值 | **46 passed**，3.33s |
| d | `test_market_validation_astro.py` | **4 passed** |
| e | `test_market_validation_review.py` | **4 passed** |
| f | `test_market_identity_catalog.py` | **4 passed** |
| g | `test_market_identity_repository.py` | **3 passed** |
| h | `test_market_identity_routes.py` | **2 passed** |
| i | `test_market_identity_validation.py` | **10 passed** |
| j | `test_collector.py` | **24 passed** |
| k | `test_collector_pipeline.py` | **39 passed** |
| l | `test_alert_loop.py` | **21 passed** |
| m | `test_snapshot_consumers.py` | **27 passed** |
| n | `test_memory_probe.py` | **39 passed** |

最终新增测试 **46/46**、全部指定回归 **177/177**，合计 **223 项通过**；回归循环退出码 0，`FAILED_FILES` 为空，没有触发真实网络请求拦截。

`python -c "import app.main"`：退出码 0。`git diff --check`：通过；仅有现有 Git CRLF 转换提示。未运行 `tests/test_api.py` 整文件，未调用 WSL。

## 工作区保护与下一步

两个用户 WIP 文件没有修改、暂存或还原，前后 SHA256 一致：

- `frontend/src/pages/PairMonitorPage.tsx`：`7C1965B293123C1FCBDACD3F3B80022142C6B60E63C47BEABC87CA49C4ADADF5`
- `frontend/tests/PairMonitorPage.test.tsx`：`C7FA3B2BBEF5D87E43E0CFD0BAF69927618EE760B08984C41AA89E254B1848DE`

原有未跟踪 `.worktrees/`、`.pytest_*`、`output/` 中的数据库/日志/备份/截图/脚本、规格文档及其他交接文档均保留。新增测试和本文档保持未跟踪；本次 pytest 产生的临时目录及 SQLite 也保留。未使用 reset、checkout 还原、stash、force push 或删除命令。

与规格无功能或文件范围偏差。生产备份、提交、推送、部署与一小时线上验收按本次指令留给后续任务；不以本地测试代替生产验收。下一步由接手方审阅这六个文件，按授权范围交付并观察 OOM/启动峰值；若需调查持续增长，另开 C3/M1-D 任务。

## Claude 验收（2026-10-25）

- 离线测试：新增 + 回归 12 个文件，223 passed；`import app.main` 通过。
- 共享对象检查：C1 在 `identity_collision` 不变时沿用原对象，`prepare` 的输出可能与输入（发布为 `all_markets`）共用对象。已搜索 `app/` 下所有对 `MarketSnapshot` 字段的赋值，结果只有 FS 自己的行对象（`fs_monitor.py`），没有原地修改市场快照的地方，所以安全。
- 本机真实网络复测（`scratchpad/m1_stage_local.py`，4 轮）：`validation.prepare` 峰值从 57–59MB 降到 33.9MB；**整轮峰值仍约 104MB（修复前 101–106MB），没有下降**。整轮峰值出现在 prepare 之外（推测是轮询结果与新旧两代快照同时存活），而且本机数据噪声大（机会数 859 对 4058）。线上效果以探针数据为准。

## 线上结果（2026-10-25，Claude）

- 11:32 CST 上线 `f38ced1`（CI 40158318571 success，备份 integrity ok：主库 sha256 484d4a8a…，路线库 38ac4dca…），探针开启，`MALLOC_ARENA_MAX` 未设置。
- 1 小时（11:34–12:34）：OOM **5 次**（基线 4 次）；平均存活约 10 分钟（基线约 15 分钟）；启动后 60 秒的跳涨 +192~198MB（基线 +229~237MB，C2 减少约 35MB）；平台期和 +7MB/分钟的斜率不变。
- 结论：C1/C2 没有减少 OOM。整轮峰值来自身份校验以外的部分，持续增长来源仍未定位。C1/C2 保留（无副作用，启动跳涨略降）。
- 主机内存（12:35）：总 1931MB，已用 1249MB，其中 backend 680MB、其他进程约 570MB；可用 497MB，无 swap。
