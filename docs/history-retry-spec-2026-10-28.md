# history-recorder 失败重试：任务书（Claude，2026-10-28，用户已同意开发）

基线：`dbd5bb8`（当前生产，M2-4b）。worktree `.worktrees/history-retry`，分支 `codex/history-retry`。

## 1 现状
- `SnapshotConsumer.run`（`backend/app/services/snapshot_consumers.py:83` 起）：handler 抛异常后计数加 1、打 exception 日志，然后**照样**把 `_processed_generation` 推进到这一代（注释 "A failed generation is consumed too"）。所以 history-recorder 一次写入失败，这一代的机会历史就永久丢失。
- 锁修复（4ad88b7）之前每小时约 14 次 `history-recorder failed`，之后是 0；现在剩下的风险是偶发的 IO 或锁错误。
- 消费方只有两个（`main.py:1053`）：`history-recorder`（`collector.record_history` → `history_recorder.record(...)`）和 `index-components`。

## 2 设计
1. `SnapshotConsumer.__init__` 新增参数 `retry_failed: int = 0` 和 `retry_delay: float = 2.0`。默认值保持旧行为（index-components 不变）。
2. handler 失败、并且 `retry_failed > 0` 时：等待 `retry_delay` 秒（必须能被 stop_event 打断），然后**只有在 slot 里还没有更新的一代**时，才对同一个 snapshot 重试，最多重试 `retry_failed` 次。如果已经有更新的一代，就放弃重试（最新快照语义优先），并打一条 warning 说明放弃了哪一代。重试全部失败后，和现在一样推进 generation。
3. 统计：`stats()` 增加 `retried` 和 `retry_recovered` 两个计数；`failed` 仍然只按“最终失败”计数。`beat(...)` 只按最终结果调用。
4. `main.py:1053` 给 history-recorder 传 `retry_failed=1`。
5. **前提核查（必须做）**：读 `history_recorder.record` 和它的 repository 写入路径，确认一次 record 是**原子的**（失败时没有提交过部分行）或者是**幂等的**（重复写入不会产生重复行，例如唯一键加 upsert）。如果两者都不满足，**不要**开启 retry，在交接文档里写明原因和需要怎么改，然后停下。
6. 不做：改 slot 语义、改 index-components、改 history 表结构、改采集频率。

## 3 文件范围与测试
- 修改：`backend/app/services/snapshot_consumers.py`、`backend/app/main.py`（只改 1053 行附近的构造参数）
- 新建：`backend/tests/test_snapshot_consumer_retry.py`、`docs/task-handoff-2026-10-28-history-retry.md`
- 禁止修改其他文件。

测试先写（不联网，时间可注入或用很小的 retry_delay）：
1. handler 第一次失败、第二次成功：同一 generation 被处理两次，`processed=1, failed=0, retried=1, retry_recovered=1`。
2. 重试等待期间有更新的一代发布：放弃重试，直接处理新的一代，`failed=1`。
3. 一直失败：重试 `retry_failed` 次后推进，`failed=1`，不会死循环。
4. 等待期间 stop_event 被设置：及时退出。
5. `retry_failed=0`：行为和旧实现完全相同（复用或保留现有测试）。
6. CancelledError 的语义不变：不推进、向上传播。
7. 回归：`tests/test_snapshot_*.py`、`tests/test_history*.py`，以及后端全量（在 backend 下 `mkdir -p ../.pytest-tmp`，`python -m pytest -q --basetemp=../.pytest-tmp/x`）。

## 4 派发
- Codex：gpt-6.1-sol xhigh。不要跑 WSL，不要联网。上线另行申请批准。
