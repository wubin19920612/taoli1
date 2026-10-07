# Astro / RH Lighter 人工建卡解限交接

日期：2026-10-07（Asia/Shanghai）。

## 目标与范围

- 用户要求解除 OPENAIUSDT 的 Bitget → RH Lighter 人工建卡限制。原来能读取公开行情、计算价差，但所有包含 RH 的建卡入口被后端一律拒绝。
- 本任务只开放用户主动确认的人工建卡。自动告警建卡、自动预建、新币自动路径和实盘实验仍受保护，避免部署后后台自动新增或启用 RH 卡片。
- 不改面板密码、Astro API 密钥、账户连接、交易参数默认值、现有卡片状态或订单执行代码；验收不实际创建、更新、暂停或启用线上 Astro 卡片，不发送订单。

## 分支、基线与交付

- 分支：`codex/frontend-localization-polish`。
- 本地开始基线：`3adb225166ea188a4ef99312cf8944f6d4539e72`。
- 开始时生产 HEAD / 两份镜像：`f8e871ae76d429700b7f7da0685c150055f81e4c`，服务器跟踪文件干净。
- 功能提交：`fe88e38a7e1c465974e5ecd633f3274258688ba7`，已推送当前 GitHub 分支。
- 生产镜像工作流：`Build production images`，run `37569566828`，backend/frontend 两个 job 均 success；服务器部署前分别通过两份固定 SHA 镜像的 manifest 检查。
- 验收记录以独立文档提交补充并推送（`[skip ci]`）；生产 HEAD 和两份镜像保持功能提交，不为文档再次重启服务。

## 证据、实现与入口

- 只读查询线上 Astro SDK 卡片列表，确认有 9 张卡片使用独立 `rh-lighter` 路线，其中包括已有 `bitget → rh-lighter`、普通/GC Lighter 与 RH、HL 与 RH 路线。没有用普通 Lighter 代替 RH，没有以 Astro 卡片作为报价源。
- `backend/app/api/routes_astro.py`：移除人工预览和人工提交的 RH 一刀切阻止；仍保留面板密码、实时快照身份、参数校验、行情变化与盘口诊断。两个人工入口共享服务，分别是机会卡片和精确标的卡片。
- `backend/app/services/astro_alerts.py`：将全入口只读限制改成明确的自动路径保护；只有已有 `handle_manual_create` 的 `manual_override=True` 可以放行 RH。人工总开关、dry-run、已有卡片跳过、HL DEX 冲突处理仍保留。
- `backend/app/services/market_labels.py`：将 `rh-lighter` 列为已知保留原 ID 的非 GC 交易所。与普通 Lighter/GC Lighter 配对时不再被当成未知对手；混合 GC 只改变支持 GC 的另一腿，绝不制造 `gc-rh-lighter` 或将 RH 归并到普通 Lighter。
- `backend/tests/test_market_labels.py`：双向、普通/GC 对手、非 GC 对手及不存在的 RH GC 路线矩阵。
- `backend/tests/test_astro_alerts.py`：人工建卡双向通过、默认暂停、已有卡片不覆盖、不可用 GC 选择拒绝、HL `io:OAI` 与 RH `OPENAI` 的 FR/DEX 身份保留、自动路径仍不调用 SDK。
- `backend/tests/test_astro_instrument_routes.py`：预览与鉴权提交矩阵；完整 API → 实际人工服务 → 隔离 SDK recorder 的 OPENAI 建卡测试，沿用用户截图的 3000/5/20/30 输入，确认输出暂停卡片及原始 `bitget → rh-lighter` 身份。
- `frontend/tests/InstrumentLookupPage.test.tsx`：精确 RH 路线的人工确认、默认不开仓和非 GC 请求回归。前端产品代码无需改动，继续消费服务器预览结果。

## 重要业务规则

- 手工确认后才可能调用 Astro SDK `action=add`；用户不确认不会写入。默认开仓状态保持原设置，未替用户打开“创建后允许开仓”。
- 预览由 `can_submit=false` 的旧 RH 全局限制转为正常业务判断；普通构建、人工开关、dry-run 和真实 SDK 可用性仍分别约束提交，不保证账户能够实际成交。
- RH 仍是独立实例。保留原始市场、双方成交额、真实 bid/ask、各自资金费率周期、价格/合约倍率与手续费诊断；未把公开 active 状态或盘口当成账户交易权限。
- Bitget ↔ RH 两腿均是非 GC，只能生成原始路线；选择不可用 GC 仍会拒绝，不伪造交易所 ID。
- HL 腿仍保留具体 DEX，`io:OAI` 不变成同名主站市场，RH `OPENAI` 不被隐式改成 `OAI`。FR 类型、方向和倍率规则未改。
- 同名、同类型、同精确路线已存在时返回 `action=existing`，不重建或覆盖现有卡片；这不再是只读限制。
- 自动路径继续使用 `AUTOMATIC_ASTRO_BLOCKED_EXCHANGES` / `AUTOMATIC_ASTRO_BLOCK_MESSAGE`；自动放开需要另开任务明确范围。

## 测试与构建

- 先在旧实现上复现 RH 开放断言与路由矩阵：27 failed、6 passed。
- 六个 Astro 相关后端测试文件：211 passed。新增完整 SDK 隔离链路并补齐成功盘口测试夹具的必填字段后，人工 API 文件最终整文件复测：24 passed（393.15s）；另有 RH 定向复测 7 passed（117.02s）。这两次复测与前述回归有重叠，不将执行次数误算为不同测试数量。
- 前端 `InstrumentLookupPage`、`DashboardPage`、`SettingsPage`、`FundingArbitragePreadd`：65 passed。
- `npm run build`：TypeScript 检查和 Vite 生产构建通过；定向 Ruff `--select F,E9` 与 `git diff --check` 通过。
- 保留既有 React act、Ant Design 属性废弃与 FastAPI/Starlette 警告，不扩展修复无关事项。

## 线上状态与验收

- 部署前 SQLite 备份：`backups/radar-before-fe88e38a7e1c-20261007T040526Z.db`，611766272 bytes，`integrity_check=ok`，宿主/容器 SHA-256 一致：`6caa843eff9b6b1baa62610d3736d71a775a80a7ebb6cbadf6845f4c8db1abb9`。
- 独立研究库备份：`backups/squeeze-route-before-fe88e38a7e1c-20261007T040526Z.db`，241664 bytes，`integrity_check=ok`，宿主/容器 SHA-256 一致：`94f40e3d3e15f9ec13e4770591f647c219e45e0ac1791d1da52577206d8b0d6b`。
- `deploy/linux-update.sh` 完成 `git pull --ff-only`、拉取预构建镜像与 Compose 启动；生产 HEAD / 前后端镜像均为 `fe88e38a7e1c465974e5ecd633f3274258688ba7`，跟踪文件干净；两容器 healthy。无生产构建、无卷删除，备份保留策略本次删除 0 文件。
- 后端直连和前端代理 `/api/health` 均 `status=ok`，11769 个市场，RH/Bitget 等 9 个交易所健康，`exchange_errors={}`；首页 HTTP 200。
- 真实 OPENAI 人工预览：`can_submit=true`、blockers 为空、原生 `bitget → rh-lighter`、仅非 GC 路线，`status=false` / `disableOpen=true`。缺少或无效面板密码的真实建卡请求分别返回 401。
- 390px 手机和 1440px 桌面浏览器均通过：允许提交为“是”、非 GC 确认按钮可用、默认不开仓、不保存全局默认值、路由准确、无横向页面溢出与 pageerror；提交各拦截 1 次，确认前后 Astro 静态配置摘要一致。已人工检查稳定后的手机预览/按钮与桌面截图。
- 只读确认：线上已经存在 `OPENAI / FF / bitget → rh-lighter` 卡片；用户再提交同一精确路线会按既有规则返回 `action=existing`，不覆盖旧参数或开仓状态。此行为与已解除的 RH 人工限制不同。
- 验收只允许真实 GET / 人工预览 POST；建卡按钮的提交由浏览器测试拦截，真实建卡接口只用无效密码验证 401，不进入 SDK 写入。线上 SDK 配置摘要用于前后对照，不在仓库保存密码或完整私有配置。
- 生产必须先校验 SQLite 备份、`git pull --ff-only`，再使用两份固定 SHA 预构建镜像启动 Compose；2 GiB 服务器不运行本地构建，不删除卷。

## 边界、未跟踪文件与下一步

- 本次开放“能人工提交创建”，没有验收实际 SDK 新增或交易成交；SDK `add` 可能重启 Astro core 的既有语义不变。
- 用户截图中的 OPENAI 全局黑名单仍对人工建卡只作提示，自动风控与其它业务规则保持不变。
- 开始时已有 `.worktrees/`、pytest 目录、其它任务交接文件、`output/` 截图/脚本和研究产物，全部保留，只暂存本任务明确列出的文件。
- 本任务生成的日志和验收截图保留于 `output/rh-manual-astro-*-20261007.*`，不提交截图或秘密。
- 若继续开放自动 RH 建卡、调整交易参数或接入 RH 账户权限，请另开相应模块任务；本记录替代旧 RH 公开市场交接中“所有人工入口仍只读”的现状描述，旧记录保留为历史。
