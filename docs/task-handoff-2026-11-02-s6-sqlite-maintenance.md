# S6 SQLite 维护与保留：编码报告与交接

## 范围、基线与资料

- 分支：`codex/s6-sqlite-maintenance`；基线：`0935bd7e5ceaf8b886a4e8774a7ef0ff90202454`。
- 目标：独立 SQLite 连接、低峰维护、五表保留期、默认关闭总开关、默认开启 dry-run。
- 已读取根目录 `AGENTS.md`、指定调查笔记 §1/§2/§3 S6，以及架构评审 P2-9/S6。架构评审文件不在本 worktree，相关章节从主 checkout 的同名 `docs/architecture-review-2026-10-23-claude.md` 只读取得。
- 不改业务表结构、history 三天策略、现有 `vacuum_if_beneficial`、共享连接事务所有权、前端、部署脚本或锁文件。

## 改动文件与入口

| 文件 | 改动 |
| --- | --- |
| `backend/app/maintenance/__init__.py` | 新增维护包 |
| `backend/app/maintenance/sqlite.py` | `SQLiteMaintenance.run` 调度，`run_once` 独立连接，`_prune` 分批保留，`_vacuum_if_beneficial` 磁盘门槛 |
| `backend/app/core/config.py` | 双开关、UTC 调度小时、批大小、五表保留天数；校验配置 |
| `backend/app/main.py` | 同时满足后台 worker 开关与维护开关时，由现有 supervisor 启动非关键任务；停机复用 supervisor 取消流程 |
| `backend/tests/test_sqlite_maintenance.py` | 15 项维护测试 |
| 本文 | 编码报告与下一阶段交接 |

## 配置与业务规则

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SQLITE_MAINTENANCE_ENABLED` | `false` | 总开关，关闭时不启动任务、不连接库 |
| `SQLITE_MAINTENANCE_DRY_RUN` | `true` | 只统计并记录候选行数；不 DELETE/checkpoint/VACUUM；专用连接另设 query_only |
| `SQLITE_MAINTENANCE_HOUR_UTC` | `20` | 一小时维护窗口，默认北京时间 04:00–05:00、韩国时间 05:00–06:00 |
| `SQLITE_MAINTENANCE_BATCH_SIZE` | `5000` | 允许 1–5000；每批提交后让出 50ms |
| `SQLITE_MAINTENANCE_SECOND_LEVEL_MARKET_SAMPLES_RETENTION_DAYS` | `2` | 按 `observed_at` |
| `SQLITE_MAINTENANCE_SECOND_LEVEL_INDEX_COMPONENT_SAMPLES_RETENTION_DAYS` | `2` | 按 `observed_at` |
| `SQLITE_MAINTENANCE_ALERT_EVENTS_RETENTION_DAYS` | `90` | 按 `created_at` |
| `SQLITE_MAINTENANCE_OIL_NEWS_ITEMS_RETENTION_DAYS` | `90` | 按 `published_at`，不按抓取时间 |
| `SQLITE_MAINTENANCE_SQUEEZE_HOURLY_CANDLES_RETENTION_DAYS` | `30` | 按 `event_time` |

保留天数允许 1–36500。拼错维护布尔配置会报错，不会误把 dry-run 关闭。表名和列名只来自固定白名单。

- 每分钟检查窗口；每个进程每天最多尝试一轮，启动在窗口内可运行，窗口外不会补跑。失败交给 supervisor 记录/退避，但当日不会反复执行。重启进程会重置当日记录。
- 每轮通过 `connect_database` 建立并最终关闭专用主库连接；不初始化 schema，不访问 squeeze-route 库，不复用业务连接。内存数据库跳过。
- 过期判定严格早于截止时间；等于截止时间保留。`julianday` 统一处理 ISO 时间偏移，无法解析的时间保留。
- 缺表、缺时间列时跳过并记日志。每表输出 `deleted`、`would_delete`、`elapsed_seconds`、`status`。
- 先在写事务外分批选 rowid，再短事务 DELETE，删除前重查时间条件，随后提交。以 rowid 游标及本轮上界避免重复扫已处理区间或追逐不断插入的数据。
- 停止事件或窗口结束后不再开始下一批/下一项维护。取消时先 interrupt，再关闭专用连接，未提交批次由连接关闭回滚；已提交批次保留。
- 非 dry-run 完成清理后执行 `wal_checkpoint(TRUNCATE)` 并记录 busy；checkpoint 忙时告警并跳过 VACUUM。
- VACUUM 同时要求空闲页 ≥16MiB、占总页数 ≥10%、数据库所在目录文件系统剩余空间 ≥数据库大小×1.5；否则告警跳过。仅在维护连接执行。剩余空间无法读取时保守跳过。

## 测试环境、命令与结果

Windows 原生 Python 3.12.10；没有运行 WSL。临时虚拟环境：`$env:TEMP\taoli1-s6-venv`。

安装依赖取自项目 `requirements.lock` 与 `requirements-dev.lock`。`uvloop==0.22.1` 不支持 Windows，因此仅在临时安装清单中排除；原始锁文件未改。Windows 另补 `tzdata==2025.3`。其余 45 个锁定版本逐项核对：`mismatches: []`。平台/测试工具的间接依赖由安装器解析，不声称整个 Windows 环境等同 Linux 镜像。

先新增测试，首次运行因未实现模块返回：`1 error in 0.74s`，错误为 `ModuleNotFoundError: No module named 'app.maintenance'`；随后实现并验证。

最终验收，在 `backend` 目录执行：

```powershell
$env:FEISHU_LIVE_SEND_ENABLED = 'false'
$env:ASTRO_ALERT_AUTO_CREATE = 'false'
& (Join-Path $env:TEMP 'taoli1-s6-venv\Scripts\python.exe') -m pytest -q tests/test_sqlite_maintenance.py tests/test_history_maintenance.py tests/test_database_materialized_cursor.py tests/test_database_snapshot_recovery.py tests/test_db_connection_isolation.py tests/test_repositories.py tests/test_api.py tests/test_health_routes.py tests/test_task_supervisor.py tests/test_feature_flags.py tests/test_config.py
```

汇总行：**`235 passed, 2 warnings in 29.88s`**。

新增测试覆盖五表默认/自定义保留期、截止边界、非法时间保留、dry-run 无写维护、缺表/缺列、独立连接、磁盘不足跳过与实际 VACUUM、每批另一连接能立即写入（busy_timeout=0）、开关/API-only 启动边界、低峰每日一轮、停机取消释放连接、窗口结束不再删除、配置默认与非法输入。

附加检查：新增维护包、新增测试、config 的 Ruff 通过；`git diff --check` 通过。main 全文件 Ruff 仍报告 13 项既有问题（已与基线输出核对，包含导入排序、循环闭包等），本任务未扩展修复。

回归过程说明：现有连接隔离测试禁止新模块显式调用 rollback，最终利用专用连接关闭回滚，未修改测试白名单。将现有 config 测试先于 API 测试运行时，缓存中的 dashboard 配置导致 64 个 API 401；将 config 放到末尾后整组通过，未扩展修改既有测试或鉴权。两条最终 warning 为 Starlette TestClient/httpx 和 `datetime.utcnow()` 弃用提示。

## 线上状态、风险与下一步

- 本次交付为分支编码/测试/提交；未连接或修改生产库，未执行生产备份、清理、VACUUM、部署或线上验收。
- 调查笔记要求“上线走部署脚本，需用户批准”。待批准后按固定 SHA 预构建镜像流程部署；先验证备份及 integrity/hash，再启用 `ENABLED=true, DRY_RUN=true` 观察一天，核对各表候选数。删除启用前需保留可恢复备份及检查数据卷空间。
- 调查笔记所称 second_level 两表约 300MB 的七月死数据不是本轮线上实测结论；首次关闭 dry-run 将按两天策略删除这些记录。
- 独立连接不消除 SQLite 单写者约束。VACUUM 仍可能暂时阻塞业务写入；已开始的 SQL 可能跨出维护窗口。API p95、内存、WAL 峰值和生产规模耗时尚需复制库演练及 24 小时观察。
- 磁盘检查是操作前快照，不能预留空间或保证 VACUUM 不遇磁盘不足。缺失/不可解析时间不会删除，需从日志及数据审计另行处理。
- history 原有维护路径仍保留，因此不能宣称已消除原路径 VACUUM 的阻塞。
- 工作开始时工作区干净；本任务只新增上述维护包、测试和文档，无其他任务 WIP。临时环境在系统 TEMP 下，不纳入仓库。
- 编码交付后，后续上线演练/启用应另开运维任务，并以本文作为交接；不要混入 S4 备份改造或共享连接事务重构。
