# 交接：标的查询市场行情与诊断合并

日期：2026-09-23 开始，2026-09-24 完成线上交付（Asia/Shanghai）

## 目标、范围与基线

- 仅合并标的查询页的市场信息展示；不改交易执行、账户连接、告警阈值、暂停卡状态或生产业务状态。
- 分支 `codex/frontend-localization-polish`；开始时 HEAD 为 `9385e21249665d556393c02bf564d7bf6fca0975`。实施期间另一任务先提交并推送 `e4fc83f417276ed4b7407d834d094796849118da`，本任务功能提交基于该新 HEAD。
- 功能提交：`643cb25c2653561e9e658f4aa1b88656a44e6d6e`；初版交接文档：`2aa4477a5e9552b8d3200dd1aa84c7be8669523b`；低内存发布集成：`6a8c43dd4f0dde81a5f94d99fae3a93341f01e6b`；来源列排版修正及最终部署版本：`f3472516552b1467ed89876980a7686449523954`。均已推送至 `origin/codex/frontend-localization-polish`。

## 数据来源与关联设计

- `/api/instruments/{symbol}` 先返回精确行情：`markets`（或兼容 `exchanges`）、`timestamp`、`upstream_timestamp`、`data_source`、bid/ask、成交额、资金费率周期、倍率、估算字段及行情过期阈值。查询结果先显示，不等待诊断。
- 有市场时再并发获取 `/api/trade-status/{symbol}` 和恢复监控订阅。前者逐市场返回 `observed_at`、`market_data_updated_at`、`orderbook_updated_at`、盘口来源、公开交易限制、订单簿深度、费率、合约规格和证据；请求失败时清空诊断并展示错误，行情保留。请求重叠时以 requestId 拒绝旧响应，刷新行情时先清空旧诊断，避免跨次混用。
- 只以唯一的 `(exchange, market_type, raw_symbol, dex)` 关联；原始市场只做大小写统一，不按规范 ticker 或展示名称匹配。Hyperliquid 从原始市场的 DEX 前缀或主 DEX 推断，若显式 DEX 与前缀矛盾则拒绝关联；键重复、缺原始市场、不同 DEX 或不同原始市场均不强行拼接。未匹配行情保留空诊断；未匹配诊断以“仅诊断，未可靠关联行情”的卡片展示，不制造报价。现有接口足够，不改后端。
- 一市场一卡：概要显示身份、行情 Bid/Ask、双方成交量信息、24h 成交额、费率/周期、倍率、来源、绝对时间及估算标记；可展开区显示单独来源和时间的盘口 Bid/Ask、0.1%/1% 深度、公开限制与交易动作/恢复订阅、Maker/Taker、费用计入说明、规格及数据质量。诊断与行情不当作同步报价；诊断时间、盘口时间或诊断行情时间超过行情阈值（兼容默认 30 秒）会明确标记。现货充提和指数成分保持原模块位置，差价交易所搜索/补全和筛选保持可用。

## 测试与本地可视化

- `cd frontend; npm test -- InstrumentLookupPage.test.tsx --silent --reporter=dot`：24 passed；包含同名不同 DEX、原始市场不匹配、诊断过期、请求加载/失败但行情仍可见、诊断订阅和现货动作；差价搜索/补全原测试继续通过。
- `cd frontend; npx tsc -b` 通过；最终 `npm run build` 通过；`git diff --check` 无错误。市场合并未改后端；发布集成纳入已在生产运行的低内存分支，额外运行 `pytest tests/test_data_filters.py tests/test_announcement_research.py -q`，11 passed。
- 本地 Vite + Playwright 在 1440px、390px 用模拟接口及经 SSH 隧道取得的真实线上接口两次检查展开卡片；来源长文本排版修正后标签不再逐字竖排。截图保存在受控可视化目录，不提交仓库。

## 生产部署与验收（2026-09-24）

- 项目运行环境中的专用 SSH 凭据已定位，严格校验主机密钥后连接成功。生产原先运行 `f4fa38e` 低内存版本；因 2 GiB 主机曾在本机 Docker 前端构建时触发全局 OOM，本次将已上线的低内存发布提交合入功能分支，只在 GitHub Actions 构建 amd64 镜像（最终 run `35953024460` 成功），不在生产主机运行 `docker compose build`。这是对旧交付清单“服务器重建”的安全替代，仍以完整提交号固定镜像。
- 首次部署 `6a8c43d` 前，发布脚本用 SQLite backup API 备份 `/data/radar.db` 至 `backups/radar-before-6a8c43dd4f0d-20260924T033521Z.db`，大小 604,995,584 字节，`integrity_check=ok`，容器内外 SHA-256 一致：`e68ba8f769f110fcc7b44c08b4b233ccbc8e1569a0da27a046e9ff78617ecbfb`。
- 最终部署 `f347251` 前再次备份至 `backups/radar-before-f3472516552b-20260924T035220Z.db`，大小 604,549,120 字节，`integrity_check=ok`，容器内外 SHA-256 一致，主机复核值 `0c39bd923afbc22b5d2870dec27164763921898242c290bfd73cdf54ee100d73`。两份备份、原 `.env` 备份、`CACHED` 与数据库卷均保留。
- 两次均先备份再 `git pull --ff-only origin codex/frontend-localization-polish`，之后 `docker compose pull` 和 `up -d --no-build --wait`，未使用 `down -v`。服务器代码与前后端镜像最终均为 `f3472516552b1467ed89876980a7686449523954`；前后端容器 healthy，后端和前端代理 `/api/health` 均返回 200，标的查询页返回 200。最终核对主机 `MemAvailable` 约 1,016 MiB、根盘剩余 6.4 GiB。
- 真实 BTCUSDT：行情接口与诊断接口各返回 15 个市场，诊断接口有 1 项降级并在页面告警；经 SSH 隧道的 Playwright 检查 1440px 与 390px 页面各显示 15 张身份唯一卡片、15 个可展开诊断，无旧“交易所市场”独立列表、无浏览器脚本错误或横向溢出。展开后能看到独立来源/时间、盘口价、深度、公开限制、费率和规格。两种宽度下现货筛选均剩 6 张卡；差价交易所搜索 Binance 均从 36 / 105 组缩至 8 / 105 组。不同 DEX 与失败降级由专项测试覆盖，未人为使生产诊断接口故障。

## 工作区与下一步

- 市场合并的手工修改仅涉及 `frontend/src/pages/InstrumentLookupPage.tsx`、`frontend/src/styles.css`、`frontend/tests/InstrumentLookupPage.test.tsx`；额外合入低内存发布配置以安全上线。原有 `.worktrees/`、`output/**`、`script/` 及其他任务的未跟踪文件均保留。本次文档补充提交不改变镜像，生产仍停留在上述 `f347251` 功能版本。
- 残余风险：前端过期判断依赖接口阈值与浏览器时钟；公开限制和手续费诊断不是账户级成交保证。移动端原有关注浮窗会盖住卡片中段，可用自带隐藏按钮关闭，本任务未改浮窗。2 GiB 服务器尚无长期稳定性观察且磁盘余量 6.4 GiB，后续仍应监控资源并沿用离机构建流程。其他模块在新任务继续。
