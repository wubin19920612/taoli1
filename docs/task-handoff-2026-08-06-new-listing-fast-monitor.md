# 交接文档：新币极速价差监控

更新时间：2026-08-06
项目目录：`C:\Users\wubin\Desktop\code\codex\taoli1`
Git 分支：`codex/frontend-localization-polish`
最新提交：`1800013 Add new listing fast spread monitor`
远端状态：已推送到 `origin/codex/frontend-localization-polish`

## 1. 当前目标

本次新增了一条独立的“新币极速价差监控”通道，用于捕捉新币上市早期的短时跨交易所真实价差，例如 UNITREE 在 Bybit 和 Gate 之间出现的快速收敛机会。

这条通道和普通套利机会池分开，不能继续完全依赖普通机会的风险过滤逻辑。重点是：

- 秒级采样；
- 使用盘口 `ask/bid` 计算可成交方向；
- 保存价差样本和提醒事件；
- 低流动性不直接隐藏，只作为风险标签；
- 历史查询可以说明当时是否被监控、是否触发、没有触发的原因。

## 2. 已完成内容

### 后端

新增文件：

- `backend/app/models/new_listing.py`
  - 新币监控项参数模型；
  - 秒级价差样本模型；
  - 新币告警事件模型；
  - 状态和历史查询结果模型。

- `backend/app/services/new_listing_monitor.py`
  - 新币监控项保存；
  - 按标的和交易所采样；
  - 交易所两两组合并计算双向价差；
  - 使用低价交易所 `ask` 买入、高价交易所 `bid` 卖出；
  - 计算原始价差、手续费和滑点后的净价差；
  - 估算顶层盘口可成交金额；
  - 普通、强提醒、极端提醒三级触发；
  - 连续命中、冷却时间和未告警原因记录；
  - 保存秒级样本、提醒事件；
  - 支持历史复盘。

- `backend/app/api/routes_new_listing_monitor.py`
  - `GET /api/new-listing-monitor/exchanges`
  - `GET /api/new-listing-monitor/watchlist`
  - `POST /api/new-listing-monitor/watchlist`
  - `DELETE /api/new-listing-monitor/watchlist/{item_id}`
  - `POST /api/new-listing-monitor/watchlist/{item_id}/collect`
  - `GET /api/new-listing-monitor/status`
  - `GET /api/new-listing-monitor/samples`
  - `GET /api/new-listing-monitor/events`
  - `GET /api/new-listing-monitor/history`

### 数据库

`backend/app/db/schema.py` 新增：

- `new_listing_watchlist`
- `new_listing_spread_samples`
- `new_listing_alert_events`

同时扩展了 `second_level_market_samples`：

- `spot_bid_size`
- `spot_ask_size`
- `future_bid_size`
- `future_ask_size`

已有数据库启动时会自动补列，不需要手工迁移。

### 秒级采样

`backend/app/services/second_level_sampler.py` 已补充部分交易所的顶层盘口数量解析：

- Bybit；
- Binance；
- Aster；
- Bitget；
- OKX。

Gate 当前主要能拿到价格，盘口数量可能为空。数量为空时，新币监控会标记 `DEPTH_UNKNOWN`，不会因为深度未知直接隐藏价差。

### 前端

新增页面：

- `frontend/src/pages/NewListingMonitorPage.tsx`

页面入口已挂到：

- `frontend/src/components/AppShell.tsx`

左侧菜单名称：`新币极速`

页面包含：

- 新币监控参数配置；
- 标的、交易所、现货/合约选择；
- 1 秒采样周期；
- 普通/强/极端阈值；
- 最低可成交金额；
- 连续确认次数和冷却时间；
- 已保存标的列表；
- 实时极速机会表；
- 历史净价差图；
- 告警事件表；
- “立即采样”按钮。

前端 API 类型和封装分别在：

- `frontend/src/api/types.ts`
- `frontend/src/api/client.ts`

## 3. 默认监控参数

新建监控项默认值：

```text
标的：UNITREEUSDT
市场：合约
交易所：Bybit、Gate、Bitget、OKX、Binance
采样周期：1 秒
记录保留：72 小时
普通净价差阈值：3%
强提醒净价差阈值：8%
极端净价差阈值：15%
最低可成交金额：100 USDT
深度验证金额：300 USDT
普通连续次数：2
强提醒连续次数：1
极端连续次数：1
冷却时间：60 秒
买入手续费：0.05%
卖出手续费：0.05%
滑点缓冲：0.10%
允许低流动性提醒：是
```

## 4. 关键计算逻辑

新币极速通道不使用历史 K 线收盘价作为实时告警依据。

交易方向：

```text
买入价 = 便宜交易所 ask
卖出价 = 贵价交易所 bid
```

原始价差：

```text
(卖出 bid - 买入 ask) / 买入 ask * 100
```

净价差：

```text
原始价差 - 买入手续费 - 卖出手续费 - 滑点缓冲
```

可成交金额：

```text
min(买入 ask * 买入 ask 数量,
    卖出 bid * 卖出 bid 数量)
```

如果两边盘口数量有一边拿不到，可成交金额为 `null`，页面显示“深度未知”。

## 5. 告警行为

告警级别：

- `normal`：净价差达到普通阈值；
- `strong`：净价差达到强提醒阈值；
- `extreme`：净价差达到极端阈值；
- `none`：未达到普通阈值。

可能的未告警原因：

- 净价差低于普通阈值；
- 连续确认次数不足；
- 同一方向处于冷却时间；
- 已知可成交金额低于最低金额；
- 没有拿到两边有效盘口。

风险标签：

- `NEW_LISTING`
- `DEPTH_UNKNOWN`
- `DEPTH_TOO_SMALL`
- `DEPTH_BELOW_TARGET`
- `BUY_SLOW_DATA`
- `SELL_SLOW_DATA`
- `LOW_LIQUIDITY_ALLOWED`

有飞书 webhook 时，新币提醒通过现有 `FeishuNotifier.send_text()` 发送；没有 webhook 时仍然会保存数据库事件。

## 6. 当前本地运行状态

当前本地服务已启动：

- 前端：`http://127.0.0.1:3000/?page=new-listing`
- 后端健康检查：`http://127.0.0.1:8000/api/health`
- 新币监控状态：`http://127.0.0.1:8000/api/new-listing-monitor/status`

当前后台进程：

- 后端端口 `8000`，PID `7328`
- 前端端口 `3000`，PID `40324`

启动日志：

- `logs/new-listing-backend.out.log`
- `logs/new-listing-backend.err.log`
- `logs/new-listing-frontend.out.log`
- `logs/new-listing-frontend.err.log`

如需重新启动：

```powershell
cd C:\Users\wubin\Desktop\code\codex\taoli1\backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
cd C:\Users\wubin\Desktop\code\codex\taoli1\frontend
npm run dev -- --host 127.0.0.1 --port 3000
```

## 7. 验证结果

已执行：

```powershell
cd backend
pytest tests/test_new_listing_monitor.py tests/test_second_level_sampling.py
```

结果：`9 passed`

已执行完整后端测试：

```powershell
cd backend
pytest
```

结果：`415 passed`

已执行前端生产构建：

```powershell
cd frontend
npm run build
```

结果：构建成功。

Ruff 没有执行成功，因为当前环境没有安装：

```text
No module named ruff
```

## 8. 当前工作区注意事项

当前 Git 分支和远端同步，最新提交为：

```text
1800013 Add new listing fast spread monitor
```

工作区中还有两个本次未处理、未提交的历史未跟踪项：

- `output/dexe_bybit_bitget_chain_probe/`
- `script/dexe_bybit_bitget_chain.py`

后续提交时不要使用 `git add .`，必须显式指定本次任务文件，避免把这两个文件带入提交。

## 9. 当前已知限制

第一版仍然是 REST 秒级采样，不是 WebSocket 盘口流。后续如果要进一步降低延迟和漏采，需要接入：

- Bybit WebSocket orderbook；
- Gate futures WebSocket orderbook；
- Bitget / OKX / Binance 的盘口流；
- 统一的本地盘口快照和重连机制。

当前还没有：

- 自动发现交易所新上市标的；
- 自动从公告中加入新币监控；
- 深度多档吃单模拟；
- 自动下单；
- 半自动双腿对冲；
- 单边成交后的自动补救。

因此当前系统适合先做：

```text
秒级发现
秒级记录
飞书提醒
历史复盘
人工下单验证
```

不应该直接把第一版接到全自动真实资金下单。

## 10. 建议下一步

推荐按以下顺序继续：

1. 用 UNITREE 做实盘观察，确认 Bybit/Gate 的实际秒级采样是否稳定。
2. 检查 Gate 返回价格和盘口数量是否足够支撑可成交金额判断。
3. 增加“监控命中率/漏报诊断”统计。
4. 增加交易所新币公告和合约列表自动发现。
5. 将 Bybit/Gate 盘口切换为 WebSocket。
6. 增加多档订单簿深度验证。
7. 先做纸面成交或半自动下单，再评估自动执行。

交接给下一窗口时，第一步应先读取本文档，再检查：

```powershell
git status --short --branch
git log -1 --oneline
```

不要重做 `1800013` 已完成的功能，除非后续任务明确要求修改新币极速监控。
