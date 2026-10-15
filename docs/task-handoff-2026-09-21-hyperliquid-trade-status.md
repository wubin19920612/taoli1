# 交接：Hyperliquid 交易可用性诊断与恢复监控

日期：2026-09-21

## 目标与范围

本任务只处理 Hyperliquid 标的不允许买入、卖出或增仓时的原因诊断，以及市场恢复后的通知。起因是 `main / ZETA` 手工买入返回：

```text
Cannot increase position when open interest is at cap.
```

目标是区分普通增仓与 Reduce Only 平仓，保留具体 DEX 和原始市场，展示官方限制及盘口信息，并允许用户监控普通增仓何时恢复。本任务不接入钱包、不签名、不发送探测订单，也不保证特定账户的订单一定成交。

## 分支与基线

- 分支：`codex/frontend-localization-polish`
- 开始基线：`d191e8ee53772367e9d07e0f0643d367d81aff77`
- 功能提交：`721dd986f1940cc1ebc7bfee028134d19aa0f71e`
- 功能提交说明：`feat: monitor Hyperliquid trade availability`

## 已完成功能

- 新增 Hyperliquid 交易状态服务，读取官方 `metaAndAssetCtxs`、`perpsAtOpenInterestCap` 和 `l2Book`。
- 以官方 `perpsAtOpenInterestCap` 作为市场级 OI 上限信号，不能因订单簿仍有报价就判断普通增仓可用。
- 严格保留 `dex + raw_symbol`，例如 `main / ZETA`；不会只按规范化 ticker 合并不同 DEX 的市场。
- 分别展示普通买入/做多、普通卖出/做空、买入 Reduce Only 平空、卖出 Reduce Only 平多。
- 展示买一、卖一、1% 盘口深度、OI、24h 成交额、标记价、预言机价、资金费率和一小时资金周期。
- 明确手续费未计入且取决于账户等级与订单类型；原始 Hyperliquid 市场倍率显示为 `1x`。
- 上游请求使用独立超时和最多三次重试；持续失败映射为 HTTP `502`，不再泄漏为不明 `500`。
- 新增持久化恢复监控表和 15 秒后台轮询。
- 受监控方向从 `blocked/unknown` 变为 `available` 时发送一次飞书恢复通知；状态不变时保持安静。
- 标的查询页新增交易状态区、OI 上限告警、Reduce Only 操作说明和“监控/停止”按钮。
- 桌面和 390px 手机视口已做浏览器检查；手机表格可横向查看交易明细，关键市场、限制和监控列固定可见。

## API 与代码入口

接口：

```text
GET    /api/hyperliquid/trade-status/{symbol}
GET    /api/hyperliquid/trade-status/watches/list
POST   /api/hyperliquid/trade-status/watches
DELETE /api/hyperliquid/trade-status/watches/{watch_id}
```

后端入口：

- `backend/app/models/hyperliquid_trade_status.py`
- `backend/app/services/hyperliquid_trade_status.py`
- `backend/app/api/routes_hyperliquid_trade_status.py`
- `backend/app/db/schema.py`
- `backend/app/main.py`
- `backend/tests/test_hyperliquid_trade_status.py`

前端入口：

- `frontend/src/api/types.ts`
- `frontend/src/api/client.ts`
- `frontend/src/pages/InstrumentLookupPage.tsx`
- `frontend/src/styles.css`
- `frontend/tests/InstrumentLookupPage.test.tsx`

## 重要业务规则

- OI 达上限表示不能新开或增加总仓位。盘口存在并不代表普通增仓订单会被接受。
- 平空应使用 Buy / Long 并勾选 Reduce Only；平多应使用 Sell / Short 并勾选 Reduce Only。
- Reduce Only 数量不能超过实际对应持仓，并且钱包、子账户、DEX、原始市场和持仓方向必须一致。
- 公开接口只能给出市场级判断。`conditional` 不表示平仓一定成功；账户余额、仓位和交易所签名订单校验仍可能拒绝。
- 不发送真实探测订单，不保存钱包凭据。
- 监控的是普通增仓恢复，不把 Reduce Only 的账户级可用性误报为已确认。
- 资金费率周期固定明确为一小时；盘口深度为快照而不是成交保证；手续费未计入。

## 验证结果

- 后端专项测试：`5 passed`。
- 后端全量测试：`725 passed, 13 warnings`。
- 标的查询页测试：`18 passed`。
- 前端生产构建：通过。
- Ruff 专项检查：通过。
- `git diff --check`：通过。
- 真实 Hyperliquid API：已确认 `main / ZETA` 曾在官方 OI 上限列表，普通买卖为 `blocked`，两种 Reduce Only 为 `conditional`。
- 上游失败回归：状态接口返回 `502 Bad Gateway`。

前端全量测试结果为 `154 passed, 1 failed`。唯一失败仍是交接基线已记录的无关旧测试：

```text
SettingsPage > loads and saves Live Pilot settings
Unable to find an element with the text: 实盘灰度
```

页面已把该区域改名为“正差价正费率实盘实验”，测试仍查找旧文案。本任务没有改动 Settings 模块。

## 生产备份与部署

- 部署前数据库备份：`backups/radar-20260921T053714Z.db`
- 大小：`722444288` 字节
- SHA-256：`a326b514e8997b9b04ff987987e725770b252d50d02387259d5aae78dcf62d7a`
- SQLite 校验：`PRAGMA quick_check = ok`
- 服务器使用 `git pull --ff-only` 更新到功能提交 `721dd986f1940cc1ebc7bfee028134d19aa0f71e`。
- 使用 `docker compose build --pull` 和 `docker compose up -d --remove-orphans` 重建，没有执行 `down -v`。
- 前后端容器均为 `healthy`。
- `/api/health` 返回 `ok`。
- 线上状态接口、监控列表接口和标的查询页面均验证通过。

## 线上实际观察

部署后的连续检查观察到 ZETA 状态发生真实切换：

- 较早检查：`main / ZETA` 的 `at_open_interest_cap=true`，普通买卖均为 `blocked`。
- 2026-09-21 13:42 左右：`at_open_interest_cap=false`，普通买卖均为 `available`。
- 恢复快照买一约 `0.05917`、卖一约 `0.05918`。
- 24h 成交额约 `1,287,493.36 USDT`。
- 资金费率约 `-0.00626665% / 1h`。

这些价格、成交额和资金费率是当时快照，不应作为后续成交承诺。当前监控列表为空，用户需要在标的查询页点击“监控”才会持久化该市场的恢复通知。

## 已知限制与残余风险

- OI cap 可能在短时间内反复进入和解除，页面和通知只能反映最近一次官方公开状态。
- 公共 API 无法证明某个账户具备正确持仓，也无法预判余额、保证金、nonce、签名或风控拒单。
- Reduce Only 若仍失败，需要保留完整原始报错，并核对钱包、子账户、持仓方向、数量、DEX 和原始市场。
- 飞书发送失败时会保留旧状态并在后续轮询重试恢复通知，但仍依赖生产环境已有飞书配置。
- 全量前端测试仍有一个与本模块无关的旧文案失败，以及既有 Ant Design 弃用和 `act(...)` 警告。

## 工作区保护项

以下本地未跟踪文件属于既有产物或其他研究任务，本任务未提交、删除或覆盖：

- `output/edge-profile-codex/`
- `output/floating-watch-*.png`
- `output/index-auto-watch-*.png`
- `output/dexe_bybit_bitget_chain_probe/`
- `script/dexe_bybit_bitget_chain.py`

服务器上的 `.env.backup-codex-20260911-1425`、`.env.backup-poll-8-20260911` 和 `CACHED` 也保持不动。

## 下一步建议

- 若要监控 ZETA，再次查询 `ZETAUSDT`，确认原始市场为 `main / ZETA` 后点击“监控”。
- 若要判断特定账户能否平仓，需要另开任务设计只读账户仓位诊断；该任务必须明确钱包/子账户授权边界，不能把公有市场状态当作账户状态。
- 修复 Settings 页旧文案测试应作为独立小任务处理，避免扩展本模块范围。
