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

## 线上交付与验收

- 功能提交 `c43d2f76a5663d02363d67ccc4b9d33798ebbb8f` 已推送到 `origin/codex/frontend-localization-polish`；GitHub Actions run `36080490592` 的前端和后端两项构建均成功，生产机能读取此 SHA 的双镜像 manifest。
- 发布前确认生产机运行镜像 `88d05647bce57a668cf0596a9d142f1117a4ef6a` 是目标提交祖先；服务器仓库无已跟踪改动、后端健康、磁盘空余约 25.7 GiB。使用 `deploy/linux-update.sh`，未在 2 GiB 主机上构建，也未使用 `docker compose down -v`。
- 脚本先备份 `/data/radar.db` 到 `backups/radar-before-c43d2f76a566-20260925T011420Z.db`，大小 606,560,256 字节，`integrity_check=ok`，容器与主机 SHA-256 一致：`6e87d9758d58c65c4a5ddb2b93cf4ddd66df74b43c63398b87e7ba233194c64e`。随后 `git pull --ff-only` 到目标提交，拉取双镜像并 `docker compose up -d --no-build --wait`；两个容器均 healthy。
- 脚本的保留策略留下本次、`88d0564` 与 `6926681` 三份近期部署备份，并清理旧的 `radar-before-f3472516552b-20260924T035220Z.db`。数据库卷和 `.env` 未删除或覆盖。
- 发布后服务器代码与双镜像均为 `c43d2f7`；后端和前端代理 `/api/health` 均 HTTP 200，STEEMUSDT 行情和市场诊断接口均 HTTP 200。经严格主机密钥校验的本机 SSH 隧道检查真实生产页面：STEEMUSDT 精确市场 7 行，手动刷新时延迟诊断响应，刷新前、等待中、响应后表格高度均为 1591px、滚动位置均为 450px，首行诊断一直展开，无浏览器脚本错误。截图在 Codex 可视化目录 `instrument-refresh-production.png`。

## 工作区与后续

- 本任务只提交页面、专项测试及本文档。交付时工作区另有后端、新币监控和分钟信号等模块的并行已跟踪修改，以及原有 `.worktrees/`、`output/`、`script/` 等未跟踪内容；均未暂存、删除或回退。
- 真实市场增删仍会改变行数和页面高度；跨市场差价区按实时价差排名，也仍可能重排。两者属于数据本身或相邻模块行为；本任务解决的是同一市场的诊断清空造成的周期性跳动。
- 后续若处理价差区排序或其他模块，按 `AGENTS.md` 新建任务，沿用本文档及既有市场身份规则。
