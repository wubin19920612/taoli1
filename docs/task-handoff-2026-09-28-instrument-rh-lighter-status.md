# 交接：RH-Lighter OPENAI 标的查询公开状态

日期：2026-09-28（Asia/Shanghai）

## 目标与范围

修复“标的查询”中 `rh-lighter:future:OPENAI` 的“该交易所尚无公开状态诊断器”错误。范围仅为交易可用性诊断及其返回的覆盖范围、指数来源标注；没有修改行情采集、Astro、账户连接或交易执行。

## 分支与基线

- 分支：`codex/frontend-localization-polish`。
- 本任务开始基线：`8fa97d9a83092c21d5624033aea7da0e25e5ae23`。
- 功能提交：`a50f0137aa489c64bcc47c9feb39e5d53d331263`，已推送 GitHub。
- 生产部署前 SHA：`fea864b9347d7df1bf4b520d9366cb547a555c03`；部署后运行功能提交 `a50f0137aa489c64bcc47c9feb39e5d53d331263`。

## 根因与修复

- `TradeAvailabilityService._public_market_info` 原来按交易所 ID 动态查 `_info_<exchange>`。`rh-lighter` 没有对应处理函数，所以 RH 行情快照和盘口都存在时，公开状态仍报 `UNKNOWN`。
- `backend/app/services/trade_availability.py` 现将 RH 显式分发到 Lighter 公开状态规则，按实例 profile 分别请求普通 Lighter 与 RH 的 `orderBookDetails`，缓存键按 exchange 隔离。无效响应报请求错误，避免误写 `NOT_FOUND`。
- 同模块的覆盖范围现在包含 RH；RH 指数价格标注为 RH 自身 `orderBookDetails` 来源，未声称接口提供成分或权重。
- 回归测试在 `backend/tests/test_trade_availability.py` 中让普通 Lighter 与 RH 使用同名原始市场 `OPENAI`、不同公开配置，验证状态、来源、盘口和缓存实例边界。

## 关键业务规则

- RH-Lighter 是独立实例，REST 为 `https://api.rh.lighter.xyz/api/v1`；普通 Lighter 为 `https://mainnet.zklighter.elliot.ai/api/v1`。不得用普通实例的状态、价格或市场 ID 补 RH。
- Radar 聚合符号为 `OPENAIUSDT`，RH 官方原始永续市场为 `OPENAI`；RH 以 USDG 为抵押/现货报价资产。查询与诊断保留 `(exchange, market_type, raw_symbol)` 身份。
- 公开状态 `active` 与盘口存在只说明公共市场可用性；账户余额、地区、权限、风控和实际下单结果未核验。

## 测试与公开证据

- 回归测试先在旧代码复现 `rh-lighter:future:OPENAI` 错误，再于修复后通过。
- `python -m pytest -q tests/test_trade_availability.py -p no:cacheprovider`：9 passed。
- `python -m pytest -q tests/test_instrument_lookup.py tests/test_lighter_adapter.py -p no:cacheprovider`：22 passed。
- 定向 Ruff 与 `git diff --check` 通过；GitHub Actions 生产镜像工作流 run `36372356228` 的 backend/frontend 任务均成功。前端源代码未改动。
- 2026-09-28 只读查询 RH 官方 `GET /orderBookDetails`：`OPENAI`，`market_id=42`，`status=active`；`GET /orderBookOrders?market_id=42&limit=20` 返回 bid/ask。这是查询时点的公开证据，不代表持续流动性。

## 线上状态与备份

- 部署脚本先备份 `/data/radar.db` 至 `~/wubin/taoli1/backups/radar-before-a50f0137aa48-20260928T031027Z.db`，`integrity_check=ok`，容器/宿主机 SHA-256 一致：`bcb3a3a16deda5a0ba77ea45b99e51d7c10414a401f5d653e8ca32f9a3a893c0`。
- 同时备份 `/data/radar-squeeze-route.db` 至 `~/wubin/taoli1/backups/squeeze-route-before-a50f0137aa48-20260928T031027Z.db`，`integrity_check=ok`，SHA-256 一致：`be93e169f287fd31de581ca51ecf4e0998993f6abadded1036e6575d547829aa`。
- 服务器 `git pull --ff-only` 后 HEAD 为功能提交；使用对应预构建镜像执行 `docker compose up -d --no-build --wait`。backend/frontend 容器均 healthy，`/api/health` 为 `status=ok`，前端首页 HTTP 200。
- 修复前 `/api/trade-status/OPENAIUSDT` 有 `rh-lighter:future:OPENAI` 公开状态诊断错误，状态为 `UNKNOWN`，但已有 RH 盘口。修复后直连后端及经前端代理查询均为 `errors={}`；RH 原始市场 `OPENAI` 返回 `public_status_code=active`、来源 `Robinhood Lighter orderBookDetails`、实时 bid/ask，公开开多/开空状态为 `available`。
- 部署脚本按既有备份保留策略清理了各一份较旧的 radar/squeeze-route 自动备份；本次两份新备份均保留。

## 已知边界、未跟踪文件与下一步

- 此次没有接入账户私有状态，也没有发送探测订单；页面上的 `available` 不保证特定账户能成交。RH 现货逐链充提仍无已验证的匿名公开状态源。
- 工作区开始时已存在 `.worktrees/`、`backend/.pytest_*`、其他模块的 `docs/task-handoff-*`、`output/` 与 `script/dexe_bybit_bitget_chain.py` 等未跟踪产物；本任务未暂存、删除或覆盖这些文件。
- 若再出现 RH 市场诊断异常，先按 exchange、market_type、raw_symbol 对照 `GET /api/trade-status/OPENAIUSDT` 的 `errors`、官方 RH `orderBookDetails`、RH 采集器状态及订单簿，再判断是元数据、盘口还是账户问题。后续其他模块工作另开任务。
