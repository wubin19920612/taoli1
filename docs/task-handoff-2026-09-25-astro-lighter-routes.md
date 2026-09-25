# 交接：Astro 普通 Lighter 建卡路线

日期：2026-09-25（Asia/Shanghai）

## 目标与范围

- 模块：Astro 建卡。修正工程内“Lighter 只能使用 GC”限制，让普通 `lighter` 和 `gc-lighter` 受现有 `both` / `non_gc` / `gc` 选择控制。
- 分支：`codex/frontend-localization-polish`；起始基线 `8863abc8a28ed3dc2bc8db39615fe8954a0f1a7c`；功能提交 `4e814fb395e1aa76d429dac7f5562b18be836b6f` 已推送。本文档的补充提交可通过 `git log -1 -- docs/task-handoff-2026-09-25-astro-lighter-routes.md` 查询。
- 用户截图中的 Astro `rh-lighter -> lighter` 卡片仅作为普通 `lighter` ID 可用的证据。本次没有开放本工程 `rh-lighter` 的建卡执行，也没有为验收创建真实 Astro 卡片。

## 已完成与代码入口

- `backend/app/services/market_labels.py`：`astro_exchange_id()` 保留普通 `lighter`；`astro_exchange_route_variants()` 对已知 GC 交易所或 Bitget 对手生成普通、GC 路线，并按所选类型筛选。显式 `gc-lighter` 只产生 GC 路线。
- `backend/app/services/astro_planner.py`：预览以普通路线为基础，未知 Lighter 对手仍拦截。`backend/app/api/routes_astro.py` 返回可选精确路线。
- `backend/app/services/astro_alerts.py`：告警自动建卡、实盘实验、人工建卡与预建共用路线选择；已有普通或 GC 同族卡片时只补缺失路线。`rh-lighter` 的只读保护保持在建卡服务入口。
- `backend/app/services/instrument_spreads.py`：更新未知 Lighter 配对的提示，仅将这一处 Astro 文案纳入功能提交；该文件其他任务的未提交改动留在工作区。
- `frontend/src/pages/SettingsPage.tsx`：修正“默认建卡路线”的 Lighter 提示。控制入口为“设置 -> Astro 卡片默认参数 -> 默认建卡路线”；机会列表、标的查询与立即预建的确认界面也可单次选择。

## 业务规则与限制

- `both` 建普通和 GC 两张，`non_gc` 只建普通，`gc` 只建 GC；不会修改、删除或重建已存在的卡片。Bitget 腿保持 `bitget` / `bitgetr`，Hyperliquid 的原始市场和 DEX 区分不变。
- 当前仍只允许 Lighter 与代码已知支持 GC 的交易所或 Bitget 配对。未知对手交易所未被截图验证，继续拦截。`rh-lighter` 在本工程仍是只读市场，不能由预览结果推断可以执行建卡或交易。
- 线上数据库目前保存 `card_variant: "gc"`，所以自动建卡仍只会选择 GC；需要普通 Lighter 卡片时，由用户在上述设置页改为“仅非 GC”或“两种都建”，或在人工建卡确认界面做本次选择。本次部署没有替用户改写全局偏好。

## 验证

- 后端针对性测试 `test_market_labels.py`、`test_astro_planner.py`、`test_astro_alerts.py`、`test_astro_instrument_routes.py`、`test_astro_preadd.py`、`test_instrument_spreads.py`：134 项通过；补充 `gc-lighter` 未知对手预览边界后，`test_astro_planner.py` 25 项通过。
- 前端 `SettingsPage.test.tsx`、`DashboardPage.test.tsx`：31 项通过；`npm run build`（含 TypeScript 检查）通过；`git diff --cached --check` 通过。`ruff check` 报告旧代码中 11 处导入顺序或样式规则问题，均不在本次改动行。
- GitHub Actions `36091663367` 的 backend、frontend 镜像任务均成功；服务器端两个对应 GHCR 镜像 manifest 均可读取。

## 线上状态与服务器访问

- 生产更新通过 `deploy/linux-update.sh` 完成：脚本先创建 `backups/radar-before-4e814fb395e1-20260925T034811Z.db`，SQLite `integrity_check=ok`，主机与容器备份 SHA-256 一致，为 `cb151dd22306da5573e2e6378369bae6c6e37fc68889383cb7719700900b28b7`。备份保留策略留存新备份，按已有规则清理一份较早的自动备份。
- 服务器仓库与运行中的前后端镜像均为 `4e814fb395e1aa76d429dac7f5562b18be836b6f`；两容器 healthy，后端 `/api/health` 为 `ok`，前端首页 HTTP 200，线上设置页 JS 资源包含新提示。
- 线上只读 `BTCUSDT lighter -> binance` Astro 预览返回 `can_submit=true`、普通 `lighter -> binance` 和 GC `gc-lighter -> gc-binance` 两条路线；`rh-lighter -> lighter` 预览仍 `can_submit=false`，包含只读阻止原因。没有调用建卡接口，实际 Astro 写入行为尚未经本次线上验收。
- 服务器登录方法固定在 `docs/linux-deployment.md` 的 “Windows Operator Access”：使用 `ssh.exe -F C:\Users\wubin\.ssh\config taoli1-prod "git -C ~/wubin/taoli1 rev-parse HEAD"` 做只读校验。地址、密钥和主机密钥记录只在本机受控配置中；Codex 沙箱读取专用密钥时需受控权限，不复制进仓库。

## 工作区与下一步

- 本次 Astro 功能提交没有包含其他任务的 instrument 模型、标的查询页面及相关测试改动。这些改动后来由其任务提交为 `632316d`；生产服务器本次仅部署到 Astro 功能提交 `4e814fb`。`.worktrees/`、`output/`、`script/`、pytest 临时目录等未跟踪产物保留，未删除或回退。
- 本对话较早提出的“一分钟价差信号”和“新币速递”模块裁剪属于其他功能模块，本次没有修改；应在独立任务中处理。
- 后续如需让本工程为 `rh-lighter` 建卡，应另开模块任务，先验证独立市场身份、真实执行能力与风险边界。普通 Lighter 本次已具备可选路线，可在正常业务流观察实际 Astro 创建结果。
