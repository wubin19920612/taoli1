# 手机标的对保存 500 与数据库锁恢复

日期：2026-10-19，Asia/Shanghai。

## 目标与范围

- 模块：双腿价差的标的对保存、缓存合并、删除，以及导致保存 500 的数据库连接故障。
- 截图组合：Hyperliquid 合约 `io` DEX、`OAIUSDT`，RH Lighter 合约 `OPENAIUSDT`，右腿倍率 1。保留 DEX、原始市场及所有已有倍率字段。
- 验收：修复并发旧快照导致的持续写失败；保存连接独立；失败/取消回滚；真实占锁返回明确的 503 重试提示；鉴权和现有 24 项上限保持原规则；备份、固定 SHA 镜像部署与线上实际保存验证。
- 本任务不修改报价、交易执行、密码、前端布局或市场身份策略。

## 分支、基线与工作区

- 分支：`codex/frontend-localization-polish`；本地及远端初始 HEAD：`34509790fa333702ac7b7e127bbc9419120dd334`。
- 初始生产 HEAD 和两份镜像：`1a5df35bc15eefb3d5cbbda1450fbba5b069ee32`，跟踪文件干净，两容器 healthy。
- 已有 `frontend/src/pages/PairMonitorPage.tsx` 和 `frontend/tests/PairMonitorPage.test.tsx` 是其他任务的布局改动，已读取差异以确定边界，不纳入本任务提交。
- 原有 `.worktrees/`、多份 `backend/.pytest_*`、其他交接文档、`output/` 和研究脚本全部保留。

## 故障证据与恢复边界

- 生产日志 2026-10-19 08:45:41 显示对应 `PUT /api/pair-spread/presets/hyperliquid|future|io|OAI|rh-lighter|future||OPENAI` 返回 500。异常来自 `PairSpreadPresetRepository._upsert_without_commit`，为 `sqlite3.OperationalError: database is locked`。同组合查询返回 200。
- 进程文件锁检查没有发现另一个持锁应用进程；新数据库连接能够立即 `BEGIN IMMEDIATE` 并回滚，而应用既有共享连接持续写失败。主库 WAL 约 2 GB。
- 初始数据库最新历史采样停在 `2026-10-15T02:44:45.496429+00:00`。备份后重启曾使写入短暂恢复，2026-10-19 10:04:46 再次出现持续锁错误，因此单独重启不足以交付。
- 本地真实文件库、两个独立连接可以复现 `SQLITE_BUSY_SNAPSHOT`：共享连接持有读快照，另一个连接提交后，旧快照不能升级为写事务。原生产堆栈未记录扩展错误码；将持续故障判断为此机制，是结合复现和现场锁状态得出的诊断。
- 历史数据缺口不能通过本次修复自动补齐；不能把 `/api/health` 的 `ok` 或行情更新视为数据库写入正常的证明。

## 实现与关键入口

- `backend/app/main.py`：文件库为标的对仓库分配独立连接并在关闭时释放。主共享连接单独启用旧快照恢复。内存测试库仍使用同一个数据库实例。
- `backend/app/services/pair_spread_presets.py`：读写串行，读取游标及时关闭；写入使用 `BEGIN IMMEDIATE`；保存、合并、删除发生异常或取消时回滚；SQLite BUSY/LOCKED 转为专门的繁忙异常。
- `backend/app/api/routes_pair_spread.py`：三个写接口保持密码校验及原语义，仅对繁忙异常返回 503、中文未保存/未同步/未删除提示及 `Retry-After: 1`。
- `backend/app/db/database.py`：只为主共享连接启用 SQLite connection factory；仅在扩展错误码 `SQLITE_BUSY_SNAPSHOT` 时，在 aiosqlite 工作线程内原子回滚失败的读事务，再将原错误交回调用方。不会自动重放基于旧读值计算的写入，也不会对普通占锁、约束错误或其他异常回滚别人的成功写入。业务循环下一次从完整操作重新开始。
- `backend/tests/test_pair_spread_presets.py`：文件库旧快照隔离、三个写操作失败回滚、繁忙后恢复、取消后读请求不得观察未提交记录，兼顾 DEX/原始市场及鉴权。
- `backend/tests/test_database_snapshot_recovery.py`：execute/executemany 的恢复开关、失败操作不自动重放、非快照异常不丢失成功写入。
- SQLite 错误语义参考：[官方错误码](https://www.sqlite.org/rescode.html#busy_snapshot)、[事务隔离](https://www.sqlite.org/isolation.html)。

## 已完成的现场备份与临时恢复

- 重启前通过 SQLite backup API 生成 `/data/radar-manual-pair-save-20261019T015957Z.db`，复制到宿主机 `backups/radar-manual-pair-save-20261019T015957Z.db`。
- 大小 641257472 字节；`integrity_check=ok`；容器及宿主机 SHA-256 均为 `6854ebb43d5e12b3531fcad0a319e82873a2a9cd6abc9eece2769f8efbe6f352`。
- 临时恢复后用服务器既有 Hyperliquid `io:ANTH` / RH Lighter `ANTHROPIC` 标的对的原始完整 payload 原样 PUT，200；返回 payload 与保存前一致，保存前后 24 项列表完全一致。
- 未创建截图中尚未保存的新组合，避免在列表已达 24 项上限时挤掉用户原有记录。没有删除数据库、WAL/SHM、卷或既有备份。

## 验证与交付状态

- 旧保存实现上新增的 5 项回归先失败，覆盖现场相同的文件库锁故障和回滚缺失。
- 初步保存/索引关注/身份仓库/历史维护回归 49 passed；独立的连接恢复回归 5 passed；相关 API 回归 8 passed；前端 API client 8 passed。
- 最终代码：保存、连接恢复、索引关注、身份仓库、历史维护回归 **54 passed**；启动模式、健康、Hyperliquid DEX 查询和资金费率记录相关 API 回归 **8 passed**；前端 API client **8 passed**。新增数据库/保存文件的 Ruff、Python 编译与 `git diff --check` 通过。
- 最终代码的回归、提交、镜像构建、部署备份和持续线上验证将在交付完成后追加于本节；目前不能把临时恢复视为最终完成。
- 已有 FastAPI/Ant Design/Pydantic 警告和 main/routes 的原有 lint 问题不在本次范围内。

## 未跟踪证据与下一步

- 本任务本地证据：`output/pair-save-*-20261019.log`、`output/pair-save-server-errors-20261019.txt`；pytest 临时目录放在本任务自己的 `output/pytest-pair-save-*`，不清理原有临时目录。
- 凭据仅在受控进程内存中使用，不写源码、报告、截图或仓库。
- 当前先完成本模块部署和真实保存验证。后续如需扩展数据库任务健康告警、各后台仓库事务隔离或历史缺口分析，请新建任务，以本文作为入口。
