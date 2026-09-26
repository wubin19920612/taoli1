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
- 隔离本地预览 `127.0.0.1:3011` 配合独立数据库和 dry-run 后端 `:8011`：桌面、390px 手机没有横向溢出；设置页将 `gc` 保存、读回后恢复 `both`，均为 HTTP 200。

## 线上交付与验收

- 功能提交 `a8dbe13b842706f14d40c0caebecf6aeda706839` 已推送。GitHub Actions run `36087705445` 的 backend、frontend 镜像构建均成功，两份 GHCR manifest 均可读取。
- 通过本机受控 `taoli1-prod` SSH 别名登录；Codex 使用 `ssh.exe -F C:\Users\wubin\.ssh\config taoli1-prod` 显式读取配置。别名内的服务器地址、私钥路径和已固定的主机公钥留在受控运行环境，不写入仓库。详情见 `docs/linux-deployment.md`。
- 生产机原提交 `6ec13caee912924563c9d4dc5cd88ae25295bfdc`，跟踪文件干净、双容器 healthy、后端健康接口正常、磁盘可用约 24.9 GiB。`deploy/linux-update.sh` 于 2026-09-25 03:07:53 UTC 创建 `backups/radar-before-a8dbe13b8427-20260925T030753Z.db`，大小 598,233,088 字节；SQLite `integrity_check=ok`，容器与主机 SHA-256 一致，复核哈希为 `7b2a0adfd9c13b646ba98a713554a223fe277a1c38497464d0f3e05db6e2d188`。
- 脚本 `git pull --ff-only` 后服务器仓库与两个运行镜像均为 `a8dbe13b842706f14d40c0caebecf6aeda706839`；两个容器 healthy，后端 `/api/health`、前端首页均返回 HTTP 200。脚本只拉取预构建镜像，没有在 2 GiB 生产机现场构建，也未删除数据库卷。备份保留策略保留新备份并清理一份较早的自动备份 `radar-before-c43d2f76a566-20260925T011420Z.db`。
- 线上 `/api/settings/astro-card` 返回 `card_variant: "both"`。只读预览 `CATUSDT` 机会返回普通 `okx -> binance` 与 GC `gc-okx -> gc-binance` 两条路线；没有调用建卡接口。Playwright 在 1440px 与 390px 实测设置页三档选项可见、无横向溢出或脚本错误；截图位于本机 Codex 可视化目录的 `astro-card-variant-production-{desktop,mobile}.png`。

## 已知限制与后续

- 本次线上验收没有创建真实 Astro 卡片，外部 Astro SDK 的实际写入与重启行为仍需在正常业务流中观察；预览与设置接口的只读结果不能证明真实下单或通知成功。
- 保留原有 `.worktrees/`、`output/`、其他模块交接文档、`script/` 和 pytest 临时目录等未跟踪产物；其他任务后续出现的已跟踪改动也未暂存、删除或回退。后续模块在新任务中继续。
