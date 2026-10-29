# taoli1 会话交接：M2 内存治理与功能裁剪（2026-10-26）

新会话先读本文件；更早的细节见 `docs/task-handoff-2026-10-25-session-summary.md` §1–§12（分支 `codex/memory-fix`）。分工不变：Claude 负责设计、用桌面版 `codex.exe exec` 派发、亲自验收和部署；Codex 负责写代码和测试；Claude 只在改动不超过 20 行时直接修改。回复用户一律用中文。

## 1 生产现状

| 项 | 值 |
|---|---|
| 版本 | **`8779d40`（10-28 18:56 CST 起，M2-4a 采集器紧凑存储）**，之前依次是 `11ee863`（Gate 降噪，16:11）、 `dbd5bb8`（M2-4b，14:45）、`020642c`（astro 预览 fresh，13:32，另一会话部署）、 `4ad88b7`（锁修复，§5.14）、 `74c8f23`（M2-3b）、`c36fca5`、`f7f56a9`、`cfd79d6`；`.env` 中 9 个 DISABLED_FEATURES 开关生效；部署分支 `codex/frontend-localization-polish` |
| `.env` 中的内存相关键 | `MEMORY_PROBE_ENABLED=true`；`DISABLED_FEATURES=<§2.1 的 9 个键>`（10-27 08:28 设置）；普查已关闭（`MEMORY_CENSUS_*` 已删除） |
| 内存 | 后端 rss_anon 平台期约 720–740MB，上限 1024m，自 13:38 起一直 0 OOM |
| 磁盘 | 约 22.8GB 可用 |

## 2 进行中的分支（都在 `.worktrees/` 下，都**没有合并、没有上线**）

| 分支 | 状态 | 下一步 |
|---|---|---|
| `codex/feature-flags` @ `f7f56a9` | **已验收**：后端全量 1926 passed，前端 279 passed，构建通过 | 等用户批准部署。用户**已授权**上线后在生产 `.env` 设置 `DISABLED_FEATURES`（先备份） |
| `codex/m2-3a-fs-specs` @ `1bfc8f4` | 只有任务书 `docs/m2-3a-fs-spec-cache-spec-2026-10-26.md`，**等用户确认后再派发** | 确认后用 gpt-6-astra high 派发 |

### 2.1 DISABLED_FEATURES（用户圈出了 9 个很少用的功能）

- 键：`squeeze,negative_basis,oil_news,opportunity_radar,symbol_spread,fat_finger,gate_twap,second_level_sampling,funding_research`。
- 行为：停用的功能不构造服务、不启动后台任务，接口返回 404 `feature_disabled`；新增 `GET /api/features`，前端据此隐藏菜单。变量为空时行为不变。未知的键会在配置加载时直接报错。
- 依赖决策（任务书 §7）：`GateTwapClient` 是核心资源，始终构造，`gate_twap` 只停 manager、接口和菜单；停用 `second_level_sampling` 时会自动一并停用 `fat_finger`。
- 内存上真正有收益的是常驻后台任务：挤仓结构（3 个任务）、负基差、原油新闻（2 个）、机会雷达。其余几个功能是用到时才占内存，停用基本不省内存。
- 影响（用户已知晓）：挤仓模拟盘和路由数据不再累积；机会雷达、负基差、原油新闻的飞书提醒会停。
- 上线步骤：推送 `f7f56a9` 到部署分支 → CI → 部署（变量为空，先确认行为不变）→ 备份 `.env` → 设置 `DISABLED_FEATURES=<上面 9 个>` → 只重建后端 → 探针采 1 小时，和 2026-10-26 17:34–18:35 的数据对比 rss_anon 底部、尖峰和斜率。建议沿用服务器端 nohup 脚本的做法，参考 `output/m2census2-run-20261026.sh`。
- 已知问题：前端全量测试偶发失败（并发负载下 `InstrumentLookupPage`、PairMonitor 等会超时，单独跑能通过，重跑全量也能通过）。CI 的前端测试也可能因此偶发失败，失败时先重跑一次再查原因。

### 2.2 M2-3a（FS 规格缓存瘦身）

- 普查第一名：`fs_public.binance_specs` 常驻 71MB，是完整的 `exchangeInfo`。
- **TTL 必须保持 20 秒**：规格的时间戳会进入 `source_times`，`fs_economics.py:129` 要求所有来源都在 60 秒以内，否则报 `stale_input`。
- 和 DISABLED_FEATURES **错开上线**，这样两者的降幅可以分开测量。

## 3 M2 后续（`docs/m2-3-governance-list-2026-10-26.md`，在 `codex/memory-fix` 上）

M2-3b 订单簿瘦身（−15~25MB）→ M2-4a 采集器和快照存储共享对象（−30~40MB）→ M2-4b 身份绑定瘦身（−10~30MB）。目标是平台期低于 600MB。其他待办：RuntimeError 修复（M2-1 交接 §3.1）、S4 备份策略、S6 SQLite 维护（WAL 曾达 174MB，后来自动回落）。

## 4 运维要点（新增部分）

- Codex 桌面版已升级：`C:\Users\wubin\AppData\Local\OpenAI\Codex\bin\2993767bd1dfb009\codex.exe`（v0.164），参数没变。
- Windows 本机跑 pytest 必须加 `--basetemp=<可写目录>`。
- 生产 Python 比本机慢约 5.8 倍。凡是有时间预算的工具，预算都要按生产速度来定。
- CI 日志要登录才能看：请用户在内置浏览器登录 GitHub，然后展开 `check-step-<N>` 读取。
- 长时间的生产操作要放在服务器端用 nohup 跑，以 `RUN_EXIT=`/`DEPLOY_EXIT=` 作为结束标志。不要在 ssh 命令里用 `pkill -f <和命令本身相同的字符串>`，会把 ssh 自己杀掉。
- 推送功能分支时，本机偶尔会遇到 TLS 握手失败，推送部署分支没有受影响。`codex/m2-2-cache-census` 是否已经推送成功，要用 `git ls-remote` 确认。
- 用户 WIP（`frontend/src/pages/PairMonitorPage.tsx` 及其测试）只存在于主检出目录，不要动。

## 5 2026-10-27 会话结果

### 5.1 feature-flags 上线（用户批准全流程）

- 推送 `f7f56a9` 到部署分支（和同名功能分支）→ CI run 40425434150 全部通过（test + build backend/frontend）。
- 阶段 A（变量为空）：服务器端脚本 `output/ff-deploy-20261027.sh`，08:27 CST 部署成功（第一次就成功），live/ready 200，`/api/features` 返回 `{"disabled":[]}`，受保护接口照常返回 200。日志：`output/ff-20261027-phaseA.log`、`ff-20261027-deploy-1.log`。上线前基线（`cfd79d6`，普查已关，10-26 22:38 到 10-27 08:23 CST）已导出：`output/ff-20261027-baseline-probe.log`。
- 阶段 B：`output/ff-enable-20261027.sh`。`.env` 备份为 `backups/env-before-disabled-features-20261027T002817Z`，写入 9 个键，**只重建后端**（08:28:32）。`/api/features` 列出 9 个键；`/api/squeeze-arbitrage/status` 返回 404 `feature_disabled`。采集 1 小时后：0 次重启，0 次 OOM，healthy。日志：`output/ff-20261027-phaseB.log`，探针数据 `output/ff-20261027-probe.log`（本机也有一份）。
- 回滚：`cp backups/env-before-disabled-features-20261027T002817Z .env` 后只重建后端；要回退代码，就用 `cfd79d6` 重新部署。

### 5.2 内存对比（`output/ff-compare-20261027.py`，rss_anon，单位 MB，跳过启动后前 10 分钟）

| 窗口 | 最小值 | p5 | 中位数 | p95 | 最大值 | 斜率 |
|---|---|---|---|---|---|---|
| 10-26 17:34–18:35（`cfd79d6` + 普查，重启后第 1 小时） | 663.4 | 669.0 | 687.2 | 732.3 | 737.3 | +31.6/h |
| 10-27 08:28–09:29（`f7f56a9` + 9 个开关，重启后第 1 小时） | 658.7 | 665.0 | 687.0 | 720.0 | 739.2 | +32.6/h |
| `cfd79d6` 平台期（上线前最后 60 分钟，已运行约 14 小时） | 744.8 | 744.8 | 747.1 | 789.1 | 791.3 | +4.1/h |

- 结论：**重启后第 1 小时看不出降幅**（底部约 −5MB，p95 约 −12MB，都在噪声范围内）。最后 20 分钟的斜率是 −0.3/h（对照为 +44/h），可能说明内存爬升更平缓，但样本太少，不能下结论。
- 注意：对照窗口当时开着普查，普查本身有额外开销，所以开关的真实收益可能更小。平台期（重启后数小时，对照约 745MB）要再采几个小时才能比较。下一步：在 10-27 下午或晚上导出容器探针，和 745MB 平台期对比。
- probe 的 `active_tasks` 在上线前就没有列出挤仓、负基差等任务名，所以不能用它确认这些任务已经停掉，只能看 `/api/features` 和 404。

### 5.3 M2-3a（用户已确认任务书并批准派发）

- Codex（gpt-6-astra high）提交了 `04cde5a`（分支 `codex/m2-3a-fs-specs`，worktree `.worktrees/m2-3a-fs-specs`），只改了任务书允许的 3 个文件；交接文档 `docs/task-handoff-2026-10-26-fs-spec-cache.md`。
- Claude 验收**通过**：审阅了 diff（只改缓存形状，读取方不变；`get()` 不缓存原始 payload；`KeyError`/`TypeError` 路径与旧实现等价）。亲自运行：定向 152 passed，后端全量 1987 passed / 4 deselected（默认排除 network/benchmark）。fake 数据下缓存从 25.7MB 降到 4.7MB（−81.8%）。
- **没有推送，也没有上线**（推送功能分支被权限拦下，用户也没有批准）。上线要等用户另行批准，并且最好等 feature-flags 的平台期数据采完再上，这样两者的降幅可以分开测量。

### 5.4 等待平台期期间并行推进（10-27 上午，都只在本机，生产无改动）

- 平台期：服务器端 nohup 任务会在 **14:30 CST** 导出探针（`output/ff-20261027-plateau-probe.log`，结束标志 `PLATEAU_EXIT=` 写在 `output/ff-20261027-plateau.log`）。导出后取最后 60 分钟，和 `cfd79d6` 的平台期（745MB）对比，命令：`python output/ff-compare-20261027.py "ff_plateau=<log>@0@60" "cfd79d6_plateau=output/ff-20261027-baseline-probe.log@0@60"`。
- **M2-3b 任务书**：`docs/m2-3b-market-validation-cache-spec-2026-10-27.md`（worktree `.worktrees/m2-3b-books`，分支 `codex/m2-3b-books` @ `dafc8b4`，基于 `f7f56a9`）。已核实：`books` 缓存的读取方只用盘口第一档，20 档深度只在 `_review` 的局部变量里用到，所以缓存只存第一档（预计约 −25MB）；`reviews` 只有 `expires_at > now` 时才会命中，可以清理过期条目；`_last_incident` 超过 60 秒的条目等同于不存在。`review_attempted` 会影响调度顺序，不改。**等用户确认后**用 gpt-6.1-sol xhigh 派发。
- **RuntimeError 线上根因（已确认）**：最近 1 小时 174 次，其中 161 次来自 `bitget/future/STGUSDT` 这个观察项。Bitget 已下架该合约（接口返回 `40309 The symbol has been removed`），观察项每 20 秒报一次完整 traceback。HL 的 W/ZETA/SAGA 是偶发上游超时。降噪任务书 `docs/trade-watch-error-noise-spec-2026-10-27.md`（worktree `.worktrees/trade-watch-errors`，分支 `codex/trade-watch-errors` @ `88aea55`）：同一个错误只打一次 traceback，并保留 `result.errors` 中的具体原因。**等用户确认后再派发**。运维建议：由用户在前端停用或删除 STGUSDT 观察项。
- **S6（只读）**：`radar.db` 780MB，`radar.db-wal` 现在只有 9.6MB（之前的 174MB 已经回落）。数据卷里还有两份旧库副本：`radar-lighter-gc-20260916.db` 597MB、`radar-manual-pair-save-20261019T015957Z.db` 612MB，共约 1.2GB。是否删除由用户决定（属于永久删除，Claude 不执行）。在容器内跑只读 PRAGMA 和 dbstat 被权限拦下了，没有取到 freelist 和各表的大小。

### 5.5 M2-3b 与报错降噪：开发完成并验收（用户确认任务书后派发，都没推送、没上线）

| 分支 | 提交 | Claude 验收 |
|---|---|---|
| `codex/m2-3b-books`（`.worktrees/m2-3b-books`） | `55fb53e` | 通过。审阅了 diff：缓存只存盘口第一档，`_review` 仍拿到完整深度；`_ExpiryCache` 会清理过期的值，但把 key 留在淘汰顺序里，直到原来的 1000 条上限把它挤出，所以淘汰顺序和旧实现严格等价。定向 102 passed，全量 1956 passed。fake 数据下 books 从 22.7MB 降到 2.5MB（−88.9%）。交接 `docs/task-handoff-2026-10-27-market-validation-cache.md` |
| `codex/trade-watch-errors`（`.worktrees/trade-watch-errors`） | `65d86b9` + `65b6431` | 通过。同一个错误只在第一次（或原因改变时）打 traceback；没有匹配到市场时，在报错里附上 `result.errors` 中的具体诊断原因。M2-1 的复现测试原来断言完整的旧文案，有 3 个用例因此失败；Claude 把断言改成 startswith（`65b6431`）。定向 64 passed，全量 1938 passed（其中一次全量有 2 个偶发失败，当时另一个全量也在跑，机器负载高；重跑全部通过）。 |

- 本机测试注意：`--basetemp` 必须用短路径（例如 worktree 下的 `../.pytest-tmp/x`）。用 Claude scratchpad 那种长路径会超过 Windows 路径长度上限，出现 70 个 `FileNotFoundError`。
- 上线顺序建议（每一步都要用户批准，相邻两次上线至少间隔 1 小时）：feature-flags 平台期测完 → M2-3a → M2-3b →（降噪不影响内存，可以和任意一项合并上线）。

### 5.6 feature-flags 平台期结果（10-27 14:30 CST 导出，开关上线后约 6 小时，0 次重启、0 次 OOM）

| 窗口（rss_anon，MB） | 最小值 | p5 | 中位数 | p95 | 最大值 | 斜率 |
|---|---|---|---|---|---|---|
| `cfd79d6` 平台期（上线前最后 60 分钟） | 744.8 | 744.8 | 747.1 | 789.1 | 791.3 | +4.1/h |
| `f7f56a9` + 9 个开关（最后 60 分钟） | 699.8 | 701.8 | 704.3 | 746.8 | 751.2 | +1.5/h |
| `f7f56a9` + 9 个开关（上线 2 小时后共 4 小时） | 695.7 | 699.2 | 703.3 | 745.7 | 751.2 | +0.3/h |

- 结论：**平台期下降约 43MB**（中位数 747→704，底部 745→700，p95 789→747），第 2 小时起就走平了。第 1 小时看不出差别（§5.2），是因为启动后的爬升阶段把差异掩盖了，挤仓、雷达等后台任务的占用是慢慢累积起来的。
- 每小时中位数（UTC）：00 时 672，01 时 696，02 时 701，03 时 704，04 时 703，05 时 704，06 时 705。数据：`output/ff-20261027-plateau-probe.log`。
- 后续比较都以约 **704MB** 作为新的平台期基线。M2-3a 预计 −55~65MB，M2-3b 预计约 −25MB，降完后平台期约 615–625MB。

### 5.7 M2-3a + 报错降噪上线（用户批准，10-27 14:53 CST）

- 集成分支 `codex/deploy-m2-3a` = `c36fca5`：在 `f7f56a9` 之上 cherry-pick 了 M2-3a（`04cde5a`→`46f2ea3`）和降噪（`65d86b9`/`65b6431`→`826ee66`/`c36fca5`）。本地全量 2013 passed；CI test、build 都通过。已推送到部署分支。`.env` 未改动（9 个开关仍然生效）。
- **第一次部署失败，生产未受影响**：备份 `radar-squeeze-route.db` 时报 `unable to open database file`。原因：备份容器以只读方式挂载数据卷；挤仓停用后，这个 WAL 库不再被任何进程打开，所以没有 `-wal`/`-shm` 文件，而只读打开 WAL 库时必须新建 `-shm`。修复：新建部署脚本副本 `output/m23a-isolated-deploy-20261027.sh`，当数据库既没有 `-wal` 也没有 `-shm` 时，用 `mode=ro&immutable=1` 打开；integrity_check 和 SHA 校验保留。**以后部署都用这个副本**（只要挤仓保持停用，旧脚本就会失败）。两次失败各留下一份 780MB 的 radar 备份（integrity 都通过），留给保留策略清理。日志：`output/m23a-20261027-*-failed1.log`。
- 重试一次就成功：live/ready 200，`/api/features` 仍然列出 9 个键，`fs-monitor` 照常周期运行；部署后 10 分钟内 `stale_input`/`spot_market_missing` 为 0，FS 相关错误为 0；STGUSDT 观察项不再每 20 秒打一次 traceback。
- 剩余噪声（不在本次范围）：Gate 公告接口返回 `567 Unknown Status`，10 分钟内 35 次 traceback（5 个分类 × 7 轮），属于上游问题，可以另开降噪任务。
- 采集：服务器端脚本会在 +2h、+4h 导出探针（`output/m23a-20261027-probe-{2,4}h.log`，结束标志 `COLLECT_2H_DONE`/`COLLECT_4H_DONE`/`RUN_EXIT=`），对比基线是 704MB 平台期。上线前的探针在 `output/m23a-20261027-baseline-probe.log`。

### 5.8 M2-3a 上线 +2h 结果（10-27 16:53 CST 导出，0 次重启，0 次 OOM，降噪后 RuntimeError traceback 为 0）

按容器运行时长对齐比较（rss_anon，单位 MB）：

| 运行时长 | `f7f56a9` 中位数 / 最大值 | `c36fca5` 中位数 / 最大值 | 差值（中位数） |
|---|---|---|---|
| 10–60 分钟 | 686.9 / 739.2 | 618.6 / 667.3 | −68 |
| 60–112 分钟 | 700.5 / 751.5 | 628.2 / 672.3 | −72 |

- `c36fca5` 最后 60 分钟：min 622.0，p5 622.0，中位数 627.9，p95 648.9，max 672.3，斜率 −1.8/h（已走平）。和 704MB 平台期相比低约 76MB，比预计的 −55~65MB 还多一些。
- 数据：`output/m23a-20261027-probe-2h.log`。+4h（约 18:53）还会导出一次，用来确认平台期。
- 下一步：+4h 确认之后，再向用户申请 M2-3b 上线（需要把 `55fb53e` cherry-pick 到 `c36fca5` 之上，使用 `m23a-isolated` 副本脚本部署）。预计平台期会降到 600MB 左右。

### 5.9 M2-3a 上线 +4h：平台期确认（10-27 18:53 CST 导出，0 次重启，0 次 OOM，RuntimeError traceback 为 0）

- 每小时中位数（按容器运行时长）：`f7f56a9` 依次为 681 / 700 / 703 / 703；`c36fca5` 依次为 617 / 628 / 626 / 627。
- `c36fca5` 上线 2 小时之后的两小时：min 625.1，p5 625.1，中位数 626.1，p95 667.3，max 678.3，斜率 +0.9/h。最后 60 分钟中位数 627.4。
- 结论：**新的平台期约 627MB**，比 704MB 低约 77MB；累计比 `cfd79d6` 的 747MB 低约 120MB。数据：`output/m23a-20261027-probe-4h.log`。
- 下一步：申请 M2-3b 上线（把 `55fb53e` cherry-pick 到 `c36fca5` 之上 → 本地全量测试 → 推送 → CI → 用 `m23a-isolated` 副本脚本部署，`.env` 不动 → 采集 2h/4h 数据，和 627MB 对比）。预计平台期约 600MB。

### 5.10 M2-3b 上线（用户批准，10-27 20:00 CST）

- 集成分支 `codex/deploy-m2-3b`（worktree `.worktrees/deploy-m2-3b`）= `74c8f23`：在 `c36fca5` 之上 cherry-pick 了 `dafc8b4`→`f9767b1`（任务书）和 `55fb53e`→`74c8f23`（实现），没有冲突。本地后端全量 2043 passed；CI run 40499126112 通过。已快进推送到部署分支。`.env` 未改动。
- 部署：`output/m23b-deploy-20261027.sh`（由 m23a 脚本改出来，调用 `m23a-isolated` 副本），第 1 次尝试就成功。live/ready 200，9 个开关仍然生效，后台任务和上线前一致（TASKS_SAME）。
- 上线后 5 分钟：没有市场审核、深度校验相关的报错；traceback 全部来自原有的公告接口噪声（Gate 567、Binance 公告 429）。上线前 60 分钟 `DEPTH_UNCONFIRMED|SPREAD_REVIEW` 日志为 0 条，作为对照。
- 采集：+2h（约 22:00）、+4h（约 00:00）导出 `output/m23b-20261027-probe-{2,4}h.log`，和 627MB 平台期对比。上线前的探针在 `output/m23b-20261027-baseline-probe.log`。

### 5.11 M2-3b 结果（10-28 早上取数，容器已运行约 13 小时，0 次重启，0 次 OOM）

| 容器运行时长 | c36fca5 每小时中位数 | 74c8f23 每小时中位数 |
|---|---|---|
| 0–4 小时 | 617 / 628 / 626 / 627 | 591 / 598 / 612 / 615 |
| 4–13 小时 | — | 616 / 617 / 614 / 614 / 616 / 617 / 617 / 620 / 618 |

- 74c8f23 上线 2 小时后（共约 10 小时）：min 604.3，p5 611.8，中位数 616.1，p95 638.0，max 663.8，斜率 +0.4/h。平台期**约 616MB**，比 627MB 低约 11MB，比预计的约 25MB 少（books 缓存也许本来没有占满 27MB，或者释放的内存被分配器留住了）。累计：747 → 616MB（−131MB），容器上限 1024m，余量约 400MB。
- 0 点导出时 `DEPTH_UNCONFIRMED|SPREAD_REVIEW` 日志为 0。数据：`output/m23b-20261027-probe-4h.log`、`output/m23b-20261028-probe-now.log`。

#### 新发现：`sqlite3.OperationalError: database is locked`（待查，未确认是否是这次上线引入的）

- 本容器（74c8f23，10-27 19:59 CST 启动）从启动起就有，每小时约 115–190 次，一直持续，分布均匀，没有集中爆发；不伴随 OOM。60 分钟内的 traceback 构成：Gate 567 约 600，locked 153，ConnectTimeout 25。
- 调用方：`announcements.process`/`repositories.create_if_new`（公告入库）、`set_provider_state`（公告轮询证据和 Hyperliquid 状态）、`history.record`（`history-recorder` 每小时失败若干次，下一代会重试，不会永久丢数据）。radar.db 只有后端进程打开，没有外部进程（备份容器已经退出）；`busy_timeout=5000`，说明进程内某个写事务持有锁超过 5 秒。
- **无法和 c36fca5 对比**：旧容器日志随容器一起删除了，部署脚本只导出了探针行。M2-3b 的改动不写库（`_last_incident` 去重语义等价，`record_incident` 次数不变），按代码判断引起这个问题的可能性很低。
- 相关背景：服务器只有 2 核，旧仓库 `~/arb-mvp` 的 node 进程（`src/main.ts --config config/app.example.json`，PID 814，开机起已运行 42 小时）一直占满约 1 核；后端 CPU 约 40%，负载 2.7。CPU 紧张会延长跨 await 的写事务持锁时间。
- 建议：另开一个只读排查任务，找出持锁超过 5 秒的写事务（候选：`history._prune`、公告批量入库、snapshot consumers），以后的部署脚本也要在部署前导出 locked 计数作为对照。是否停掉旧 arb-mvp 进程由用户决定。

### 5.12 `database is locked` 根因（10-28，只读排查，生产未改动）

- 约 80% 是 `SQLITE_BUSY_SNAPSHOT`（517）：6 小时内 941 条 locked 中有 744 条和 `Rolled back stale SQLite snapshot` 发生在同一秒。触发最多的是公告轮询。
- 根因：aiosqlite 的 `execute(SELECT)` 和 `fetch*()` 是两次排队操作，中间语句处于活跃状态，连接持有读快照；如果此时其他连接提交，而同一连接上另一个协程正好写入，就会立即得到 517。本地复现：现有写法 200 轮报错 193 次，在连接工厂的 `execute` 里当场读完结果集后为 0 次。只读 Codex 审计（`output/lock-audit-codex-20261028.md`）确认代码里没有未读完的游标，所有读取路径都受影响，所以要集中修复。
- 判断：这个问题和 M2-3b 无关，属于长期存在的问题（只要多个连接并发就会触发）。以前的部署检查没有统计这类报错，所以一直没发现。
- 修复任务书：`docs/sqlite-snapshot-fix-spec-2026-10-28.md`（worktree `.worktrees/sqlite-snapshot-fix`，分支 `codex/sqlite-snapshot-fix` @ 本次 docs 提交，基于 `74c8f23`）。只改 `backend/app/db/database.py`，4 个连接都生效。**等用户确认后**用 gpt-6-astra high 派发。
- 后续（不在本次范围）：`import_catalog` 在 `BEGIN IMMEDIATE` 中持锁做全表解析（每天一次）；history 写入失败时 generation 直接被消费（`snapshot_consumers.py:98–100`），样本会有缺口；Gate 公告 567 降噪；旧 arb-mvp node 进程占 1 核。

### 5.13 锁修复：开发完成并验收（用户确认任务书后派发，没推送、没上线）

- `codex/sqlite-snapshot-fix` @ `4ad88b7`（基于 `74c8f23`，前一个提交是任务书 `7b9e8b5`）。只改了 `backend/app/db/database.py`，新增 `_MaterializedCursor` 和 `_MaterializingConnection`，4 个连接都生效；新增 `tests/test_database_materialized_cursor.py` 和 worktree 内的交接文档 `docs/task-handoff-2026-10-28-sqlite-snapshot.md`。
- 验收：改动范围只有 3 个文件；Claude 亲自跑后端全量 2054 passed；新测试是确定性的，旧实现 200 轮报错 200 次，修复后 0 次。只读 Codex 审阅（`output/sqlite-fix-review-20261028.log`）没有发现 P0–P2 问题，只有一项 P3：`arraysize`/`fetchmany` 遇到非法参数时的校验和原生不同，业务里没有用到，不处理。Claude 通读了 `database.py`，没有问题。
- 部署脚本已准备好但没有执行：`output/sqlitefix-deploy-20261028.sh`。部署前记录 60 分钟内的 locked、rollback、history-recorder 失败次数，上线后在 +1h、+4h 再各记录一次，同时导出探针。**需要用户批准**：推送 `4ad88b7` 到部署分支 → CI → 部署（`.env` 不动）。

### 5.14 锁修复上线（用户批准，10-28 09:47 CST）

- 推送 `4ad88b7` 到部署分支（从 `74c8f23` 快进）→ CI run 40611871595 通过。
- **第一轮部署失败，生产没有受影响**：第 1 次尝试备份 radar.db 时报 `attempt to write a readonly database`（只读挂载的读连接碰上 WAL 重置，偶发）；第 2 次尝试卡了 29 分钟，原因是分步在线备份（`pages=256`）在持续写入下，源库每改一次就从头重来，永远拷不完（备份期间 WAL 从 9.6MB 涨到 77MB）。Claude 停掉了备份容器，部署按失败退出，旧版本继续运行。没拷完的 741MB 备份改名为 `backups/radar-before-4ad88b7de5b7-20261028T011021Z.db{,-journal}.partial-killed`，**留给用户决定是否删除**。失败日志：`output/sqlitefix-20261028-*-failed1.log`。
- 修复：新的部署脚本副本 `output/sqlitefix-isolated-deploy-20261028.sh`，改为 `source.backup(target, pages=-1)`，在一个读快照里一次拷完；integrity_check 和 SHA 校验保留。本地 137MB 库在持续写入下对比：分步备份 60 秒没拷完，一次性备份 1.1 秒完成，检查通过。**以后部署都用这个副本**，以前备份能成功只是写入较少时碰巧完成的。
- 重试一次就成功，备份不到 2 分钟完成：live/ready 200，9 个开关仍然生效，7 个后台任务都在。
- 上线前 60 分钟（74c8f23）：locked 274，rollback 69，history-recorder 失败 14。上线后 2 分钟：**locked 0，rollback 0，history 失败 0**；剩下的 traceback 都是公告上游噪声（Gate 567、Binance 429）。
- 采集：服务器脚本会在 +1h（约 10:47）、+4h（约 13:47）统计 locked、rollback、hist_fail，并导出探针 `output/sqlitefix-20261028-probe-{1,4}h.log`，和 616MB 平台期对比。

### 5.15 备份清理和并行任务书（10-28，生产未改动）

- 备份清理：`output/cleanup-backups-20261028.sh`（已上传到服务器，默认只列出不删除，加 `--apply` 才删除）。演练结果：79 个文件，约 14.6GB。保留最近两次上线前的备份（4ad88b7、74c8f23）及对应的挤仓库备份、所有 `.env` 备份，以及在用的库。**删除由用户亲自执行**。另外确认仓库里的 `deploy/linux-update.sh` 用的是 `source.backup(target)`，默认就是一次性拷贝；分步备份的问题只存在于 `output/` 下的副本脚本。
- M2-4b 任务书：`.worktrees/m2-4b-identity` @ `8bf1a70`（`docs/m2-4b-identity-registry-slim-spec-2026-10-28.md`）。核实结果：deep copy 只造成刷新时的短暂双份；常驻的 50MB 是 1.9 万个完整 `MarketBinding` 对象（包含 evidence），而 `annotate` 只用到 13 个字段，并且每次都要重算 fingerprint 的 sha256。方案：改用 frozen + slots 的精简条目，预先算好 fingerprint，预计 −30~40MB，CPU 也会略降。
- Gate 降噪任务书：`.worktrees/gate-announcement-noise` @ `4887e87`（`docs/gate-announcement-noise-spec-2026-10-28.md`）。每小时约 280 条 567 traceback，读取器回退正常；主机直接请求返回 200、容器内返回 567，推测是对 UA 的风控拦截。**不伪装 UA**；方案是按分类去重日志，加上 5 个分类全部失败时退避 300 秒。
- 两份都**等用户确认后**再用 gpt-6.1-sol xhigh 派发；上线要等锁修复 +4h 观察结束，逐个进行。

### 5.16 M2-4b 与 Gate 降噪：开发完成并验收（用户确认任务书后派发，都没推送、没上线）

- **M2-4b**：`codex/m2-4b-identity` @ `56f16b1`（基于 `4ad88b7`）。只改了 `market_identity.py`，新增 `_RegistryEntry`（frozen + slots，13 个字段，fingerprint 预先算好，字符串 intern），新增 `tests/test_market_identity_registry_slim.py` 和 worktree 内的交接文档。改动范围只有 3 个文件；全量 2069 passed（Claude 重跑结果相同）。只读审阅**通过，没有发现问题**：`annotate()` 源码和旧版完全一致，等价性测试里的参照实现和旧版逐字相同。体积：2 万条 binding 158.6MB → 7.4MB（−95%）。
- **Gate 降噪**：`codex/gate-announcement-noise` @ `c384ce9`（`f796648` + 并发修复）。只读审阅发现一项 P2：Gate 实例由 announcement-loop 和 enrichment-loop 共享，并发 fetch 时，一次全部失败的调用可能覆盖另一次调用期间已经恢复的状态，导致错误退避 300 秒。修复方法：记录 `_direct_success_at`，只有本次调用开始后没有任何直连成功时才写入退避，不加锁。修复前并发用例 2 个失败，修复后全量 2064 passed（2061 加 3 个新用例）。没有改 UA 或请求头。
- 验收规则调整（用户 10-28）：如果 Codex 日志里有全量命令和原始汇总行，Claude 就不再重跑全量。依据：两次对比结果完全一致。
- 上线顺序建议：锁修复 +4h 观察结束（约 13:47）→ M2-4b（内存收益大，单独测量）→ 间隔至少 1 小时 → Gate 降噪。都用 `output/sqlitefix-isolated-deploy-20261028.sh` 副本部署，**需要用户批准**。
- **锁修复 +1h（10:47 CST）**：locked 0、rollback 0、history-recorder 失败 0、重启 0、OOM 0（上线前 60 分钟分别是 274/69/14）。内存按相同容器年龄（10–60 分钟）对比，和 74c8f23 基本相同，这个修复本来就不影响内存。+4h（约 13:47）再确认一次平台期。

### 5.17 M2-4a 调查和任务书（10-28，生产未改动）

- 只读 Codex 实测（`output/m2-4a-investigation-20261028.log`）：1.2 万行中，原始快照独占约 36.4MiB，和标记副本共享的部分只有 4MiB，开销主要是每个对象约 1.6KB 的字段字典。aliases 不幂等，FX 和盘口缓存过期后也无法还原，所以**不能只保留标记结果再重新处理**。store 和 slots 没有额外复制。
- 方案：原始快照改成紧凑记录（字段 tuple 加 fields_set 位图），每轮用 `model_construct` 重建，实测约省 30MiB。任务书：`.worktrees/m2-4a-collector`（`docs/m2-4a-collector-compact-raw-spec-2026-10-28.md`），**等用户确认后**用 gpt-6-astra high 派发；排在 M2-4b 和 Gate 降噪之后上线。

**M2-4a 开发结果（10-28）**：worktree `.worktrees/m2-4a-collector`，ed8eb29（任务书）→ 53bbe8a（实现）→ dcce832（unpack 快速路径）。1.2 万行保留内存 37.9MB→10.4MB（−72.6%）；第一版 unpack 187ms 超出 150ms 上限，Claude 本地拆分后发现约 125ms 耗在 `model_construct`，改成 `__new__` 加直接设置 `__dict__`/`__pydantic_fields_set__`/extra/private（带前提检查，不满足就回退到 model_construct）后降到 67ms。全量 2062 passed（来自 Codex 日志），9 轮 collector 等价测试通过；只读审阅结论：无问题。注意：本地 pydantic 是 2.12.5，生产锁定版本是 2.14.1，快速路径的等价测试会在 CI（按 lock 安装依赖）中再验证一次。尚未推送和部署，排在 M2-4b 和 Gate 降噪之后。

**锁修复 +4h（10-28 13:47）**：restarts=0、oom=0、locked=0、rollback=0、hist_fail=0。**注意**：13:32 有另一个会话用 `output/astro-fresh-deploy-20261028.sh` 部署了 `020642c`（astro 手动预览 fresh，包含 4ad88b7），所以 4ad88b7 实际只运行到 13:32（约 3.75h），10:47 之后没有内存数据，+4h 探针采到的是 020642c 新容器的数据（从 13:32 开始）。4ad88b7 的内存结论仍以 10–60 分钟中位数 592.1MB 为准（与 74c8f23 同龄阶段持平）。**当前生产版本是 020642c**；M2-4b、Gate 降噪、M2-4a 上线前都要 cherry-pick 到 020642c 之上。

**M2-4b 上线（10-28 14:45 CST，dbd5bb8）**：集成分支是 6ad5c8e + M2-4b，外加修正 astro 测试从注册表取 MarketBinding 的写法（第一次推送的 d0441ce 有 8 个测试失败，已被 dbd5bb8 取代，没有部署 d0441ce）。集成全量 2102 passed，CI 通过。上线前 /api/markets 身份分布：pending 11347、verified 916、expired 4。**初步内存（rss_anon 中位数）**：5–10 分钟 535、10–20 分钟 538、20–40 分钟 541；020642c 同龄阶段是 597、598、600，**约 −60MB**，比预期的 30–40MB 多。
**020642c 内存偏高的核查**：之前说的 642MB 是 docker stats 的 cgroup 数字，包含 page cache，不能和 rss_anon 比。按容器年龄比 rss_anon，020642c 是 597–601，4ad88b7 是 588–594，只高约 7MB，astro 改动的影响很小。
**CPU（py-spy，宿主机 ~/.local/bin/py-spy）**：uvicorn 稳定在约 0.55 个核。采集循环约 0.26 个核：validate 0.11（其中 validate_pair 0.09）、prepare 0.10、MarketKey.key 0.05（其中 json.dumps 0.04）、pydantic 构造 0.06、多交易所溢价 0.04、构建机会 0.04、fs_public 0.03。`/api/instruments/WUSDT` 每次只要约 30ms，可以忽略。旧仓库 arb-mvp 的 node 进程占 0.95 个核（由用户决定是否处理）。只读调查报告在 `output/cpu-hotspots-investigation-20261028.log`。
**history 重试（10-28，`.worktrees/history-retry`，f3e01f5 任务书 → 79607f6 只含核查文档）**：Codex 按任务书 §2-5 先做前提核查，结论是**前提不满足，没有实现 retry**。原因：`insert_many`（`repositories.py:1621`）的 executemany 失败后不回滚，写了一半的行留在共享连接 db 的开放事务里，之后会被同一连接上其他 repository 的 commit 一起提交（例如 `SettingsRepository.set_risk_settings`）；history 表也没有唯一键，重放会产生重复行；`_prune()` 在插入提交之后还可能失败。已用内存库和触发器做故障注入复现。全量 2102 passed（代码没有改动）。是否修，需要先设计共享连接上的事务边界，由用户决定，优先级低（锁修复后 hist_fail=0）。
**CPU 热点调查结论（只读 Codex，`output/cpu-hotspots-investigation-20261028.log`）**：采集间隔是“本轮耗时 + 5 秒”。热点来自大量重复构造对象：每轮约 4.7 万次 `MarketKey.key` 的 json.dumps 和 5.2 万次 MarketKey 构造；validate 阶段对每个机会重新标注两条腿（约 5000 次），约 1 万次 model_copy；validate_pair 约 1 万个 ValidationIssue；溢价监控每批最多做 17 次全量扫描。按收益和风险排序：①MarketKey.key 用有界缓存保存 JSON 字符串（约 0.025–0.037 核，低风险，1 个文件）；②溢价监控复用按 generation 建的索引（0.01–0.025 核，中风险）；③跳过 collision 副本和批内的标注复用（0.005–0.02 核）。跨轮缓存 pair 校验结果收益最大（0.02–0.05 核），但风险高，暂缓。前三项合计约 0.04–0.08 核（约占 uvicorn 的 10%），比停掉 arb-mvp 的 node（0.95 核）小一个数量级。另外，Astro 401 轮询来自 `FloatingWatchPanel.tsx:662`，每 10 秒加一次刷新耗时，401 后不会停止，可以另开一个小任务处理（主要是减少无效请求和日志，不是 CPU 的主要来源）。
**M2-4b +1h（15:45）**：restarts=0、oom=0、locked=0；rss_anon 中位数 40–60 分钟 540（020642c 同龄 601，4ad88b7 同龄 594），**约 −61MB，已低于 600MB 目标**。身份分布前后一致（pending 11346、verified 916、expired 4，总行数差 1 是行情正常波动）。tb=651/h，主要是 Gate 567（下一项 Gate 降噪处理）。+4h 统计约 18:45。Gate 降噪集成分支 `codex/deploy-gate-noise` 11ee863（基于 dbd5bb8，全量 2112 passed）、M2-4a 集成分支 `codex/deploy-m2-4a` 8779d40（基于 11ee863，全量 2120 passed），都还没推送。
**Gate 降噪上线（10-28 16:11 CST，11ee863，部署脚本 `output/gate-noise-deploy-20261028.sh`）**：部署前 60 分钟 gate_fail=620；上线后前几分钟 5 个分类各打了 1 条 WARNING，之后进入退避，符合设计。+1h 统计约 17:12。注意：M2-4b 脚本的 +4h 探针（约 18:45）采到的是 11ee863 容器的数据（同样包含 M2-4b），按容器年龄比较即可。
**Gate 降噪 +1h（17:11）**：restarts=0、oom=0、locked=0；最近 60 分钟 `failed to fetch gate announcement category` 为 **0**（上线前 620/h），Traceback 总数从 651/h 降到 42/h。Gate 公告照常入库（最新一条 09:02:25Z 抓取，source=gate-next-announcements）。剩下的 traceback：HL 公告 ConnectTimeout 7、gate 读取器回退偶发失败 6、Bybit 本地化失败 3、Bitget/OKX ValueError 3。
**M2-4b + Gate 平台期（18:45）**：M2-4b 脚本 +4h：restarts=0、oom=0、locked=0、tb=44/h。按容器年龄统计 11ee863（包含 M2-4b）的 rss_anon 中位数：20–40 分钟 550、60–90 分钟 547、90–120 分钟 547、120–180 分钟 548，**平台期约 548MB，1–3 小时之间没有上涨**（74c8f23 同期 594→612）。之后上线 M2-4a（8779d40）。
**M2-4a 上线（10-28 18:56 CST，8779d40，`output/m24a-deploy-20261028.sh`）**：CI 通过，live 和 ready 都是 200，.env 没动。Gate 观察脚本在它 +4h 之前已被 Claude 停掉（日志末尾有标注），因为容器已经换了。+1h 统计约 19:57，届时按容器年龄和 548MB 平台期对比，并用 py-spy 确认 collector CPU 没有上升（`/api/health/detail` 需要密码，拿不到 stage_timings）。
**MarketKey 编码缓存（`.worktrees/marketkey-cache`，55dac29 任务书 → a5e06bc）**：第一版同时缓存了 from_snapshot 构造的对象，两个缓存合计 12.5MB，共享实例还导致注册表隔离和释放测试失败，没有提交。第二版只给 `.key` 的 json.dumps 加了 `lru_cache(32768)`（核心改动 8 行，Claude 已通读）。1.24 万个 key 的缓存保留内存 3.26MB；1.24 万行 × 4 次读取的耗时：旧实现 136ms，冷缓存 53ms，热缓存 29ms。后端全量 2153 passed。待上线。
**浮窗 Astro 401（`.worktrees/astro-401-poll`，7db0456 → 3af2af3）**：收到面板密码 401 后暂停 astro pairs 请求，密码变化或手动刷新后恢复。前端全量 296 passed，tsc 通过。只读审阅发现一个 P2：旧请求晚到的 401 会把新密码也标记为阻断。已交给 Codex 用请求序号和发起时的密码修复（进行中）。
**M2-4a 测量被打断（19:56）**：另一个会话在 19:2x 依次部署了 7f65f4d/eb5d839/457ae12（astro 手动建卡提速，都包含 8779d40），19:56 又开始部署 6b146b2（loop stall watchdog）。8779d40 自己的容器只跑了约 30 分钟，没有单独留下探针。m24a 脚本的 +1h 探针采到的是 457ae12 容器（运行 28 分钟），rss_anon 中位数为 5–10 分钟 548、10–20 分钟 545、20–40 分钟 544；11ee863 同龄是 540、544、550，**看不出 M2-4a 预期的 −25~30MB**。可能是被 astro 的改动抵消了，也可能是释放的堆没有还给操作系统，目前无法分开。uvicorn CPU 60 秒采样约 0.50 核（之前约 0.55）。需要等部署停下来后，按容器年龄看 2–4 小时的平台期再下结论。**之后部署前必须先确认没有其他会话正在部署**（看 `output/*-run.log` 和 `pgrep -af deploy`）。

**10-29 早上更新**：另一个会话昨晚 20:08 部署了 6b146b2（loop stall watchdog），之后停止部署，6b146b2 一直跑到今天 08:32，随后又部署了 ddda20b（collector 让出事件循环、固定 mapping epoch）。用 m24a 脚本的 +4h 探针（覆盖 6b146b2 前 3 小时）按容器年龄看 rss_anon：20–40 分钟 554、60–90 分钟 556、90–120 分钟 556、120–180 分钟 558；11ee863（M2-4b+Gate）同龄为 550、547、547、548。所以 **M2-4a + astro 提速 + loop-stall 合计约 +8MB**，M2-4a 预期的 −30MB 看不出来，但整体仍在 600MB 以下，1–3 小时之间也没有上涨。ddda20b 运行约 20 分钟时为 558。
MarketKey 缓存和浮窗 Astro 401 已经叠到 8950523 上（集成分支 `codex/deploy-mk-astro401`，HEAD 3e85b69），全量测试进行中。用户的决定：等 astro 会话停止部署（容器连续稳定 3 小时、没有部署进程）后再上线。
