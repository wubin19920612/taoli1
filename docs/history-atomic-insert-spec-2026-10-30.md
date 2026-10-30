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
