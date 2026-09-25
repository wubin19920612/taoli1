# 交接：Astro 建卡路线选择

日期：2026-09-25（Asia/Shanghai）

## 目标与范围

- 模块：Astro 建卡。用户要求在原来默认同时创建 GC、非 GC 两种卡片的基础上，能选择“两种都建”“仅非 GC”“仅 GC”，并作用于所有可能生成双卡的 Astro 建卡入口。
- 分支：`codex/frontend-localization-polish`；开始基线：`537a9237c1553306d766d631a11d692f53fff6d4`。功能提交可由 `git log -1 --format=%H -- docs/task-handoff-2026-09-25-astro-card-variants.md` 查到。
- 本任务不修改 Astro 已有卡片，也不以真实交易卡片作为验收数据。

## 已完成与入口

- `backend/app/models/settings.py`、`backend/app/models/astro.py`、`backend/app/models/astro_preadd.py`：增加 `both` / `non_gc` / `gc`。旧设置及未传该字段的请求默认 `both`，保持原有双卡行为。
- `backend/app/services/market_labels.py`、`backend/app/services/astro_alerts.py`：按所选路线筛选写入；告警自动建卡、实盘实验、自动预建、立即预建、机会列表和标的查询人工建卡共用此逻辑。查重仍把未选中的同族路线识别为同一组，已存在非 GC 卡片时仍能补建 GC 卡片，反向亦然。
- `backend/app/services/astro_preadd.py`、`backend/app/api/routes_astro.py`：立即预建可按本次选择覆盖全局默认；人工预览返回可用精确路线和默认选择；人工建卡可选择本次生效或保存为全局默认。
- `frontend/src/components/AstroCardVariantSelector.tsx`、`frontend/src/pages/SettingsPage.tsx`、`frontend/src/pages/DashboardPage.tsx`、`frontend/src/pages/InstrumentLookupPage.tsx`、`frontend/src/pages/FundingArbitragePage.tsx`：全局设置、两个手动建卡确认框、立即预建确认框提供三档选择；手动确认框显示所选路线。前端 API 类型及请求见 `frontend/src/api/types.ts`、`frontend/src/api/client.ts`。

## 业务规则

- 全局默认值供自动告警、实盘实验、自动预建使用；手动建卡和立即预建允许本次覆盖。手动建卡勾选“保存为全局建卡默认值”时也保存路线选择。
- Lighter 仅支持 GC 路线；选择“仅非 GC”时跳过且不写入 Astro。Bitget 与 GC 交易所组合仍保留 Bitget 原始腿；Hyperliquid 的原始市场与 DEX 区分不变。
- 新选择只影响之后的创建请求，不删除、暂停或重建已存在卡片。`both` 仍只补建缺失的路线。

## 验证

- 后端全量测试：755 项通过；另 11 项在 pytest 建立本机默认临时目录时遇到 Windows `PermissionError`，未进入测试逻辑。指定新的可写 `--basetemp` 后，对受影响的三个测试文件重跑，18 项全部通过。
- 前端相关四个测试文件：59 项通过；`npm run build`（含 TypeScript 检查）通过；`git diff --check` 通过。
- 本机 3000 端口的前端开发服务已加载新页面。8000 端口现有后端服务仍是旧进程，不能将其响应当作新后端验收。

## 线上状态与下一步

- 截至编写交接时尚未部署或在线创建卡片。当前环境对服务器的 SSH 主机密钥校验通过，但 `ubuntu` 身份认证返回 `Permission denied (publickey,password)`；生产运行版本、数据库备份及页面行为尚未在本任务中核验。
- 代码推送后需确认该功能提交的 backend/frontend 两个预构建镜像成功。获得有效服务器 SSH 凭据后，按 `docs/linux-deployment.md` 用 `deploy/linux-update.sh` 先备份并校验 `/data/radar.db`，再 `git pull --ff-only`、拉取对应 SHA 的镜像并启动 Compose；生产机不现场构建，不执行 `docker compose down -v`。
- 部署后检查双容器、`/api/health`、全局 Astro 设置读回、人工预览的 GC/非 GC 路线、桌面与窄屏选择器。不要为验收创建真实交易卡片。
- 保留原有 `.worktrees/`、`output/`、其他模块交接文档、`script/` 和 pytest 临时目录等未跟踪产物；均不属于本模块提交。
