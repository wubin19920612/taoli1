# history-recorder 重试交接（2026-10-28）

## 结论与范围

**§2-5 前提不满足，停止 retry 实现，未开启重试。** 本次只新增此交接文档；没有修改 `snapshot_consumers.py`、`main.py`、repository、表结构或现有测试，也没有新增 retry 行为测试。

目标原为按 `docs/history-retry-spec-2026-10-28.md` 给 history-recorder 增加一次失败重试。用户明确要求先核查原子性或幂等性，两者均不满足时写明原因并停下。本次先执行离线故障注入，确认两者均不成立，因此没有进入测试先行的功能实现阶段。仍按用户验收要求运行现有 snapshot/history 回归和后端全量测试。

- worktree：`.worktrees/history-retry`。
- 分支：`codex/history-retry`。
- 开始时 HEAD：`f3e01f5a0b6bd8b8bacbdd9502315b8889f46dc7`（任务书提交）。
- 规格标注的生产基线：`dbd5bb8`；它也是开始时 HEAD 的父提交。线上状态未联网复核。
- 开始时工作区和暂存区干净，没有已有未跟踪文件。
- 全程使用 Windows 原生 PowerShell/Python 3.12.10；未运行 WSL，未联网。

## 前提核查证据

调用链与入口（均为本次检查的基线代码）：

1. `backend/app/main.py:1053` 创建 history-recorder consumer，尚未传入任何 retry 参数。
2. `backend/app/services/collector.py:301` 的 `record_history()` 将同一快照的 opportunities 和 `published_at` 传给 recorder。
3. `backend/app/services/history.py:20` 的 `record()` 选行后调用 `insert_many()`；只有该调用成功返回才更新 `_last_sample_at`，随后执行 `_prune()`。
4. `backend/app/db/repositories.py:1621` 的 `insert_many()` 执行普通 `INSERT` 的 `executemany()`，随后单独 `commit()`，没有失败回滚。
5. `backend/app/db/schema.py:186` 的 history 表只有自增行 ID；`:219` 起的业务索引均不是唯一索引，插入没有 upsert，因此同一机会、同一时间可重复落行。
6. `backend/app/main.py:792` 创建的共享连接启用了 `recover_stale_snapshots=True`。`backend/app/db/database.py` 的恢复仅针对 `SQLITE_BUSY_SNAPSHOT`，不保证任意批量插入失败的回滚。
7. `backend/app/main.py:825` 的 settings repository 和 `:865` 的 history repository 使用同一 `db`。其他 repository 可使用该连接并提交，例如 `SettingsRepository.set_risk_settings()`（`backend/app/db/repositories.py:1342`）。它可能连带提交前一次失败留下的部分历史行。

原子性不成立：批量插入第二行失败后，第一行留在开放事务中；之后同连接的其他 repository 提交可将这部分行落库。

幂等性不成立：上述失败发生在 `_last_sample_at` 更新前，重放同一快照不会被采样间隔拦截；普通 INSERT 和非唯一索引也不能去重，第一行会再次插入。

另外，`record()` 在插入提交之后仍会执行可能失败的 `_prune()`，因此不能把整个 `record()` 当作单一原子事务。此类异常发生时采样时钟已经前移；重放可能直接跳过，不能等同于完整恢复。

### 离线故障注入复现

在 `backend` 目录，通过 PowerShell here-string 将下面代码传给 `python -` 执行。只用内存数据库，使用真实 schema、repository、recorder 和生产同款连接恢复选项；触发器仅存在于内存测试库。没有修改应用源文件或表结构文件。

```python
import asyncio
import sqlite3
import sys
from datetime import UTC, datetime

sys.path.insert(0, "tests")
from test_history import opportunity
from app.db.database import connect_database
from app.db.repositories import OpportunityHistoryRepository, SettingsRepository
from app.db.schema import initialize_schema
from app.models.settings import HistorySettings, RiskSettings
from app.services.history import OpportunityHistoryRecorder


async def counts(db):
    async with db.execute(
        "SELECT symbol, COUNT(*) FROM opportunity_history GROUP BY symbol ORDER BY symbol"
    ) as cursor:
        return {row[0]: row[1] for row in await cursor.fetchall()}


async def main():
    db = await connect_database(":memory:", recover_stale_snapshots=True)
    try:
        await initialize_schema(db)
        repo = OpportunityHistoryRepository(db)
        recorder = OpportunityHistoryRecorder(repo, HistorySettings(enabled=True))
        items = [opportunity("FIRSTUSDT", 2.0), opportunity("SECONDUSDT", 1.0)]
        now = datetime(2026, 10, 28, tzinfo=UTC)
        await db.execute("""CREATE TEMP TRIGGER fail_second_history_row
            BEFORE INSERT ON opportunity_history WHEN NEW.symbol = 'SECONDUSDT'
            BEGIN SELECT RAISE(ABORT, 'injected second-row failure'); END""")
        await db.commit()
        try:
            await recorder.record(items, now=now)
        except sqlite3.IntegrityError as exc:
            assert str(exc) == "injected second-row failure"
        else:
            raise AssertionError("Fault injection did not fail")
        assert recorder._last_sample_at is None
        assert db.in_transaction
        assert await counts(db) == {"FIRSTUSDT": 1}
        await SettingsRepository(db).set_risk_settings(RiskSettings())
        assert not db.in_transaction
        assert await counts(db) == {"FIRSTUSDT": 1}
        await db.execute("DROP TRIGGER fail_second_history_row")
        await db.commit()
        assert await recorder.record(items, now=now) == 2
        assert await counts(db) == {"FIRSTUSDT": 2, "SECONDUSDT": 1}
        async with db.execute("PRAGMA index_list(opportunity_history)") as cursor:
            indexes = await cursor.fetchall()
        assert indexes and all(row["unique"] == 0 for row in indexes)
    finally:
        await db.close()


asyncio.run(main())
```

实际执行退出码为 0，所有断言通过，观测输出为：

```text
AFTER_FAILED_RECORD: FIRSTUSDT=1, in_transaction=True, last_sample_at=None
AFTER_UNRELATED_REPOSITORY_COMMIT: FIRSTUSDT=1, in_transaction=False
AFTER_SAME_SNAPSHOT_REPLAY: FIRSTUSDT=2, SECONDUSDT=1
PREREQUISITE: FAILED (partial writes can commit; replay duplicates rows)
```

## 验证

在 `backend` 下先执行 `New-Item -ItemType Directory -Path ../.pytest-tmp -Force`，实现用户要求的 `mkdir -p ../.pytest-tmp`。

本分支匹配 `tests/test_snapshot_*.py` 和 `tests/test_history*.py` 的全部文件为下列三个文件，回归命令为：

```text
python -m pytest -q tests/test_snapshot_consumers.py tests/test_history.py tests/test_history_maintenance.py --basetemp=../.pytest-tmp/history-retry-regression
38 passed in 11.75s
```

后端全量命令：

```text
python -m pytest -q --basetemp=../.pytest-tmp/x
```

全量汇总行原文：

```text
2102 passed, 4 deselected, 12 warnings in 128.89s (0:02:08)
```

退出码为 0。12 条 warning 均是 API/index-components 现有测试触发的 `datetime.utcnow()` 弃用提示；未修改范围外代码。4 个带默认排除标记的测试未执行。

离线保障来自现有 `backend/tests/conftest.py` 的外部网络阻断 fixture，以及 `pyproject.toml` 默认排除 `network`、`benchmark` 标记。本次没有安装依赖、调用远端服务或更改测试配置。

## 交付与线上状态

- 仅提交此文档，提交主题与 Co-Authored-By trailer 按用户指定；主题不代表 retry 已实现或已启用，实际结论以本文为准。
- 用户禁止联网，因此不推送，不创建线上备份，不部署，也不做线上健康检查；没有连接生产数据库。
- `.pytest-tmp/` 未被 Git 忽略，包含此次回归和全量测试生成的数据库及指标文件。全部保留原位，不暂存、不提交，也不修改范围外的忽略配置；其他测试缓存沿用原有配置。最终 Git 状态在提交前后复核。

## 后续任务建议

本任务按 §2-5 停在前提核查失败，不扩大 §3 允许范围。下一任务先修复 history 写入的重试安全前提，再重新评审和实现 retry：

1. 先写故障注入测试，要求第二行失败时不会留下可被其他调用提交的部分行，并验证提交后维护失败的语义。
2. 选择事务隔离方案，例如独立 history 写连接及明确的事务和失败回滚；共享连接上只增加一个局部 rollback 不能解决其他调用交错提交的问题。将已提交的写入结果与后续 prune/vacuum 失败区分清楚。
3. 或评审可靠的业务去重键及 upsert 方案，证明部分失败后重放也不会产生重复行。表结构和 repository 改动不在本任务授权范围内。
4. 前提测试通过后，再按原任务书为 consumer 写六类 retry 行为测试并实现；保持 slot 最新快照语义、index-components 默认行为、采集频率和取消传播语义。
5. 推送和上线在后续允许联网、获准上线的任务中进行；不能把本地既有测试通过视为 retry 功能或生产验收。
