# 交接：标的查询自动刷新稳定性

日期：2026-09-25（Asia/Shanghai）

## 目标与范围

- 模块：标的查询页的精确行情市场。用户反馈页面自动刷新时内容跳动，影响读数。
- 验收目标：同一标的刷新行情、等待市场诊断期间，不收起已展开的诊断、不删掉整批诊断行、不插入加载提示导致表格收缩；诊断失败时保留上次结果并明确标注未更新。
- 基线：分支 `codex/frontend-localization-polish`，开始时 HEAD `bb09e8de2a271951bebf5f80abfe8d9afc79e278`。

## 已实现与代码入口

- `frontend/src/pages/InstrumentLookupPage.tsx` 的 `runLookup`：行情先返回，同一规范标的保留上一轮诊断至新诊断完整返回；换标的立即隔离旧诊断，失败保留上次诊断和独立错误状态。
- 同文件 `currentTradeStatus`：统一按标的约束市场、现货充提和指数成分，避免换标的期间短暂显示上一标的数据。诊断错误按标的记录。
- 同文件市场行：React key 按精确市场身份与同身份出现次数生成，减少刷新时节点重建，保留 `<details>` 的展开状态。后台定时器不发起重叠的刷新请求。
- 旧诊断刷新失败时显示“诊断未更新”，并保留原有过期提示。首次诊断加载时仅在市场行显示加载状态，避免整条横幅消失造成位移；失败时仍显示独立警告，行情继续可见。
- 没有修改后端接口、数据模型或交易执行逻辑。

## 业务规则

- 行情与诊断分别来自 `/api/instruments/{symbol}` 和 `/api/trade-status/{symbol}`，来源与时间不合并成同步报价。
- 关联仍使用唯一的精确市场身份 `(exchange, market_type, raw_symbol, dex)`；Hyperliquid DEX 与原始市场信息不得丢失。
- 保留旧诊断不代表它已更新：失败时显式标注，原有时间戳及过期判断继续显示。真实市场增删或价差排名变化仍可能改变页面高度或顺序。

## 验证

- `cd frontend; npm test -- InstrumentLookupPage.test.tsx --reporter=dot --silent`：26/26 通过。新增回归覆盖同一市场的刷新等待、成功替换、失败保留、展开状态和换标的隔离。
- `cd frontend; npm run build`：TypeScript 和 Vite 生产构建通过；`git diff --check` 通过。
- 本地生产预览 + Playwright 模拟两个精确市场，延迟第二次诊断响应：刷新前与等待时表格高度均为 `616.5px`、行数均为 2，诊断保持展开。截图位于 Codex 可视化目录 `instrument-refresh-check.png`，不提交仓库。此检查使用模拟接口，不等于线上验收。

## 线上状态与下一步

- 本文档创建时，本次改动尚未提交、推送或部署；最终状态应在交付后补录。上一任务交接文档记录过线上版本，但本任务没有据此推断当前线上状态。
- 仅暂存本任务的页面、测试和本文档。工作区原有 `.worktrees/`、`output/`、`script/` 及其他任务交接文档等未跟踪内容均保留，不归本任务提交。
- 推送当前分支后，等待该提交对应的前后端预构建镜像；在服务器按 `docs/linux-deployment.md` 与 `deploy/linux-update.sh` 备份数据库、校验并 `git pull --ff-only`，然后检查容器、`/api/health` 和精确行情页的自动刷新行为。不得在 2 GiB 生产机直接构建，也不得使用 `docker compose down -v`。
