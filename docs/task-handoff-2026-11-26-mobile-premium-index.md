# P15 手机溢价指数交接

本次交付分支为 `codex/mobile-p1`，基线为 `1383482`。App 使用手机只读会话访问溢价指数。后端沿用网页的交易所查询服务、参数校验和标的别名规则。本次验收限 Windows 本地离线测试和 Git 提交。

## App 接入

- `GET /api/mobile/premium-index/exchanges` 返回与网页一致的交易所标识数组。
- `GET /api/mobile/premium-index/query` 接收 `exchange`、`symbol`、可选 `dex`、`hours`（默认 24，最小 1）和 `interval_minutes`（默认 1，可选 `1, 5, 15, 60, 240, 1440`）。响应的 `interval_minutes` 为服务实际采用的周期。Hyperliquid 请求应携带对应 DEX，可用 `symbol=io:ANTH` 这样的前缀写法。
- `GET /api/mobile/premium-index/current` 接收 `exchange`、`symbol`、可选 `dex`，直接返回 `MobilePremiumIndexCurrent`。查询响应的 `current` 使用同一结构，允许为 `null`。
- `MobilePremiumIndex*` 响应模型见 `backend/app/models/mobile.py`。`premium_pct` 统计来自服务的全量历史点，`premium_pct.current` 是最后一个有效历史点。即时溢价使用 `current.premium_pct`。历史 `points` 按时间排序并去重，最多 800 点，保留首尾时间和全局极值。同一时间只有一个展示槽，若全局最小和最大只能落在同一时间，曲线保留绝对值较大的点，统计仍包含两个极值。
- 同一会话最多一个在途 `query`。`query` 和 `current` 各按 3 秒启动间隔限制，名额与 P13 价差查询分开。重复在途和请求过频分别返回 429。查询超时 60 秒返回 504，连接断开时取消并允许同一会话立即重试。上游失败返回固定 502，参数错误返回 422。

## 后端入口与验证

- `backend/app/api/routes_mobile_premium.py` 负责接口投影、错误转换和固定警告文案。服务端日志记录经脱敏的上游诊断。`backend/app/api/mobile_query_utils.py` 为 P13 和 P15 共用会话限频、断连、超时、连接清理与降采样实现，限频状态保存在各自的进程内实例。
- 在 `backend` 目录使用任务指定的 Python 和 basetemp 运行 `python -m pytest tests -k mobile -q`，结果为 `192 passed, 4404 deselected, 1 warning in 52.04s`。其中包含 P13 回归。P15 用例通过假服务和进程内 ASGI 验证，测试禁止访问真实交易所。唯一告警来自 Starlette TestClient 的 httpx 弃用提示。
- 线上版本、真实交易所行为与 App 真机接入留待后续验收。下一任务可直接按响应模型进行 App 对接。
- 任务开始时已有四份未跟踪规格文档，仍在原路径。`docs/mobile-p12-feishu-push-2026-11-25.md`、`docs/mobile-p13-pair-spread-2026-11-25.md`、`docs/mobile-p14-eqn-news-2026-11-25.md`、`docs/mobile-p15-premium-index-2026-11-26.md`。
