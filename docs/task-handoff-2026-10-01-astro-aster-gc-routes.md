# Astro Aster 混合 GC 路线交接（2026-10-01）

## 目标、范围与基线

- 模块：Astro 建卡路线。修复 Aster 配对在全局选择“仅 GC”时被错误跳过的问题。
- 用户确认 Aster 没有独立 GC ID，应保留 `aster`，仅将支持 GC 的另一腿改为对应 GC ID，例如 `gate -> aster` 的 GC 路线是 `gc-gate -> aster`。
- 分支：`codex/frontend-localization-polish`；本地基线 `7b278fb`，开始时生产源码及双镜像为 `7acfdd98b5aa232fad8343c53ae6df87159c4d5d`。
- 不修改全局“仅 GC”偏好、开卡状态、交易金额、资金费周期/估算、费用、市场倍率或行情筛选规则；不另行开启真实交易。

## 根因与已完成实现

- 旧实现仅允许 Bitget 在混合 GC 路线上保留原 ID，遗漏 Aster；`gate -> aster` 只生成普通路线，选择 GC 时为空，返回“所选 GC/非 GC 卡片类型在当前路线上不可用”。此前将其解释为纯设置冲突不完整，用户确认的路线语义说明这是支持表遗漏。
- `backend/app/services/market_labels.py`：将无独立 GC ID 的已知交易所归为 `_ASTRO_NON_GC_EXCHANGE_IDS = {aster, bitget, bitgetr}`，双向生成普通和混合 GC 路线；适用于 Binance、Bybit、Gate、Hyperliquid、Lighter、OKX 对手。
- `backend/app/services/astro_alerts.py` 无需修改：告警、实盘实验、人工建卡、预建共用路线函数，查重与补建沿用现有逻辑。
- `backend/app/api/routes_astro.py` 无需修改：预览使用相同路线函数，将包含至少一个 GC ID 的混合路线标记为 GC。
- `frontend/src/pages/SettingsPage.tsx`：默认建卡路线帮助文字解释 Aster/Bitget 保留原 ID 的规则。
- `backend/app/services/astro_planner.py`、`backend/app/services/instrument_spreads.py`：同步 Lighter 未知对手的阻止说明，避免遗漏已经支持的 Aster。

## 业务规则与安全边界

- `non_gc`：`gate -> aster`；`gc`：`gc-gate -> aster`；`both`：各建一张。反向亦然，永不生成 `gc-aster`。
- 只补缺失的所选路线，不修改已有卡片。现有非 GC 同族卡片不阻止补建 GC，反向同理。
- Hyperliquid 原始市场与具体 DEX 按原逻辑保留。缺失原始市场、DEX 冲突、黑名单、dry-run、`rh-lighter` 只读保护保持不变。
- Lighter 与已知 Aster 配对现在允许普通及混合 GC 路线；未知/未确认的对手仍被拦截。原以 Aster 为未知对手的测试改用 HTX，保护范围没有全面放开。
- 截图的 `FUNDING_AGAINST_MARK_INDEX_DEVIATION` 风险信息不是此次路线筛选失败的直接原因；未删除或弱化该风险信息。

## 测试与审查

- 先新增回归测试：修复前 Aster 定向测试为 35 失败、8 通过，明确复现缺失路线、GC 拦截、同族查重和预览问题。
- 新覆盖：6 个 GC 对手双向路线和三种类型、4 个建卡入口双向三种类型、同族补建、Hyperliquid DEX 保留、标的查询预览正确标记混合 GC。
- 后端 `test_market_labels.py`、`test_astro_planner.py`、`test_astro_alerts.py`、`test_astro_instrument_routes.py`、`test_astro_preadd.py`、`test_instrument_spreads.py`：178 项通过。后续仅同步后端阻止说明后，planner/instrument-spreads 30 项再次通过。
- 前端 `SettingsPage.test.tsx`、`DashboardPage.test.tsx`：33 项通过；新增设置帮助文字断言后，该设置保存用例再次通过。TypeScript 检查与 Vite 生产构建通过。测试日志仍有非失败的 React act 提示，未扩大范围修改它。
- 修改文件的 `ruff check` 和 `git diff --check` 通过。手工复核路线白名单、参数保留和同族查重，无新增外部请求或秘密输出。

## 线上状态与验收

- 部署前只读确认：自动建卡开启，默认类型为 `gc`，普通告警默认卡片为暂停/禁开；Astro 只读列表请求成功，未找到 ZCAT 卡片。
- 必须等待双镜像完成，先备份并校验线上 `/data/radar.db`，再按 `docs/linux-deployment.md` 快进更新和使用预构建镜像启动 Compose；禁止生产机现场构建与删除数据卷。
- 功能提交 `09f2a30e920c2aa0b985889a6d23e8947e596f8e` 已提交并推送到既有分支，没有 force push。本文后续验收记录提交仅修改交接文档，不改变应用镜像版本。
- GitHub Actions `36873162008` 的 backend/frontend 两个构建任务均成功；两镜像 manifest 在服务器校验通过。
- `deploy/linux-update.sh` 在快进更新前创建主库备份 `backups/radar-before-09f2a30e920c-20261001T140417Z.db`，`integrity_check=ok`，容器与主机 SHA-256 一致：`0c5dd45bde6597827368783f78b53560513fe43abb2570af3e217420094fddcc`。
- 另备份 `backups/squeeze-route-before-09f2a30e920c-20261001T140417Z.db`，完整性校验通过，容器与主机 SHA-256 一致：`6de1e0b8ef6e1f81b986e5995d9e93c3994feff47433b3212a42316c87e37072`。原保留策略各清理一份过期自动备份，新备份保留。
- 服务器使用 `git pull --ff-only` 更新到功能提交，拉取双镜像后以 `up -d --no-build --wait` 启动；没有现场构建、删除数据卷或改写环境设置。双容器 healthy，运行镜像均为 `09f2a30e920c2aa0b985889a6d23e8947e596f8e`。
- 线上 `/api/health` 返回 `status=ok`、11792 个市场、10362 个机会，`exchange_errors={}`；前端首页 HTTP 200，新设置帮助文字已存在于线上 JS 资源。
- 实际 `POST /api/astro/instrument/preview`：`ZCATUSDT gate -> aster` 及 BTC 同方向均返回 `can_submit=true`、普通路线和 `gc-gate -> aster` 的 GC 路线；BTC 反向返回 `aster -> gc-gate`。默认配置仍为 `card_variant=gc`、`open_enabled=false`，无需改成非 GC。
- Astro 只读列表接口 HTTP 200；验收时未观察到 ZCAT 卡片，未人为重放告警或调用真实建卡接口。真实 SDK 混合路线写入/重启及交易行为仍需在正常业务触发中观察，不能将预览成功当成外部写入或成交的证明。

## 工作区、已知限制与后续

- 保留原有 `.worktrees/`、`output/`、`script/`、其他模块交接文档及 pytest 临时目录等未跟踪产物；只暂存本任务文件。
- 本次不重放历史告警，不人为添加真实卡片或开启交易。后续告警按新路线选择和原风险/查重规则执行；外部 SDK 写入与实际成交应在正常业务流继续观察。
- 其他功能模块请在新任务中继续，交接入口为本文件。连接信息与秘密仅使用受控运行环境，不写入仓库。
