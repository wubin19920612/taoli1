# 交接：标的查询市场行情与诊断合并

日期：2026-09-23（Asia/Shanghai）

## 目标、范围与基线

- 仅合并标的查询页的市场信息展示；不改交易执行、账户连接、告警阈值、暂停卡状态或生产业务状态。
- 分支 `codex/frontend-localization-polish`；开始时 HEAD 为 `9385e21249665d556393c02bf564d7bf6fca0975`。实施期间另一任务先提交并推送 `e4fc83f417276ed4b7407d834d094796849118da`，本任务功能提交基于该新 HEAD。
- 功能提交：`643cb25c2653561e9e658f4aa1b88656a44e6d6e`，已推送至 `origin/codex/frontend-localization-polish`。交接文档由后续单独提交推送。

## 数据来源与关联设计

- `/api/instruments/{symbol}` 先返回精确行情：`markets`（或兼容 `exchanges`）、`timestamp`、`upstream_timestamp`、`data_source`、bid/ask、成交额、资金费率周期、倍率、估算字段及行情过期阈值。查询结果先显示，不等待诊断。
- 有市场时再并发获取 `/api/trade-status/{symbol}` 和恢复监控订阅。前者逐市场返回 `observed_at`、`market_data_updated_at`、`orderbook_updated_at`、盘口来源、公开交易限制、订单簿深度、费率、合约规格和证据；请求失败时清空诊断并展示错误，行情保留。请求重叠时以 requestId 拒绝旧响应，刷新行情时先清空旧诊断，避免跨次混用。
- 只以唯一的 `(exchange, market_type, raw_symbol, dex)` 关联；原始市场只做大小写统一，不按规范 ticker 或展示名称匹配。Hyperliquid 从原始市场的 DEX 前缀或主 DEX 推断，若显式 DEX 与前缀矛盾则拒绝关联；键重复、缺原始市场、不同 DEX 或不同原始市场均不强行拼接。未匹配行情保留空诊断；未匹配诊断以“仅诊断，未可靠关联行情”的卡片展示，不制造报价。现有接口足够，不改后端。
- 一市场一卡：概要显示身份、行情 Bid/Ask、双方成交量信息、24h 成交额、费率/周期、倍率、来源、绝对时间及估算标记；可展开区显示单独来源和时间的盘口 Bid/Ask、0.1%/1% 深度、公开限制与交易动作/恢复订阅、Maker/Taker、费用计入说明、规格及数据质量。诊断与行情不当作同步报价；诊断时间、盘口时间或诊断行情时间超过行情阈值（兼容默认 30 秒）会明确标记。现货充提和指数成分保持原模块位置，差价交易所搜索/补全和筛选保持可用。

## 测试与本地可视化

- `cd frontend; npm test -- InstrumentLookupPage.test.tsx --silent --reporter=dot`：24 passed；包含同名不同 DEX、原始市场不匹配、诊断过期、请求加载/失败但行情仍可见、诊断订阅和现货动作；差价搜索/补全原测试继续通过。
- `cd frontend; npx tsc -b` 通过；`cd frontend; npm run build` 通过；`git diff --check` 无错误。未修改后端，无需运行后端专项测试。
- 用本地 Vite 与 Playwright 模拟接口检查展开后的桌面 1440px、移动端 390px：各一张行情卡和对应诊断；页面宽度与 viewport 一致，无横向溢出。截图在受控可视化目录，不提交仓库。模拟数据验证布局不等于线上真实行情验证。

## 生产状态与阻塞

- GitHub 功能提交已推送；**服务器未备份、未拉取、未重建、未启动新版，也未完成线上健康/页面验收**。本次没有创建 `/data/radar.db` 备份，故无备份文件或校验值；生产运行版本未知，不能声称已部署。
- 已确认本机保存的服务器主机密钥匹配，但现有 SSH 身份认证被拒绝（`Permission denied (publickey,password)`）；未尝试绕过身份认证或更改服务器状态。需授权的服务器账号与可用 SSH 密钥/代理后才能继续。不将主机、密钥位置、密码等敏感运行信息写入仓库。
- 获得访问后先检查服务器分支和工作区，在线备份 `/data/radar.db` 到主机 `backups/`，校验 SQLite `PRAGMA quick_check`、大小和 SHA-256；确认备份后 `git pull --ff-only`，再 `sudo docker compose up -d --build`（禁止 `down -v`），核验前后端容器、`/api/health`，以及线上真实标的单卡、不同 DEX、诊断展开和请求失败降级；在本文补记备份路径/校验、线上提交号和验收结果后推送文档补充提交。

## 工作区与下一步

- 提交仅包含 `frontend/src/pages/InstrumentLookupPage.tsx`、`frontend/src/styles.css`、`frontend/tests/InstrumentLookupPage.test.tsx`。原有 `output/**` 与 `script/dexe_bybit_bitget_chain.py` 等未跟踪文件归属其他任务，保留不处理。
- 残余风险：前端过期判断以接口声明阈值和本机时钟为准；公开限制与手续费诊断不是账户级成交保证。待完成服务器访问、备份、部署、真实页面验收及最终文档补记后，本模块才算线上交付；其他模块另起任务。
