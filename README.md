# CEX Arbitrage Radar

自托管的 CEX 价差监控面板，用公开 API 拉取 Binance、OKX、Bybit、Gate、Bitget、HTX、Aster、Hyperliquid 的 USDT 现货/永续行情，计算 `SF`、`FF`、`SS` 机会并按规则发送飞书告警。

## 功能

- 现货/永续盘口快照采集，不需要交易所私钥。
- Hyperliquid 使用公开 `info` 接口接入；其现货/永续价格按 midpoint 映射为 bid/ask，USDC 现货对在面板内归一到 USDT 符号，并通过 `perpDexs` 纳入股票、指数、商品等 builder-deployed 永续标的。
- `SF`、`FF`、`SS` 开仓价差、平仓价差、费用后净估算、资金费率展示。
- 风险标签：低成交额、数据过期、异常大价差、价差宽、同名币风险、资金费率逆风、标记/指数偏离、缺失资金费率。
- 默认隐藏不可行动机会：低成交额、数据过期、异常大价差、价差宽、同名币风险、缺失资金费率；标记/指数偏离默认显示，实时面板可自行选择隐藏哪些风险标签，并用 K 为单位设置双边最低 24h 成交额。
- 小硬盘友好的轻量历史：默认每 120 秒只记录价差达到 0.5%、成交额达到 100K 的 Top 100 机会，保留 3 天并定期清理 SQLite 空间。
- Web 面板：实时机会、筛选、风险参数、告警规则、告警历史。
- 飞书自定义机器人告警，支持 webhook secret 签名。
- Docker Compose 一键部署。

## 本地运行

后端：

```bash
cd backend
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev
```

打开 `http://127.0.0.1:3000`。前端开发服务器会把 `/api` 代理到 `http://127.0.0.1:8000`。

## Linux 服务器部署

```bash
cp .env.example .env
docker compose up -d --build
```

默认端口：

- 前端面板：`http://服务器IP:3000`
- 后端 API：`http://服务器IP:8000/api/health`

建议在服务器上用 Nginx/Caddy 配 HTTPS，并限制后台访问来源。设置 `DASHBOARD_PASSWORD` 后，前端“参数与告警”页输入同一个密码即可保存风险参数和告警规则。

如果你想在本地测试环境打开“重启前端 / 重启后端”按钮，使用：
```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```
然后把 `DASHBOARD_PASSWORD` 设成你自己的面板密码。默认的 `docker-compose.yml` 不会挂载 Docker socket，也不会开启服务控制；只有叠加 `docker-compose.local.yml` 时才会打开 `frontend/backend` 的重启能力。
如果你用 `docker compose -p <name>` 指定了别的项目名，记得把 `COMPOSE_PROJECT_NAME` 设成同一个值。

## 飞书告警

在 `.env` 中填写：

```env
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/...
FEISHU_SECRET=...
```

然后重启：

```bash
docker compose up -d --build
```

## 风险标签和可调参数

这些标签都可以在实时面板的“隐藏风险”里选择隐藏/显示；阈值在“参数与告警 -> 风险参数”里修改并保存。成交额输入用 `K`，例如 `1000K = 1,000,000 USDT`。同一页还可以设置黑名单标的和忽略交易所，保存后会同步影响实时列表、健康统计和告警。

| 标签 | 中文含义 | 触发逻辑 | 可调位置 |
| --- | --- | --- | --- |
| `LOW_VOLUME` | 低成交额 | 只对已知的 24h 成交额判断；任一已知侧低于低成交额阈值都会触发，两侧都缺失成交额时不触发 | 低成交额阈值，单位 K |
| `STALE_DATA` | 数据过期 | 当前时间减去行情最后更新时间，超过数据过期秒数 | 数据过期秒数 |
| `HUGE_SPREAD_VERIFY` | 异常大价差 | 开仓价差高于异常大价差阈值，需要人工复核盘口、同名币、停充提等问题 | 异常大价差阈值 |
| `WIDE_SPREAD` | 开平价差宽 | `abs(平仓价差 - 开仓价差)` 高于开平价差宽度阈值，说明进出场估算差异大 | 开平价差宽度 |
| `SAME_TICKER_RISK` | 同名币风险 | 标的在同名风险标的列表中，可能不是同一个资产 | 同名风险标的 |
| `FUNDING_AGAINST` | 资金费率逆风 | `卖出侧资金费率 - 买入侧资金费率` 低于负的逆风阈值 | 资金费率逆风阈值 |
| `MARK_INDEX_DEVIATION` | 标记/指数偏离 | 合约标记价与指数价的偏离绝对值高于阈值 | 标记/指数偏离阈值 |
| `MISSING_FUNDING` | 缺资金费率 | 至少一侧永续合约没有资金费率数据 | 暂无阈值，可在隐藏风险中显示/隐藏 |

保存风险参数后，后端采集器会在下一轮行情采集时重新加载阈值并重新打标签；实时面板筛选里的“成交额 K”和“隐藏风险”会立即作用于当前列表请求。打开实时面板时，“成交额 K”默认跟随已保存的低成交额阈值；新增告警规则里的“最低成交额 (K)”也默认跟随该阈值，前端用 K 展示，接口和数据库仍按 USDT 保存。
黑名单标的会被从机会列表、健康统计、告警和采集结果中排除；忽略交易所会跳过对应交易所的采集请求，并从健康统计和告警评估中剔除。

## 轻量历史

为避免 Linux 小硬盘被历史数据打满，历史记录默认是 compact 模式，不保存全量行情 JSON，只保存回溯判断需要的摘要字段：标的、买卖交易所、开平价差、扣费后价差、资金费率差、双边成交额和风险标签位图。价差和资金费率按整数缩放入库，风险标签用 bitmask，减少每行体积。

默认参数：

```env
HISTORY_ENABLED=true
HISTORY_SAMPLE_SECONDS=120
HISTORY_RETENTION_DAYS=3
HISTORY_KEEP_TOP_N=100
HISTORY_MIN_OPEN_SPREAD_PCT=0.5
HISTORY_MIN_VOLUME_24H_K=100
HISTORY_VACUUM_INTERVAL_SECONDS=86400
```

这表示每 120 秒最多记录 100 条候选机会，只保留最近 3 天。保留期外数据会删除，并按 `HISTORY_VACUUM_INTERVAL_SECONDS` 定期执行 SQLite `VACUUM` 回收文件空间，避免数据库文件长期只增不减。

## API

- `GET /api/health`
- `GET /api/opportunities?type=FF&symbol=BTC&exchange=okx&min_open_spread_pct=0.5&include_risky=false&hidden_risk_labels=LOW_VOLUME,HUGE_SPREAD_VERIFY&min_volume_24h_k=1000`
- `GET /api/markets`
- `GET /api/history/opportunities?symbol=BTCUSDT&hours=24&limit=1000`
- `GET /api/settings/risk`
- `PUT /api/settings/risk`
- `GET /api/admin/service-control`
- `POST /api/admin/service-control/{service}/restart`
- `GET /api/alerts/rules`
- `POST /api/alerts/rules`
- `DELETE /api/alerts/rules/{rule_id}`
- `GET /api/alerts/events`
- `GET /api/stream`

## 安全边界

这个项目只做监控和提醒，不接入私有交易接口，不做自动下单，不提供绕过监管或开户入金规避方案。告警结果只是信号，实际交易前需要人工确认盘口深度、提现/转账限制、合约规则、资金费率结算时间和交易所风控。

## 验证

```bash
python -m pytest backend/tests -q
cd frontend
npm test
npm run build
```
