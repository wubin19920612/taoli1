# S2 采集链解耦实现交接（2026-10-25）

## 范围与交付状态

- 规格：`docs/s2-collector-decoupling-spec-2026-10-25.md`，仅实施采集链解耦 S2。
- 工作目录：`C:\Users\wubin\Desktop\code\codex\taoli1`。
- 分支：`codex/collector-decoupling`；基线及当前 HEAD：`4984b6d5246848a9695a31511d91abe9a7a01b73`。未切换或新建分支。
- **未暂存、未提交、未推送、未 SSH、未部署。** 交由 Claude 验收后提交及执行规格 §6 的线上验收。本次没有线上备份或部署版本变更。
- 复用 `backend/app/runtime/supervisor.py` 的 `beat`、`distribution` 和现有任务启动方式，没有修改 S1 文件。

## 改动文件与设计对应

| 规格 | 文件及入口 | 实现 |
| --- | --- | --- |
| §3.1 | 新增 `backend/app/services/snapshot_consumers.py`：`PublishedSnapshot`、`LatestSnapshotSlot`、`SnapshotConsumer` | frozen 快照、容量 1 邮箱、覆盖计数、等待新代或 stop、串行消费、异常继续、心跳、耗时及代次差统计 |
| §3.2 | `backend/app/services/collector.py`：构造函数、`_collect_once`、`_publish_to_slots`、`runtime_stats` | 默认 `snapshot_slots=None` 保留内联模式；后台发布 tuple 快照，历史与指数不占采集阶段；风险设置限时加载、失败复用成功值及计数 |
| §3.2 | 同文件：`record_history`、`process_index_components` | 后台历史传入快照发布时间；指数保留原先 watch → observe → fetch → process 及异常日志语义 |
| §3.3 | `backend/app/core/config.py`：`Settings`、`__post_init__`、`get_settings` | 默认 background；两个环境变量；非法模式、非正或非有限超时值报错 |
| §3.4 | `backend/app/main.py`：lifespan 的 `if start_collector` 分支、collector 创建处 | 各自创建 history/index slot；注册 `history-recorder`、`index-components`；通过现有 `_start_background_task` 使用非 critical、restart 策略；工厂绑定各自 consumer，避免循环闭包串绑 |
| §3.5 | `backend/app/api/routes_health.py`：`detail` | 密码校验后返回消费者与 slot 的合并统计、collector runtime；两个新字段独立容错，异常时省略；旧 health/live/ready 不变 |
| §4 | 新增 `backend/tests/test_snapshot_consumers.py` | 覆盖邮箱、取消清理、消费者错误恢复及 S1 心跳、滑动窗口、慢历史/指数隔离、风险设置首次失败/回退/恢复、快照内容与只读约定、配置和 lifespan 装配 |
| §4 | `backend/tests/test_health_routes.py` | 新增详情统计内容、单个指标故障隔离、无 collector 时省略 runtime 的测试；原用例保留 |
| §3.6、§5 | 新增本文件 | 实现交接、验证证据、取舍和待验收事项 |

共 8 个任务文件：新增 3 个，修改 5 个。未修改规格 §5 禁止的文件。

## 行为及统计口径

- collector 发布时同步向两个独立 slot 投递同一份只读快照；快照的 markets/opportunities 来自过滤后的结果，用 tuple 浅拷贝容器，保留模型原始字段及身份信息。
- 当前正在处理的一代不被中断；未处理的旧代被最新代覆盖。`dropped` 仅计覆盖尚未领取的快照，领取后再发布不计为丢弃。
- `processed` 为成功处理次数，`failed` 为 handler 抛异常次数；`processed_generation` 为最近已完成尝试的代次，失败也推进，避免重复消费同一个失败快照。`lag_generations = latest_generation - processed_generation`，处理中的代次仍计入 lag。
- `last_duration_seconds` 和 `duration_p95_seconds` 包含成功/失败的完整处理耗时，默认最多保留 120 次，p95 复用 S1 的 nearest-rank 算法。尚未处理时耗时为 null，代次/计数为 0。
- stop 唤醒空闲消费者；取消时清理 slot 内部等待任务，`CancelledError` 继续向上传播，不计失败。正在等待慢 handler 的任务由既有 supervisor shutdown 取消。
- 后台模式在实例级设置 `StageTimings.names`，仅保留 poll/prepare/build/publish；没有改 S1 的类定义或内联模式阶段统计。
- 空适配器或全部被忽略的发布路径也会向后台邮箱投递空快照，保持发布代次一致；原内联空适配器路径仍直接返回。
- 风险设置首次加载失败不能拿构造时的默认设置兜底，仍抛异常；后续超时/异常仅沿用最近成功加载的设置并累计 fallback。取消不会计为 fallback。
- 配置：`COLLECTOR_POST_PUBLISH_MODE=background`（默认）或 `inline`；`COLLECTOR_RISK_SETTINGS_TIMEOUT_SECONDS=2`（默认秒数）。inline 不创建 slot/消费者，state 中为 `{}`。API-only 模式不创建消费者。

## 规格歧义与实现取舍

§3.2 同时要求“内联路径也调用两个公开方法”和“内联历史保持原参数、不传 now”，而给出的 `record_history(snapshot)` 契约固定传 `now=snapshot.published_at`。本实现优先保留明确的旧行为：**内联历史仍直接调用 `history_recorder.record(filtered_opportunities)`；后台调用 `record_history(snapshot)`，传发布时间。** 指数两种模式均调用公开的 `process_index_components`。这是唯一结构性差异；两种历史调用参数均有测试锁定。

## 测试命令与结果

全部在 Windows PowerShell、`backend` 目录运行，未调用 WSL。每轮使用新的 basetemp，没有删除或清理旧目录。

```powershell
$env:PYTHONPATH='tests'

# RED：先完成测试编写，尚未创建生产模块。
python -m pytest -q -p no:cacheprovider --basetemp=.pytest_s2_a -p s1_offline_guard tests/test_snapshot_consumers.py tests/test_health_routes.py
# 预期失败：1 个 collection error，ModuleNotFoundError: app.services.snapshot_consumers。

# GREEN：完成生产实现后的首轮定向验证。
python -m pytest -q -p no:cacheprovider --basetemp=.pytest_s2_b -p s1_offline_guard tests/test_snapshot_consumers.py tests/test_health_routes.py
# 39 passed in 61.01s。

# 最终：补充非空机会快照检查，并执行规格 §4 全部回归文件。
python -m pytest -q -p no:cacheprovider --basetemp=.pytest_s2_c -p s1_offline_guard tests/test_snapshot_consumers.py tests/test_collector.py tests/test_collector_pipeline.py tests/test_history.py tests/test_history_maintenance.py tests/test_index_components.py tests/test_index_watch_filter_equivalence.py tests/test_task_supervisor.py tests/test_health_routes.py tests/test_alert_loop.py
# 211 passed, 11 warnings in 97.63s。

python -c "import app.main"
# exit code 0，无输出。
```

- 11 个 warning 来自 `test_index_components.py` 执行 Pydantic 模型时触发的 `datetime.utcnow()` 弃用提示；没有测试失败或网络 guard 报错。
- 慢依赖测试以 Event 阻塞真实 consumer handler 路径，collector 三轮均在每轮 0.5 秒的测试 deadline 内完成，generation 达到 3；另一消费者独立推进。释放阻塞后只处理已在途的第 1 代及最新第 3 代，跳过第 2 代。
- lifespan 装配测试使用内存数据库和假 adapter/provider，捕获其他任务工厂而不运行其业务循环，仅运行本次消费者验证绑定。未触达飞书、Astro、Gate 或交易所。
- 未运行 `tests/test_api.py` 整文件。未执行前端测试，因为本任务没有前端改动。
- `git diff --check` 对任务内受跟踪改动通过；检查暂存区为空。

## 已知限制与后续验收

- 历史 recorder 的 `sample_seconds`（默认 120 秒）节流逻辑不变，后台使用 `published_at` 作为采样时间。跳过中间行情代不改变这个节流阈值；若消费者本身持续慢于采样间隔，实际样本间隔仍可能增大，线上约 120 秒的中位数需要 Claude 实测。
- 慢指数消费者会跳过中间行情代，`observe_markets` 的价格采样点变稀疏，这是有意取舍。provider 原有逐标的节流、最新行情成分比较及变化处理逻辑不变；S2 不补回两个抓取时刻之间的瞬时变化。
- 指数处理保留原 `try/except` 吞异常并记录日志的行为，因此被该方法内部吞掉的 provider/monitor 错误不会增加外层 consumer.failed；只有逃逸至 handler 外层的异常才计入该指标。
- VACUUM 仍在共享 SQLite 连接上运行，连接上的 API 读写阻塞仍存在，留给 S6。缓存风险设置后的采集读取最多等待配置超时；S2 没有改连接、维护调度或整轮采集 deadline。
- 内联回退会恢复历史/指数阻塞采集的旧行为，风险设置读取超时机制在两种模式均启用。
- 快照仅冻结容器，不深拷贝或冻结模型；消费者必须遵守只读约定。slot 限定同一 asyncio event loop、单消费者使用。
- 未做线上基线对比；后续由 Claude 按规格 §6 检查 ready、generation 推进、历史采样间隔、collector p95、消费者 lag/p95 和 fallback。回退配置为 `COLLECTOR_POST_PUBLISH_MODE=inline`。

## 工作区保护与交接

- 用户 WIP `frontend/src/pages/PairMonitorPage.tsx` 与 `frontend/tests/PairMonitorPage.test.tsx` 的 SHA256 在任务开始/最终复核一致，分别为 `7C1965B293123C1FCBDACD3F3B80022142C6B60E63C47BEABC87CA49C4ADADF5`、`C7FA3B2BBEF5D87E43E0CFD0BAF69927618EE760B08984C41AA89E254B1848DE`；未修改、暂存或还原。
- 既有未跟踪产物包括 `docs/architecture-review-2026-10-23-claude.md`、本次规格文件、其他交接、`.worktrees/`、`output/` 和多个测试目录，全部保留。原有部分 `.pytest_*` 存在受限 ACL，未尝试清理。
- 本任务新增的模块、测试和本交接仍未跟踪；测试临时目录按命令保留。没有删除数据库、日志或备份。
- 下一步仅为 Claude 验收与提交；后续 S6 或其他模块另建任务，不扩展本任务。

## 线上部署结果（2026-10-25，Claude 执行，用户批准）

- Claude 验收：S2 新测试 + 9 个回归文件 211 passed（离线 guard），`import app.main` 通过；本地提交 `3be4e8f`。
- 推送 `codex/collector-decoupling`；部署分支 `codex/frontend-localization-polish` 由 `4984b6d` 快进到 `3be4e8f95cd911161fb86df5aa30a724dee0ae8d`。CI run 40110723584 success，前后端 sha 镜像 manifest 通过。
- 部署脚本 `output/s2-isolated-deploy-20261025.sh`（与 S1 包装字节一致，SHA256 `8345b75e…`），服务器日志 `output/s2-deployment-20261025.log`，00:31（CST）完成。
- 备份（integrity_check=ok，容器与宿主机 SHA256 一致）：
  - 主库 `backups/radar-before-3be4e8f95cd9-20261024T161720Z.db`，SHA256 `80f15b1a90a985d63481996f2e3bbaf122d2235e498797d5340ef095f518289a`
  - 路线库 `backups/squeeze-route-before-3be4e8f95cd9-20261024T161720Z.db`，SHA256 `03fb1799cae24e6c7e9800c5bd1dbc79f15c4714a23640cc251fe1194b0ca3b3`
  - 主库备份耗时约 12.5 分钟（S1 时约 1 分钟）。推测原因：后端频繁 OOM 重启时写库，SQLite 在线备份遇到源库写入要从头复制。
- 线上验证：HEAD = 3be4e8f，tracked clean；`live` 200、`ready` 200、`detail` 无密码 401、旧 `/api/health` status=ok、exchange_errors={}。
- 历史采样（只读隔离容器查 `opportunity_history.observed_at`）：上线后间隔 129/125/125/103/124/125/127/126/138 秒；上线前中位数 121 秒。最后一个 19 秒间隔是 OOM 重启后的首次采样（旧行为相同）。
- 观察：`history-recorder` 在 25 分钟内失败 4 次，错误为 `sqlite3.OperationalError: database is locked`。同时段指数价采样等其他写库操作也报同样错误，且集中在 OOM kill 前约 1 分钟（16:49–16:50Z，OOM 在 16:50:21Z）。判断是内存打满导致的锁超时，不是 S2 引起的。S2 下只有该代历史失败，采集不受影响。
- **既有严重问题**：后端自 10-23 起每小时被 cgroup OOM（768MiB）杀 5–16 次，被杀时 anon-rss 约 780MB；重启后约 350MB，几分钟内涨满。S1、S2 前后频率不变。下一步应先做 M1 内存剖析（优先于 S3）。
- 回滚：`.env` 设 `COLLECTOR_POST_PUBLISH_MODE=inline` 后重启，或 revert `3be4e8f`。
