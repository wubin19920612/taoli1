# CEX Arbitrage Radar

自托管的 CEX 价差监控面板，用公开 API 拉取 Binance、OKX、Bybit、Gate、Bitget、HTX、Aster 的 USDT 现货/永续行情，计算 `SF`、`FF`、`SS` 机会并按规则发送飞书告警。

## 功能

- 现货/永续盘口快照采集，不需要交易所私钥。
- `SF`、`FF`、`SS` 开仓价差、平仓价差、费用后净估算、资金费率展示。
- 风险标签：低成交额、数据过期、异常大价差、价差宽、同名币风险、资金费率逆风、标记/指数偏离、缺失资金费率。
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

## API

- `GET /api/health`
- `GET /api/opportunities?type=FF&symbol=BTC&exchange=okx&min_open_spread_pct=0.5`
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
