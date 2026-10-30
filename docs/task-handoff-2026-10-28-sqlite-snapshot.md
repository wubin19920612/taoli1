# SQLite 结果集物化修复交接（2026-10-28）

## 范围与基线

- 任务：按 `docs/sqlite-snapshot-fix-spec-2026-10-28.md` 消除 aiosqlite 的 execute 与 fetch 两次排队之间，活跃读语句导致的 SQLITE_BUSY_SNAPSHOT（517）窗口。
- 工作树：`.worktrees/sqlite-snapshot-fix`；分支：`codex/sqlite-snapshot-fix`。
- 开始时 HEAD：`7b9e8b5`（任务书与复现脚本），工作区干净；任务书的生产基线 `74c8f23` 是当前分支祖先。生产版本未在本任务中远程复核。
- 仅修改 `backend/app/db/database.py`，新建 `backend/tests/test_database_materialized_cursor.py` 和本交接文档。任务书、复现脚本、其他源码及配置均未修改。
- 用户明确要求本地 Git 提交，不 push，不运行 WSL。

## 实现与业务边界

- `_MaterializedCursor` 在 aiosqlite 工作线程执行 execute 的同一调用内读完结果，并在 finally 中关闭原游标，包含读取异常时的关闭。
- 保存 description、rowcount、lastrowid、arraysize；rowcount 在 fetchall 后读取，确保 INSERT RETURNING 的最终受影响行数正确。
- 支持逐行读取、剩余结果读取、批量读取、同步迭代及关闭。用 deque 避免逐行读取时的列表头部搬移；关闭后读取抛出 ProgrammingError。
- `_MaterializingConnection.execute()` 按 description 是否为 None 判断结果集，覆盖 SELECT、PRAGMA、RETURNING；无结果集的 DML 保留原 sqlite3.Cursor，executemany 不变。
- 默认工厂及开启恢复的工厂均使用物化；`_SnapshotRecoveringConnection` 的 517 回滚、告警及不重放逻辑未修改。
- 显式 BEGIN 持有的事务快照不会因游标关闭而释放；已有快照恢复回归仍验证这条边界。此修复不承诺消除所有写锁竞争。
- 已读取本机 aiosqlite 0.22.1 Cursor 与 execute_fetchall 实现，并测试 async with、async for（跨批次）、execute_fetchall、Row 按列名访问等接口。
- 检查 backend/app 调用点，未发现 `.cursor()`、游标复用 execute、fetchmany 或游标异步迭代用法；现有 fetchone 查询为键查询、LIMIT 1 或聚合/单行 PRAGMA，未发现无界多行查询仅取首行的用法。新增包装不扩展到 cursor.execute、cursor.row_factory 等未要求接口。

## TDD 与验证结果

环境：Windows 原生 Python 3.12.10、SQLite 3.49.1、aiosqlite 0.22.1。测试使用临时 WAL 文件库及项目离线网络防护，未使用 WSL。

所有 pytest 命令均在 `backend` 目录运行：

```powershell
python -m pytest -q --basetemp=../.pytest-tmp/x tests/test_database_materialized_cursor.py tests/test_database_snapshot_recovery.py
python -m pytest -q --basetemp=../.pytest-tmp/x
```

1. 先写测试、未修改 database.py 时：**10 failed, 6 passed**。普通 sqlite3.Connection 工厂对照通过“错误次数 > 0”断言；旧默认及恢复工厂各 200 轮均发生 **200 次 517**，零错误断言失败；已有 5 项快照恢复测试全部通过。RETURNING 在取结果前 commit 也按预期失败。
2. 实现修复后：新测试与快照恢复合计 **16 passed**。普通工厂继续报错，两种修复工厂各 **200 轮、0 次错误**。通过事件固定 reader → 另一连接提交 → writer → fetch 的交错顺序，避免依赖线程调度概率。
3. 后端全量：**2054 passed, 4 deselected, 12 warnings，149.63 秒**。4 项由默认 network/benchmark 标记配置排除；12 项是 test_api/test_index_components 路径中的 datetime.utcnow 弃用警告。
4. 静态检查发现测试闭包循环变量 B023 告警，已显式绑定每轮事件。`python -m ruff check app/db/database.py tests/test_database_materialized_cursor.py` 最终 **All checks passed**；绑定调整后重跑上述定向测试，仍为 **16 passed**。
5. `git diff --check` 通过。database.py 为 58 行新增、2 行删除；最终提交范围限定为上述三个文件。

语义测试另覆盖：空结果、读尽返回值、fetchmany/arraysize、同步迭代与 fetch 混用、SELECT 元数据、DML rowcount/lastrowid、RETURNING 读前提交、重复关闭、关闭后读取报错、构造期间读取失败关闭原游标。fetchmany 的非正数行为按本机 Python 3.12 sqlite3 对照验证。

## 未做事项与下一步

- 未 push、未连接生产服务器、未备份线上数据库、未部署；没有新的线上验收结论。提交信息使用用户指定标题及 Co-Authored-By 尾注。
- 未修改 busy_timeout、连接拆分、import_catalog 事务范围、history generation 重放或公告写队列；未运行前端测试与构建。
- 未测生产内存。物化提前读取全部结果，deque 与 list 转换短暂增加引用容器开销；不能将本地功能测试解释为生产峰值内存不变的证据。
- `.pytest-tmp/` 为本次指定 pytest 命令生成的未跟踪测试产物，保留且不提交；开始时没有其他未跟踪文件。不修改 `.gitignore` 或 `docs/next-task.md`，遵守三文件限制。
- 后续由 Claude 完成任务书要求的验收与复核；部署另行批准后执行现有备份/部署流程，并对比上线前 60 分钟及上线后 +1h、+4h 的 locked、快照回滚、history-recorder 与公告 OperationalError 次数，同时检查内存。生产观察应在后续任务继续。
- 若需回滚，用 revert 回滚本次修复提交，保留本地测试证据。
