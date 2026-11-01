# 主连接回滚隔离：建卡账本改用独立连接（2026-11-01）

## 背景（生产 ae985da，只读调查已确认）
- 主库 `radar.db` 的主连接 `db`（`main.py` 里 `connect_database(db_path, recover_stale_snapshots=True)`）由二十多个仓储共用，每个仓储各自 `execute → commit`。Python sqlite3 的隐式事务是**按连接**的：A 仓储 `execute` 之后、`commit` 之前让出事件循环，此时如果 B 仓储执行 `rollback()`，A 已经执行、还没提交的写入会被一起撤销，而 A 随后的 `commit()` 仍然返回成功。
- 市场身份、FS、价差预设、squeeze 路线库都已经用了**独立连接**，它们自己的回滚不会影响主连接。
- 主连接上显式调用 `rollback()` 的只剩 `app/services/astro_card_attempts.py`（S7-B 建卡账本）：`mark_pending` 和 `mark` 出错时，包括 `CancelledError`（`except BaseException`），都会对整个主连接回滚。
- `database.py` 遇到 `SQLITE_BUSY_SNAPSHOT` 时的自动回滚不在本次范围内：只有事务还没写入时才会触发，回滚掉的只是读快照，不会撤销别人已执行的写。
- 生产现状：当前容器自 11-01 15:49 起没有 OperationalError、database is locked 或快照回滚的记录；旧容器日志已随重建删除。这是没有观测到发生的潜在问题，影响是告警投递状态、告警事件、设置等写入被静默撤销。
- 架构审阅 A07“多仓库共享主连接并自行 commit”提到过这个方向；这次只修已确认的回滚源，不做全面的事务归属重构。

## 要做的事
1. **建卡账本改用独立连接**（`backend/app/main.py`）：
   - 仿照 `identity_db`/`preset_db`：`attempts_db = db if db_path == ":memory:" else await connect_database(db_path)`，用它构造 `AstroCardAttemptRepository`。
   - 关闭时与 `identity_db` 一样：`if attempts_db is not db: await attempts_db.close()`，放在 `db.close()` 之前，关闭顺序和异常处理与现有代码一致。
   - 表结构仍由主连接的 `initialize_schema(db)` 创建（同一个文件），不需要另外初始化。
2. **账本内部不改业务语义**（`backend/app/services/astro_card_attempts.py`）：保留现有锁、`mark_pending` 的“先清理再 upsert 再 commit”、出错回滚。只在类的 docstring 或注释里说明：本仓储会对自己的连接回滚，**必须**使用独立连接，不能传入共享主连接。
3. **防回归测试**：
   - 静态测试：扫描 `backend/app/**/*.py` 里所有 `.rollback(` 调用，断言只出现在白名单模块中：`db/database.py`（快照恢复）、`db/market_identity_repository.py`、`services/fs_repository.py`、`services/pair_spread_presets.py`、`services/astro_card_attempts.py`、`services/squeeze_arbitrage/paper_repository.py`、`services/squeeze_arbitrage/route_repository.py`。新模块要回滚，必须先改用独立连接并加进白名单。
   - 启动测试：文件型数据库（`tmp_path`）启动 app 后，断言 `app.state.astro_alert_service.attempts.db is not` 主连接（主连接可以从 `alert_event_repo.db` 取得），并且关闭 app 后账本连接已关闭。`:memory:` 时仍与主连接相同。
   - 隔离测试（文件型数据库）：主连接上一个仓储 `execute` 了还没 commit 时，账本 `mark_pending` 失败触发回滚（用 monkeypatch 让账本的 execute 抛错），之后主连接 commit，断言主连接那条写入**仍然存在**。在改动前，这个测试应该失败。
4. **只读审计（不改代码，写进交接记录）**：列出主连接上“一个写入单元里有多条写语句”的位置（`execute` 两次及以上才 `commit`，或循环里 `execute` 最后统一 commit），每处写 文件:行号、涉及的表、如果中途失败或被别的仓储提前 commit 会发生什么（部分提交还是幽灵提交），并按影响排序。这些留作后续任务，本次不改。

## 不改
- 其他仓储、`database.py`、S7-C 的告警写锁、S7-B 的账本状态机和锁、表结构。

## 验收
- 上面 3 项测试全部通过；隔离测试在改动前失败（交接记录写明红灯结果）。
- 已有测试一个都不能放宽或删除。

## 范围
- 可改：`backend/app/main.py`、`backend/app/services/astro_card_attempts.py`（只加注释或 docstring）、新增 `backend/tests/test_db_connection_isolation.py`、本任务书（末尾追加交接记录和审计结果）。
- 不可改：其他所有文件。
- 提交信息：`fix: give astro card attempts its own sqlite connection`
- 基线：分支 `codex/db-write-lock`，基于 `ae985da`（生产版本，部署分支 tip）。

## 测试环境（必须）
在本工作树的 `backend` 目录运行：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = (Get-Location).Path
$py = 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad\venv\Scripts\python.exe'
& $py -c "import app.main as m; print(m.__file__)"
& $py -m pytest -q -p no:cacheprovider --basetemp 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad\pt-dbw'
```

第一条命令输出的路径必须在 `.worktrees\db-write-lock` 下。`--basetemp` 必须加，否则 `tmp_path` 会因为权限问题报错。日志里保留执行的命令、路径输出和汇总行。不要运行 WSL；不要访问生产或外网；**不要 git push**。

## 输出（最后给我）
改动文件清单；隔离测试的红灯和绿灯结果；审计列表；全量测试命令和汇总行。

## Claude 验收（Codex 不用做）
- 只读审查：主连接上是否还有别的回滚源；关闭顺序是否正确；`:memory:` 分支是否不变。
- 上线后 +1h：restarts、oom、locked 都是 0；`astro_card_attempts` 没有新增 pending 或 unknown；手动或自动建卡正常；`OperationalError` 和 database is locked 的计数不比上线前多。

## Codex 本地交接记录（2026-11-01）

### 范围、基线与执行边界
- 模块：Astro 建卡账本的 SQLite 连接隔离；验收为回滚白名单、文件型连接独立且关闭、内存型保持共享、回滚不撤销主连接写入、后端全量测试。
- 工作树：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\db-write-lock`；分支 `codex/db-write-lock`；初始 HEAD `ae985da48c0e28135dca2ced9edb9f7bad072be1`。
- 初始工作区只有本任务书未跟踪；按本次明确指令仅本地提交，不 push、不运行 WSL、不访问生产或外网。交接直接追加到本任务书，不新增其他交接文件。

### 先测试：红灯记录（修改实现之前）
先新增 `backend/tests/test_db_connection_isolation.py`，尚未修改 `main.py` 或账本仓储。执行目录为本工作树 `backend`，执行命令：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = (Get-Location).Path
$py = 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad\venv\Scripts\python.exe'
& $py -c "import app.main as m; print(m.__file__)"
& $py -m pytest -q -p no:cacheprovider --basetemp 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad\pt-dbw' tests/test_db_connection_isolation.py
```

路径输出：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\db-write-lock\backend\app\main.py`。

退出码 1；汇总行：`3 failed, 2 passed in 9.80s`。
- 文件型启动断言失败：账本连接与 `alert_event_repo.db` 为同一对象。
- 隔离用例分别注入 `sqlite3.OperationalError`、`asyncio.CancelledError`，均确认真实 `rollback()` 被等待一次；之后主连接 `commit()` 成功，但新连接查询 `alert_delivery_state` 得到 `[]`，预期为 `['shared-write']`。这是目标缺陷导致的红灯，不是环境错误。
- 回滚白名单静态检查、`:memory:` 连接及关闭测试通过。
- 隔离用例通过真实 `AlertDeliveryStore.mark_pending` 写入，暂缓其 commit，再让账本 execute 失败；最后用新连接验证持久化结果。未访问外部服务；沿用现有默认测试筛选及 `conftest.py` 的离线网络阻断。

### 实现、绿灯与全量验证
- `backend/app/main.py:1000`：文件库创建 `attempts_db`，复用主连接已初始化的表；`:memory:` 继续使用主连接。`main.py:1437`：按现有关闭方式，在主连接之前关闭独立账本连接。
- `backend/app/services/astro_card_attempts.py:22`：只新增类 docstring，说明回滚与连接所有权要求及内存测试例外；状态机、锁、清理/upsert/commit 顺序和 `BaseException` 回滚均不改。
- `backend/tests/test_db_connection_isolation.py`：AST 扫描全部 app Python 模块的 rollback 调用并执行七模块白名单；文件/内存启动和关闭；数据库错误/取消两种回滚隔离，共 5 个用例。
- 实现后在同一 `backend` 目录、使用红灯记录相同环境变量、venv、定向测试命令及 `--basetemp` 重跑：退出码 0，`5 passed in 9.19s`。两个隔离用例都由新连接读到 `shared-write` 及非空 `pending_since`；文件型账本连接独立且退出 lifespan 后不可用。

后端全量执行的完整命令（未追加测试筛选参数）：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = (Get-Location).Path
$py = 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad\venv\Scripts\python.exe'
& $py -c "import app.main as m; print(m.__file__)"
& $py -m pytest -q -p no:cacheprovider --basetemp 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad\pt-dbw'
```

路径输出：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\db-write-lock\backend\app\main.py`。

退出码 0；汇总行：`2558 passed, 6 deselected, 13 warnings in 112.12s (0:01:52)`。
- 6 个 deselected 来自仓库原有 `not network and not benchmark` 默认设置，没有放宽或删除既有测试。
- 13 条警告为 Starlette/httpx TestClient 和 `datetime.utcnow()` 的弃用提示；本任务不修改依赖或其他测试。
- 本地代码复核：账本仓储去掉新增类 docstring 后的 AST 与基线完全一致；三个 Python 文件均可 AST 解析。只改允许的四个文件，未更改数据库层、其他仓储、表结构或 S7-C 写锁。

### 只读审计：主连接多语句写入（按潜在影响排序）

审计依据为当前工作树源码和 `main.py` 的连接注入，未连接生产、未将潜在风险表述为已发生事故。以下路径均相对于仓库根目录，行号为本次交付代码；未改动这些被审计文件。

术语与边界：
- **部分提交**：一个逻辑单元只保存/删除了前缀，后续语句没有完成。中途失败后这些仓储不主动 rollback，已执行前缀可留在主连接事务中；随后任何共用者 commit 都可能把它持久化。若事务最终随连接关闭而回滚，则不会持久化。
- **幽灵提交**：方法已报错/取消，但它的写入被其他仓储的 commit 持久化，调用方无法从自己的返回结果判断实际落库情况；与部分提交可以同时发生。另一仓储在两条语句之间提前 commit，还会拆开本来期望整体提交的单元。
- `executemany` 在 aiosqlite 一次工作线程调用内逐条执行 SQL，不应描述成协程可在每一行之间插入 commit；但批次中途报错时，成功前缀仍可能留在事务内，随后被其他仓储提交，所以一并列出。
- 以下运行时项目没有仓储自有 rollback。本次只隔离建卡账本回滚，不解决主连接的提交归属；`database.py` 的失败读快照恢复仍按任务书排除。下表的失败后果均为源码推断，不是本次测试逐项注入故障的结果。

| 顺序 | 位置及写入/提交行 | 涉及表 | 中途失败或其他仓储提前 commit 的后果 |
| --- | --- | --- | --- |
| 1 | `backend/app/db/repositories.py:648` `replace_auto_watch_source`；656、660 写，664 commit | `index_component_auto_watchlist` | 先删旧项后加新项；删除成功而新增失败时，可能只撤掉旧监控、未建立新监控。前缀被别的仓储提交后为部分提交，也可能出现方法失败却已改 watchlist 的幽灵提交。 |
| 2 | `backend/app/services/squeeze_arbitrage/repository.py:227` `save_features`；231、246/266 写，259/276 commit | `squeeze_feature_results`、`squeeze_watch_events` | 特征已更新而事件更新/创建失败，可能出现特征与监控事件不一致；其他仓储可提前固化特征，或在方法失败后提交它。达到上限时 259 的早退 commit 是正常业务分支。 |
| 3 | `backend/app/services/squeeze_arbitrage/repository.py:308` `set_coverage`；318/323、328 写，338 commit | `squeeze_liquidation_gaps`、`squeeze_liquidation_coverage` | 缺口打开/关闭而覆盖状态仍旧，影响覆盖完整性解释；可能部分提交，也可能报错后缺口记录被他人提交。318 与 323 不会在同次状态切换中同时执行。 |
| 4 | `backend/app/services/squeeze_arbitrage/repository.py:158` `save_market_data`；162、172 批写，191 commit | `squeeze_hourly_candles`、`squeeze_positioning_samples` | K 线批次已写而持仓批次缺失/只写前缀，数据批次不齐；两批之间可被提前 commit，失败前缀也可能随后成为幽灵提交。 |
| 5 | `backend/app/services/negative_basis_monitor.py:1014` `delete_auto_watch_items_matching`；1028 循环删，1031 commit | `negative_basis_watchlist` | 屏蔽策略只删除部分匹配监控项，失败却可能已有项永久删除；循环各次 await 之间可以被其他仓储提交。 |
| 6 | `backend/app/services/negative_basis_monitor.py:1034` `delete_unselected_auto_watch_items`；1039 循环删，1042 commit | `negative_basis_watchlist` | 自动监控清理只完成一部分；提前/后续他人 commit 可固化部分删除，表现为失败后的幽灵提交。 |
| 7 | `backend/app/services/funding_research/repository.py:58` `create_market_snapshots`；61 批写，94 commit | `funding_research_market_snapshots` | 批次错误可遗留部分市场快照；其他仓储随后提交会使方法失败但部分数据存在，影响研究样本完整性。 |
| 8 | `backend/app/services/funding_research/repository.py:143` `create_candidate_snapshots`；151 批写，191 commit | `funding_research_opportunity_snapshots` | 候选批次只写前缀；他人提交后样本集不齐且调用方已经收到失败，属于部分/幽灵提交。 |
| 9 | `backend/app/services/second_level_sampler.py:395` `insert_samples`；398 批写，441 commit | `second_level_market_samples` | 同轮秒级市场样本可能只持久化一部分；批写失败前缀可被后续他人 commit，影响回放完整性。 |
| 10 | `backend/app/services/second_level_sampler.py:443` `insert_component_samples`；446 批写，479 commit | `second_level_index_component_samples` | 同轮指数成分样本可能不齐；批写失败前缀随后被提交，出现部分/幽灵提交，影响重建指数。 |
| 11 | `backend/app/db/repositories.py:380` `record_index_prices`；383 批写，390 commit | `index_component_price_samples` | 除正常 `OR IGNORE` 重复外，批次出错仍可留下前缀；他人提交后趋势价格序列只包含部分本轮样本。 |
| 12 | `backend/app/services/squeeze_arbitrage/repository.py:503` `prune_samples`；513 六表循环删，517 commit | `squeeze_hourly_candles`、`squeeze_positioning_samples`、`squeeze_liquidation_updates`、`squeeze_feature_results`、`squeeze_scan_runs`、`squeeze_discovery_runs` | 六表保留期清理不一致；循环间提前 commit 可分段固化删除，中途失败也可能已删除部分表的数据。 |
| 13 | `backend/app/services/negative_basis_monitor.py:1182` `prune`；1184、1188 删，1192 commit | `negative_basis_signal_samples`、`negative_basis_alert_events` | 样本已清理但事件未清理；部分删除可被他人提前/随后提交，方法失败也不能视为未清理。 |
| 14 | `backend/app/services/second_level_sampler.py:634` `prune`；636、640 删，644 commit | `second_level_market_samples`、`second_level_index_component_samples` | 市场样本和成分样本的清理边界分裂；可部分提交，也可在方法失败后由他人提交先前删除。 |
| 15 | `backend/app/services/funding_research/repository.py:262` `prune_snapshots_before`；263、267 删，271 commit | `funding_research_market_snapshots`、`funding_research_opportunity_snapshots` | 市场快照删除而候选快照尚存；前缀由他人固化后清理不齐，可能出现失败后的幽灵提交。 |
| 16 | `backend/app/db/repositories.py:404` `prune_index_prices`；405、409 删，414 commit | `index_component_price_samples`、`index_component_trend_followups` | 价格样本清理与已结束跟踪记录清理分裂；提前/随后他人提交可使失败的清理已经部分生效。 |

### 启动迁移补充审计（同属主连接，低于运行时并发风险）

`main.py:998` 在注入运行时仓储、启动后台任务之前调用 `initialize_schema(db)`。因此下列启动路径没有正常运行中“另一个仓储并发提前 commit”的窗口；仍存在多语句迁移中途失败后只完成一部分的风险。`executescript` 没有包显式 BEGIN，不能把末尾的 `commit()` 理解为整个 schema 原子提交；它还会先提交此前已有事务。表结构、迁移代码均未修改。

| 位置 | 涉及表 | 失败/提交边界 |
| --- | --- | --- |
| `backend/app/db/schema.py:6` `_migrate_alert_rule_excluded_labels`；18 循环 UPDATE | `alert_rules` | 无内部 commit；已更新前缀在后续 `initialize_squeeze_schema` 的 `executescript` 入口被提交。若后续初始化失败，不能据此认为规则迁移未保存；属于启动过程部分生效，无运行时仓储并发。 |
| `backend/app/db/schema.py:24` `_ensure_opportunity_history_columns`；45 循环 ALTER | `opportunity_history` | 在当前无显式事务的启动路径，先完成的列变更可已提交；后续列失败不会整体回退。 |
| `backend/app/db/schema.py:48` `_ensure_exchange_announcement_columns`；67 循环 ALTER | `exchange_announcements` | 同上，可只增加部分列。 |
| `backend/app/db/schema.py:70` `_ensure_second_level_sample_columns`；83 循环 ALTER | `second_level_market_samples` | 同上，可只增加部分列。 |
| `backend/app/db/schema.py:86` `_ensure_pair_spread_funding_columns`；97 循环 ALTER | `pair_spread_funding_watchlist` | 同上，可只增加部分 DEX 字段。 |
| `backend/app/db/schema.py:100` `_ensure_oil_news_columns`；111 循环 ALTER | `oil_news_items` | 同上，可只增加部分翻译字段。 |
| `backend/app/db/schema.py:114` `initialize_schema`；115 executescript，705 起调用迁移，717 commit | 下方列出的 31 张业务表及后续初始化器涉及的表 | 脚本及嵌套初始化分段提交；中间失败时已建表/迁移可能保留。仅为启动部分生效风险，不等同于运行时其他仓储的幽灵提交。 |
| `backend/app/services/squeeze_arbitrage/repository.py:16` `initialize_squeeze_schema`；17 脚本、91 ALTER、96 嵌套初始化，97 commit | `squeeze_market_identity`、`squeeze_scan_runs`、`squeeze_hourly_candles`、`squeeze_positioning_samples`、`squeeze_feature_results`、`squeeze_watch_events`、`squeeze_liquidation_updates`、`squeeze_liquidation_coverage`、`squeeze_liquidation_gaps`、`squeeze_scan_state`、`squeeze_discovery_runs` | 主连接 schema 初始化会走这里；脚本和旧表加列可部分生效，嵌套路线初始化另有 commit。 |
| `backend/app/services/squeeze_arbitrage/route_repository.py:19` `initialize_route_schema`；20 脚本、57 循环 ALTER、61 ALTER，62 commit | `squeeze_route_state`、`squeeze_route_latest`、`squeeze_route_events`、`squeeze_route_worker_state`、`squeeze_route_meta` | 经主 schema 初始化也会在主连接创建这些表；中途失败可能只完成部分 DDL。运行时路线数据使用独立 route_db，不计入上方运行时项目。 |
| `backend/app/db/market_identity_repository.py:17` `initialize_market_identity_schema`；18 脚本，49 commit | `asset_identities`、`market_identity_bindings`、`market_identity_audit`、`market_validation_incidents`、`market_validation_incident_events`、`market_validation_config`、`market_invalid_opportunities` | 主 schema 最后调用，脚本失败可能留部分已创建表；运行时市场身份仓储使用独立连接，不计入运行时项目。 |

`schema.py:115` 主脚本的 31 张表：`alert_rules`、`alert_delivery_state`、`astro_card_attempts`、`alert_events`、`phone_price_alert_rules`、`phone_price_alert_events`、`app_settings`、`account_connections`、`opportunity_history`、`index_component_snapshots`、`index_component_changes`、`index_component_price_samples`、`index_component_trend_followups`、`index_component_watchlist`、`index_component_auto_watchlist`、`exchange_announcements`、`announcement_provider_state`、`oil_news_items`、`funding_research_market_snapshots`、`funding_research_opportunity_snapshots`、`funding_research_paper_trades`、`second_level_market_samples`、`second_level_index_component_samples`、`negative_basis_watchlist`、`negative_basis_signal_samples`、`negative_basis_alert_events`、`pair_spread_funding_watchlist`、`pair_spread_funding_samples`、`pair_spread_presets`、`hyperliquid_trade_status_watchlist`、`trade_availability_watchlist`。

### 排除项、后续任务及交付状态
- 已沿 `main.py` 注入逐一检查主连接仓储写路径，包括告警/投递状态、手机价格告警、设置、账户连接、公告/油价新闻、历史、指数监控、资金研究、秒采样、负基差、价差资金费率、交易状态/可用性监控和 squeeze。单条 DML 后 commit 的方法不计入上述多语句列表。
- `SettingsRepository.mutate_floating_watch_settings` / `mutate_floating_watch_position_settings` 虽有 Python 循环，但循环只改内存，最终各只有一条 upsert；`OpportunityHistoryRepository.insert_many` 是单条多 VALUES INSERT，不是循环执行 SQL；`SqueezeRepository.record_liquidation` 的 INSERT/UPDATE 是互斥分支，同次调用最多一条写入。均不误列为多语句单元。
- 文件型库下，市场身份、FS、价差预设、路线/模拟账本与本次建卡账本使用独立连接，其运行时多语句单元不列为主连接问题。`:memory:` 是规定保留的共享例外；`paper_repository.py:12` 的多语句初始化只在内存分支使用主连接，文件型使用 route_db。
- 后续建议：另开任务按上表影响顺序处理主连接事务归属及迁移原子性，不在本次扩大实现或验收范围。线上状态未复核、未备份、未部署；任务书原有生产描述仅为输入背景。
- 本地交付仅包含 `backend/app/main.py`、`backend/app/services/astro_card_attempts.py`、`backend/tests/test_db_connection_isolation.py`、本任务书。提交信息严格使用 `fix: give astro card attempts its own sqlite connection`，提交号以包含本记录的本地提交为准；不 push。不存在需保留的其他初始修改或未跟踪产物。
- 交付复核：`git diff --cached --check` 通过，暂存区文件清单恰为上述四个文件，分支仍为 `codex/db-write-lock`；未暂存差异为空。Git 未配置自定义 hooks 路径或提交签名，默认 hooks 目录只有 `.sample` 文件。
