# SQLite `database is locked`（BUSY_SNAPSHOT）修复：任务书（Claude，2026-10-28，待用户确认）

基线：`74c8f23`（当前生产版本）。worktree `.worktrees/sqlite-snapshot-fix`，分支 `codex/sqlite-snapshot-fix`。

## 1 线上证据（10-28，只读，容器 74c8f23 自 10-27 19:59 CST 启动）

- `sqlite3.OperationalError: database is locked` 每小时约 115–190 次，从启动起一直持续、分布均匀，没有伴随 OOM。受影响的写入：公告入库（`AnnouncementRepository.create_if_new`）、`set_provider_state`（公告轮询证据和 Hyperliquid 状态）、`history.record`（`history-recorder` 每小时失败若干次）、trade availability、index-components。
- 6 小时内 `Rolled back stale SQLite snapshot` 警告 262 次；941 条 locked 中有 **744 条（约 80%）和快照回滚发生在同一秒**，也就是说主要是 `SQLITE_BUSY_SNAPSHOT`（517），不是 5 秒 busy_timeout 超时。触发回滚最多的是 `announcement_polling.poll_source`（193 次）。
- radar.db 只有后端进程打开。lifespan（`backend/app/main.py:785–910`）共打开 4 个连接：主连接 `db`（被几十个 Repository 共享）、`identity_db`、FsRepository 连接、`preset_db`。

## 2 根因（本地已复现）

aiosqlite 的 `await db.execute(SELECT)` 和之后的 `await cursor.fetchall()/fetchone()` 是**两次独立的排队操作**。两次之间，语句处于活跃状态，主连接持有读快照。这段时间里，如果别的连接提交了写入，而主连接上另一个协程排到了写操作，那么这次写入就要从过期的读快照升级为写事务，SQLite 会**立即**返回 517 `database is locked`。

- 复现脚本：`docs/sqlite-snapshot-repro-2026-10-28.py`。同一连接上 reader（execute → sleep(0) → fetchall）、另一个连接提交、writer 三者并发：**现有写法 200 轮报错 193 次**；原型修复（在连接工厂的 `execute` 里，对有结果集的语句当场 `fetchall()` 并关闭游标）后为 **0 次**。
- 只读 Codex 审计（`output/lock-audit-codex-20261028.md`，主检出目录）确认：代码中没有“多行查询只 fetchone、中途 break、fetchmany”的情况，问题出在两次 await 之间的间隙，所有读取路径都受影响，没有某一个调用方是唯一的罪魁祸首。所以要做集中修复，不能逐个调用方修补。
- `import_catalog`（`market_identity_repository.py:191`，在 `BEGIN IMMEDIATE` 中持锁做全表解析）每天只运行一次，解释不了持续的每小时报错。**这次不改**，列为后续事项。

## 3 设计（最小改动，集中在 `backend/app/db/database.py`）

1. 新增 `_MaterializedCursor`：在工作线程内构造，构造时调用 `cursor.fetchall()` 并 `close()` 原游标；保留 `description`、`rowcount`、`lastrowid`、`arraysize`，提供 `fetchone()`、`fetchall()`、`fetchmany(size)`、`close()`、`__iter__`，语义与 sqlite3.Cursor 相同（读完返回 None 或 []）。
2. 新增 `_MaterializingConnection(sqlite3.Connection)`：`execute()` 调用 `super().execute()`，`cursor.description is not None`（有结果集，包括 SELECT、PRAGMA、`RETURNING`）时返回 `_MaterializedCursor`，否则返回原游标。`executemany` 不变。
3. `_SnapshotRecoveringConnection` 改为继承 `_MaterializingConnection`，517 恢复逻辑不变。`connect_database()` 在不需要恢复快照时，factory 改用 `_MaterializingConnection`，**4 个连接全部生效**。
4. 需要核对 aiosqlite 0.22.1 `Cursor` 包装层的兼容性：`fetchone/fetchall/fetchmany/close/rowcount/lastrowid/description/arraysize`、`async with db.execute(...) as cur`、`execute_fetchall`、`async for`。已确认 `backend/app` 中没有 `.cursor()`、`cursor.execute(...)`、`fetchmany`、`async for row in cursor` 的用法。
5. 不做：busy_timeout、连接拆分、`import_catalog` 事务缩短、history 失败的 generation 重放（`snapshot_consumers.py:98–100`）、公告的写队列。

## 4 风险

- 内存：结果集会在 execute 时一次性读完。现有代码对这些查询本来就会 `fetchall()`，峰值不变；需要确认没有对大表只取前几行的用法（Codex 审计说没有）。
- 语义：`rowcount` 对 SELECT 是 -1，DML 保持原游标，不受影响。`lastrowid` 只对 DML 有意义。`INSERT ... RETURNING` 会被读完，语句当场结束，这样更正确。

## 5 文件范围与测试

- 修改：`backend/app/db/database.py`
- 新建：`backend/tests/test_database_materialized_cursor.py`、`docs/task-handoff-2026-10-28-sqlite-snapshot.md`
- 禁止修改：其他所有文件（包括 `docs/sqlite-snapshot-repro-2026-10-28.py`）。

测试先写（不联网，用 tmp_path 中的 WAL 文件库）：
1. **并发复现**：照搬复现脚本，用 `connect_database()` 建立两个连接，做 200 轮 reader / 另一连接提交 / writer 交错：修复后报错 0 次。同时用普通 `sqlite3.Connection` 工厂做对照，证明这个测试在旧实现下会失败（报错次数 > 0）。
2. 游标语义：`fetchone` 逐行读取，读完后返回 None；`fetchall` 返回剩余行；`fetchmany(n)`；`description`；`aiosqlite.Row` 按列名取值；`async with db.execute() as cur`；`execute_fetchall`；空结果；DML 的 `rowcount`/`lastrowid` 与原来一致；`INSERT ... RETURNING` 能取到返回行。
3. 517 恢复回归：`tests/test_database_snapshot_recovery.py` 必须通过，`recover_stale_snapshots=True` 的回滚警告逻辑保持不变。
4. 后端全量：`--basetemp=../.pytest-tmp/x`（先 `mkdir -p .pytest-tmp`）。

## 6 上线与验收（部署需用户批准）

- 部署前，部署脚本先记录上线前 60 分钟的 `database is locked` 和 `Rolled back stale SQLite snapshot` 次数（基线约 150/h 和约 44/h）。上线后在 +1h、+4h 各统计一次：预期**两者都接近 0**，`history-recorder failed` 为 0，公告轮询的 OperationalError 为 0。
- 内存：同时对比 616MB 平台期，不应上升。
- 回滚：revert 这个提交。

## 7 派发

- Codex：gpt-6-astra high（数据库层，影响所有连接）。不要跑 WSL。
- 验收：Claude 跑范围检查和全量测试；代码审阅交给只读 Codex，Claude 通读 `database.py` 的改动（不到 60 行）。
