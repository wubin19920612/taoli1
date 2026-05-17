# CEX Arbitrage Radar

自托管的 CEX 价差监控面板，用公开 API 拉取 Binance、OKX、Bybit、Gate、Bitget、HTX、Aster 的 USDT 现货/永续行情，计算 `SF`、`FF`、`SS` 机会并按规则发送飞书告警。

## 功能

- 现货/永续盘口快照采集，不需要交易所私钥。
- `SF`、`FF`、`SS` 开仓价差、平仓价差、费用后净估算、资金费率展示。
- 风险标签：低成交额、数据过期、异常大价差、价差宽、同名币风险、资金费率逆风、标记/指数偏离、缺失资金费率。
- 默认隐藏不可行动机会：低成交额、数据过期、异常大价差、价差宽、同名币风险、标记/指数偏离、缺失资金费率；实时面板可自行选择隐藏哪些风险标签，并用 K 为单位设置双边最低 24h 成交额。
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

这些标签都可以在实时面板的“隐藏风险”里选择隐藏/显示；阈值在“参数与告警 -> 风险参数”里修改并保存。成交额输入用 `K`，例如 `1000K = 1,000,000 USDT`。

| 标签 | 中文含义 | 触发逻辑 | 可调位置 |
| --- | --- | --- | --- |
| `LOW_VOLUME` | 低成交额 | 买入侧和卖出侧取较小的 24h 成交额，低于低成交额阈值 | 低成交额阈值，单位 K |
| `STALE_DATA` | 数据过期 | 当前时间减去行情最后更新时间，超过数据过期秒数 | 数据过期秒数 |
| `HUGE_SPREAD_VERIFY` | 异常大价差 | 开仓价差高于异常大价差阈值，需要人工复核盘口、同名币、停充提等问题 | 异常大价差阈值 |
| `WIDE_SPREAD` | 开平价差宽 | `abs(平仓价差 - 开仓价差)` 高于开平价差宽度阈值，说明进出场估算差异大 | 开平价差宽度 |
| `SAME_TICKER_RISK` | 同名币风险 | 标的在同名风险标的列表中，可能不是同一个资产 | 同名风险标的 |
| `FUNDING_AGAINST` | 资金费率逆风 | `卖出侧资金费率 - 买入侧资金费率` 低于负的逆风阈值 | 资金费率逆风阈值 |
| `MARK_INDEX_DEVIATION` | 标记/指数偏离 | 合约标记价与指数价的偏离绝对值高于阈值 | 标记/指数偏离阈值 |
| `MISSING_FUNDING` | 缺资金费率 | 至少一侧永续合约没有资金费率数据 | 暂无阈值，可在隐藏风险中显示/隐藏 |

保存风险参数后，后端采集器会在下一轮行情采集时重新加载阈值并重新打标签；实时面板筛选里的“成交额 K”和“隐藏风险”会立即作用于当前列表请求。

## API

- `GET /api/health`
- `GET /api/opportunities?type=FF&symbol=BTC&exchange=okx&min_open_spread_pct=0.5&include_risky=false&hidden_risk_labels=LOW_VOLUME,HUGE_SPREAD_VERIFY&min_volume_24h_k=1000`
- `GET /api/markets`
- `GET /api/settings/risk`
- `PUT /api/settings/risk`
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
