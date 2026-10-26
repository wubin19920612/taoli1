# taoli1 会话交接：M2 内存治理与功能裁剪（2026-10-26）

新会话先读本文件；更早的细节见 `docs/task-handoff-2026-10-25-session-summary.md` §1–§12（分支 `codex/memory-fix`）。分工不变：Claude 负责设计、用桌面版 `codex.exe exec` 派发、亲自验收和部署；Codex 负责写代码和测试；Claude 只在改动不超过 20 行时直接修改。回复用户一律用中文。

## 1 生产现状

| 项 | 值 |
|---|---|
| 版本 | `cfd79d6`（M2-1 缓存治理 + S9 CI 门禁 + M2-2 普查代码），部署分支 `codex/frontend-localization-polish` |
| `.env` 中的内存相关键 | `MEMORY_PROBE_ENABLED=true`；普查已关闭（`MEMORY_CENSUS_*` 已删除） |
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
