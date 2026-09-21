# 浮窗账户持仓展示与屏蔽交接

## 目标与范围

本任务只处理“浮窗账户持仓展示与屏蔽”模块：在关注行情浮窗中展示项目已明确支持的交易所账户真实未平仓持仓，并允许用户只在浮窗中屏蔽、查看和恢复指定持仓。

本任务没有修改 Astro 卡片、告警、订单、平仓、交易所仓位或其他页面。当前仓库只有 Gate 已有可验证的私有账户凭据接入，因此生产实现只注册 Gate 持仓 provider；未伪造 Binance、OKX、Bybit、Bitget 或 Hyperliquid 私有账户数据。

## 分支与基线

- 分支：`codex/frontend-localization-polish`
- 开始基线：`4429560`（`docs: update trade alert diagnostics handoff`）
- 功能提交：`1430d60`（`feat: show account positions in floating watch`）
- 持久化修复：`92ec702`（`fix: preserve hidden positions on watch updates`）
- 最终生产版本：`92ec70262146932186c2281188aa30c77a7f6850`

## 已完成功能

- 新增 `GET /api/account-positions`，并发聚合所有已注册账户 provider；单个账户失败不会阻断其他账户。
- 当前 Gate provider 直接调用已认证的 `/futures/usdt/positions?holding=true`，不从 Astro、关注标的或订单记录推导仓位。
- Gate provider 同时查询合约元数据中的 `quanto_multiplier`，展示实际张数、市场倍率和换算后的基础币数量。
- 支持 Gate 单向和 `dual_long` / `dual_short` 双向持仓；同一市场的多仓、空仓保持不同身份。
- 账户状态明确区分 `not_configured`、`ok`、`empty`、`permission_denied`、`error` 和 `stale`。
- 成功快照在进程内缓存；后续接口失败时只返回标记为 `stale` 的最近成功快照和年龄，不把旧数据冒充实时数据。
- 新增浮窗“持仓 N”标签，展示账户、交易所、原始/规范化标的、市场类型、DEX、方向、数量、均价、标记价、名义价值、未实现盈亏、收益率、杠杆、倍率、张数、价格口径、更新时间和新鲜度。
- 交易所未提供的值显示 `--`；从未实现盈亏和保证金计算的 Gate 收益率显示“收益率（估）”。
- 每个持仓提供 EyeInvisible 图标和 Tooltip；屏蔽只写入浮窗设置，不下单、不平仓，也不改变 Astro、告警或后端持仓采集。
- “已屏蔽”入口显示完整持仓身份并支持逐个恢复；已平仓身份继续保存在 SQLite，未来相同身份重新开仓仍保持屏蔽。
- 屏蔽配置保存在现有 SQLite `app_settings` 中；已验证页面刷新、独立窗口、服务重启以及增删关注标的后仍然保留。
- 账户标识使用配置别名的不可逆短哈希；API、页面和日志不返回 API Key、Secret 或上游敏感错误正文。

## 关键代码入口

- `backend/app/models/account_position.py`
  - 定义持仓、账户状态、新鲜度和稳定身份哈希。
- `backend/app/services/account_positions.py`
  - 账户 provider 协议、Gate 真实持仓解析、并发聚合、部分失败和旧快照降级。
- `backend/app/services/gate_twap.py`
  - Gate 私有持仓与公开合约元数据请求；HTTP 错误只保留状态码和清洗后的错误。
- `backend/app/api/routes_account_positions.py`
  - `GET /api/account-positions`。
- `backend/app/api/routes_settings.py`
  - 密码保护的 `POST /api/settings/floating-watch/positions`。
- `backend/app/db/repositories.py`
  - SQLite 屏蔽身份增删，并在普通关注项变更时保留 `hidden_positions`。
- `backend/app/main.py`
  - 注册 Gate provider、账户持仓服务和路由。
- `frontend/src/components/FloatingWatchPanel.tsx`
  - 持仓标签、字段、账户状态、屏蔽和恢复交互。
- `frontend/src/api/client.ts`、`frontend/src/api/types.ts`
  - 持仓与屏蔽设置 API 类型和校验。
- `frontend/src/styles.css`
  - 紧凑持仓布局、状态样式和窄屏约束。
- `backend/tests/test_account_positions.py`、`frontend/tests/FloatingWatchPanel.test.tsx`
  - 后端身份/聚合/持久化与前端展示/交互回归覆盖。

## 重要业务规则

- 持仓唯一身份由账户、交易所、市场类型、原始市场、方向和 DEX 共同生成；规范化 ticker 和显示名称不参与替代原始身份。
- 不同账户、交易所、原始市场或方向即使规范化 ticker 相同，也不能合并或互相误屏蔽。
- Hyperliquid 持仓身份强制要求显式 `main` 或具体 builder-deployed DEX；非 Hyperliquid 身份禁止携带 DEX。当前尚未注册 Hyperliquid 私有账户 provider。
- Gate `size` 保留原始合约张数；有 `quanto_multiplier` 时数量换算为基础币，没有倍率时数量单位明确显示为“张”。
- 价格口径固定显示 `Gate 标记价（mark_price）`；名义价值优先使用交易所 `value`，缺失且倍率、标记价可用时才估算并标记估算字段。
- 只有一次真实账户接口成功返回空列表才能标记 `empty`；未配置、无权限、超时和接口错误都不能显示为“当前无持仓”。
- 屏蔽配置最多保存 200 个完整身份，只影响前端浮窗过滤；后端仍持续查询该持仓。

## 测试结果

- 后端全量测试：`746 passed, 11 warnings in 1160.20s`。
- 最终持久化修复后的专项测试：`backend/tests/test_account_positions.py backend/tests/test_floating_watch.py` 为 `8 passed, 2 warnings in 49.97s`。
- 前端浮窗专项测试：`FloatingWatchPanel.test.tsx` 为 `21 passed`。
- 前端全量测试：`164 passed, 1 failed`；唯一失败是既有 `SettingsPage` 用例仍查找旧文案“实盘灰度”，与本任务文件和调用链无关。
- `npm run build`：TypeScript 检查和 Vite 生产构建通过；服务器 Docker 前后端生产构建也通过。
- `git diff --check`：功能提交和修复提交前均通过。
- 本地视觉检查：900px 和 390px 均无文字重叠或页面级横向溢出；模拟真实持仓卡片覆盖长原始市场、不同市场身份和屏蔽/恢复视图。
- 生产视觉检查：900px 与 390px 的 `document.scrollWidth === clientWidth`，四个标签均完整；390px 下每个标签宽 92px，页面显示明确的“尚未配置支持持仓读取的账户”状态。

## 数据库备份

最终部署前使用 SQLite Backup API 创建一致性备份：

- 文件：`backups/radar-20260921T112742Z-pre-account-positions-final.db`
- 大小：`636829696` 字节
- SHA-256：`ef248a9644e489ba328c7ba10c3f50cbd716a813a216b25d893df9cc6f994418`
- SQLite `PRAGMA quick_check`：`ok`

首次功能部署前另有备份 `backups/radar-20260921T112210Z-pre-account-positions.db`，大小 `651866112` 字节，SHA-256 `68b49ec3c0925207b389bd0ec3e4373551e3811035c7b15ef30a19a433677dc9`，`quick_check=ok`。

## 线上状态

- 服务器通过 `git pull --ff-only origin codex/frontend-localization-polish` 更新到 `92ec70262146932186c2281188aa30c77a7f6850`。
- 首次部署执行完整 `docker compose build --pull`；最终修复部署重建 backend，并执行 `docker compose up -d --remove-orphans`。没有执行 `docker compose down -v`。
- backend、frontend 容器均为 `healthy`。
- backend 和 frontend 代理的 `/api/health` 均返回 `status=ok`；验收时 8 个公共行情交易所状态均为 `healthy`，`exchange_errors={}`。
- backend 和 frontend 代理的 `/api/account-positions` 均正常返回。
- 生产 `.env` 和运行容器未检测到非空 `GATE_API_KEY`、`GATE_API_SECRET` 或 `GATE_ACCOUNT_ID`，因此真实结果为 `positions=[]`、Gate `configured=false`、`state=not_configured`、消息“尚未配置账户凭据”。这不是“账户已核验且当前无持仓”。
- 在线屏蔽持久化使用一个明确标记的合成身份验证：初始 0，屏蔽后 1，重启 backend 后仍为 1，执行一次关注标的更新后仍为 1，恢复后回到 0；没有改变原有配置，也没有触发任何交易行为。
- 生产截图保存在未跟踪的 `output/floating-account-positions-production-desktop.png` 和 `output/floating-account-positions-production-mobile.png`。

## 已知问题与残余风险

- 当前只有 Gate 具备项目内已实现、已验证的私有持仓 provider；其他交易所只有公共行情能力时不会出现在账户持仓中。新增交易所必须先实现并验证其真实私有账户接口、权限错误、市场身份和双向持仓语义。
- 生产没有配置 Gate 凭据，因此无法在线验证真实 Gate 持仓字段，也无法在生产 UI 点击真实持仓的屏蔽按钮。真实数据解析、同 ticker 不同市场、双向持仓、屏蔽和恢复由专项测试及本地模拟真实卡片覆盖；配置只读凭据后仍应补一次真实账户验收。
- 成功快照缓存当前仅在 backend 进程内；服务重启后第一次查询失败时没有可展示的旧快照，但会明确返回错误而不是“无持仓”。
- 前端全量测试仍有上述设置页旧文案基线失败，应在独立设置模块任务中处理。

## 未跟踪文件

本任务没有提交、删除或覆盖既有本地产物。与本任务直接相关的未跟踪验收产物：

- `output/floating-account-positions-local-desktop.png`
- `output/floating-account-positions-local-mobile.png`
- `output/floating-account-positions-production-desktop.png`
- `output/floating-account-positions-production-mobile.png`
- `output/edge-account-position-visual/`

其他既有未跟踪产物仍保留，包括：

- `output/edge-profile-codex/`
- `output/dexe_bybit_bitget_chain_probe/`
- `output/floating-watch-*.png`
- `output/index-auto-watch-*.png`
- `output/trade-availability-*.png`
- `output/new-listing-*.png`
- `script/dexe_bybit_bitget_chain.py`

服务器继续保留既有未跟踪运维文件：

- `.env.backup-codex-20260911-1425`
- `.env.backup-poll-8-20260911`
- `CACHED`

## 下一步建议

- 配置 Gate 只读 API Key、Secret 和稳定的 `GATE_ACCOUNT_ID` 后，重新验证真实多/空持仓字段、权限状态、屏蔽、恢复以及同一身份平仓后再开仓的行为。
- 后续增加其他交易所账户持仓时，每个交易所单独实现 provider 和契约测试；Hyperliquid 必须把 `main` 与每个 builder-deployed DEX 分开。
- 下一个功能模块请新建 Codex 任务，并同时提供根目录 `AGENTS.md`、平台总交接文档和本文件。
