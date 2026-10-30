# history insert_many 原子写入（2026-10-30）

## 问题（已由 .worktrees/history-retry 的 79607f6 核查文档复现）
`OpportunityHistoryRepository.insert_many`（backend/app/db/repositories.py:1621）用 executemany 逐行插入。中途某行失败时，前面的行留在共享 aiosqlite 连接的开放事务里，之后会被其他 repository 的 commit 一起提交（例如 SettingsRepository.set_risk_settings），产生半批历史数据。
另外 `HistoryRecorder.record`（backend/app/services/history.py:39-42）在插入已提交之后调用 `_prune`；prune/vacuum 失败会让 record 抛异常，调用方会误以为插入失败。

## 约束
- 连接是共享的，不能 ROLLBACK（会丢掉其他调用方未提交的写入），也不要用 SAVEPOINT（await 之间其他协程的语句会落进保存点）。
- 不加重试，不加唯一键，不改表结构，不改其他 repository。

## 要做
1. insert_many 改为**单条语句**的多行 `INSERT ... VALUES (...),(...)`：SQLite 单条语句失败会整句回滚，一行都不会留下。先在 Python 里把所有参数序列化好（序列化异常在碰数据库之前抛出），然后 execute，再 commit，返回行数，行为和现在一致。
2. 参数上限：30 列 × 行数必须不超过 SQLite 变量上限。核实生产镜像的 sqlite 版本和 SQLITE_MAX_VARIABLE_NUMBER（3.32 起默认 32766）。给 `HistorySettings.keep_top_n`（backend/app/models/settings.py:273）加 `le=1000`；insert_many 收到超过 1000 行时直接 ValueError，不分块（分块会破坏原子性）。确认 config.py 的环境变量路径也经过这个校验，生产当前是 100。
3. `HistoryRecorder.record`：insert 成功后 prune 失败只记一次 `logger.warning(..., exc_info=True)` 然后返回 inserted，不往外抛。insert 失败照常抛出。
4. 测试（backend/tests 下与 history 相关的现有文件里追加）：
   - 用触发器让第 N 行插入失败（例如 BEFORE INSERT 触发器 RAISE(ABORT)，参考 79607f6 的故障注入方式），断言 insert_many 抛错后表里 0 行；再由另一个 repository commit 一次，表里仍是 0 行。
   - 1000 行成功；1001 行抛 ValueError 且表里 0 行；keep_top_n=1001 校验失败。
   - prune 抛异常时 record 返回插入行数、行已提交、记录了 warning。
   - 现有 history 测试全部保持通过、不放宽断言。

## 验收
- 只改：backend/app/db/repositories.py、backend/app/services/history.py、backend/app/models/settings.py、相关测试文件、本任务书。
- `cd backend && python -m pytest -q -p no:cacheprovider` 全量通过，日志里保留命令和汇总行。
- 最后 commit 到当前分支（codex/history-atomic-insert），提交信息 `fix: make history batch insert atomic`。

## 实施与验收记录（2026-10-30）

### 范围与实现

- 工作目录：`.worktrees/history-atomic`；分支：`codex/history-atomic-insert`；基线：`ad8ca1046d108cfa55f2a00ab8ce46117bdc40df`。开始时工作区和暂存区干净，无未跟踪文件。
- `OpportunityHistoryRepository.insert_many` 先检查 1000 行上限并完整序列化，再使用一条多行 `INSERT ... VALUES` 和一次 commit。保留空批次返回 0、成功返回行数的行为，不分块、不重试、不加 rollback/savepoint。复核确认 30 个插入列和序列化表达式与基线一致。
- `HistorySettings.keep_top_n` 加入 `le=1000`。`config.py` 的 `HISTORY_KEEP_TOP_N` 经 `get_settings()` 进入 `Settings.history_keep_top_n`，随后 `Settings.history_settings` 构造 `HistorySettings` 执行校验；`main.py` 创建 recorder 时访问此属性。环境变量 1000 可用、1001 拒绝的路径已有测试，无须修改 config/main。
- `OpportunityHistoryRecorder.record` 只捕获插入返回后的 `_prune` 异常，记录一次带异常堆栈的 warning 后返回 inserted；插入异常仍传播，失败时采样时间不前移。
- 仅修改三个获准源文件、现有 `backend/tests/test_history.py` 和本任务书；不修改 schema、其他 repository 或既有测试断言。本节同时保留后续交接所需的验证记录。

### 测试证据

在 Windows 原生 PowerShell / Python 3.12.10 下执行，全程未运行 WSL。新增 15 个测试实例，覆盖：

- 第 1/2/3 行触发 `RAISE(ABORT)`，分别使用普通连接和生产同款 `recover_stale_snapshots=True` 连接。失败后与其他 repository 提交后历史表均为 0 行，其他调用方原有的未提交写入得到保留。
- 1000 行成功且提交；1001 行在访问数据库前抛 ValueError；序列化失败在任何 SQL 执行前抛出。
- 模型和环境变量入口的上限校验。
- prune/vacuum 分别失败时返回插入行数，通过独立连接确认新行已经提交，并验证仅一次 warning 且含原始异常；插入失败仍传播且不调用维护。

先补测试、尚未修改实现时，在 `backend` 下执行：

```text
python -m pytest -q -p no:cacheprovider tests/test_history.py tests/test_history_maintenance.py
9 failed, 17 passed in 4.78s
```

实现后，在 `backend` 下执行相关回归，退出码 0：

```text
python -m pytest -q -p no:cacheprovider tests/test_history.py tests/test_history_maintenance.py tests/test_snapshot_consumers.py
53 passed in 16.32s
```

后端全量命令与汇总行（在 `backend` 下执行，退出码 0）：

```text
python -m pytest -q -p no:cacheprovider
2279 passed, 6 deselected, 12 warnings in 195.37s (0:03:15)
```

6 个测试按现有 pytest 配置的 `not network and not benchmark` 条件排除。12 条 warning 均来自未修改的 API/index-components 测试触发的 `datetime.utcnow()` 弃用提示。`git diff --check` 通过；提交前仅有上述 5 个允许文件的改动，无未跟踪文件。

### SQLite 兼容性核实

通过受控 SSH 配置只读检查运行中的生产后端容器，Python 探针使用 `python -B -`，只创建 `:memory:` 数据库，未连接生产数据文件：

- 生产后端镜像：`ghcr.io/wubin19920612/taoli1-backend:sha-0915fcbb0a701d0f044c3be809deee3017326699`，服务器代码 HEAD 同为此 SHA。
- 生产 Python：3.12.16；`sqlite3.sqlite_version`：**3.46.1**；`PRAGMA compile_options`：**MAX_VARIABLE_NUMBER=250000**；`Connection.getlimit(SQLITE_LIMIT_VARIABLE_NUMBER)`：**250000**；实际加载的 `history_keep_top_n`：**100**。
- 本地 Python：3.12.10；SQLite：**3.49.1**；编译参数和运行时变量上限均为 **32766**。
- 30 列 × 1000 行 = **30000** 个变量，低于生产和本地实际限制。采用实测编译/运行时值，不以 SQLite 默认上限代替核实。

交付范围为任务书要求的当前分支提交；生产仅执行上述只读兼容性核实，未推送或部署本次改动。后续 retry 仍需另开任务评审与验证，本次没有增加重试。
