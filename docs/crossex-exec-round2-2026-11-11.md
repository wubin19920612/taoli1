# crossex-exec 第二轮（PM 决定，2026-11-11）

Claude 已用真实 key 做了 C1 只读核对，结果如下。按这些结果修改，约束仍以 `docs/crossex-exec-spec-2026-11-11.md` 为准（不调用鉴权接口、不 push、不 SSH、不 WSL、不 amend，按 test/feat/docs 分开提交）。

## C1 事实（字段名与取值形态，已脱敏）
- **CrossEx** `GET /crossex/accounts`
  - 示例：`{"margin_balance":"254.06","available_margin":"154.19","initial_margin":"99.87","maintenance_margin":"49.93","initial_margin_rate":"2.5439","maintenance_margin_rate":"5.0879","position_mode":"DUAL","account_mode":"CROSS_EXCHANGE","assets":[{"coin":"USDC","exchange_type":"LIGHTER","balance":"-498.86","liability":"498.86","borrowing_initial_margin":"99.77",...}]}`
  - 两个 rate 都是“保证金余额 ÷ 对应保证金”的倍数：254.06/99.87=2.544，254.06/49.93=5.088。
  - 风控用 `maintenance_margin_rate`：<3 停开，<2 全平；`maintenance_margin` 为 0 时视为安全。
  - 资产里任何 `liability>0` 都要写进状态日志。
- **CrossEx** `GET /crossex/fee`
  - 返回列表，每项含 `exchange_type`（BINANCE/OKX/GATE/BYBIT/ASTER…）、`future_taker_fee`、`future_maker_fee`，都是小数字符串。实测各所 taker 均为 0.00048。
  - 另有 `special_fee_list`，按 `symbol` 覆盖默认费率，要优先使用。
- **CrossEx** 持仓、挂单接口返回 `[]`。账户是 `position_mode=DUAL`。
- **Bitget** 是**统一账户（UTA）**。所有 v2 classic 接口都返回 `{"code":"40085","msg":"You are in Unified Account mode, and the Classic Account API is not supported at this time"}`。
- **Bitget v3**（实测可用）：
  - `GET /api/v3/account/settings`：`data.accountMode="unified"`、`holdMode="hedge_mode"`、`assetMode="multi_assets"`，`symbolConfigList` 里有逐合约的 `marginMode` 和 `leverage`。
  - `GET /api/v3/account/assets`：`data.accountEquity`、`usdtEquity`、`effEquity`、`mmr`、`imr`、`mgnRatio`、`assets[]`。
  - `GET /api/v3/position/current-position?category=USDT-FUTURES`：无持仓时 `data.list=null`。
  - `GET /api/v3/account/fee-rate?symbol=BTCUSDT&category=USDT-FUTURES`：`data.takerFeeRate="0.0006"`、`makerFeeRate="0.00019"`。
  - `GET /api/v3/trade/unfilled-orders?category=USDT-FUTURES`：`data.list=[]`。
- 该 Bitget 账户权益约 3 万 USDT。用户已决定就用这个账户，所以下面的硬上限是**必须项**。

## 要做的
1. **Bitget 改为 UTA v3**
   - 下单、查单、撤单、成交、持仓、资产、费率、设置全部用官方 v3 UTA 接口；先查官方文档，交接里列出处 URL。
   - 启动时检查 `accountMode=="unified"`，否则拒绝启动。
   - 删除 classic v2 交易路径；不要保留两套，以免误用。
   - 签名方式不变，用文档里的示例向量测试。
2. **持仓模式**：默认值改为 `CROSSEX_POSITION_MODE=DUAL`、`BITGET_POSITION_MODE=hedge_mode`。启动时读取真实模式，与配置不一致就拒绝启动。
3. **环境变量改名**：服务器上已写好的变量名是 `CROSSEX_API_KEY`、`CROSSEX_API_SECRET`、`BITGET_API_KEY`、`BITGET_API_SECRET`、`BITGET_API_PASSPHRASE`。代码和 env 示例改用这些名字，`GATE_API_*` 不再支持。
4. **路径**：默认路径改到 `/home/ubuntu/wubin/crossex-exec/var/`，包括数据库和 STOP 文件。env 文件路径是 `/home/ubuntu/wubin/crossex-exec/crossex-exec.env`。service 模板同步修改。
5. **代码内硬上限**：写成模块常量，配置只能调低，不能调高。
   - 单腿名义额 ≤150 USD；
   - 所有未平仓位的名义额合计 ≤600 USD；
   - 启动时记录两边账户权益基线（CrossEx `margin_balance`，Bitget `usdtEquity`），任一边下降 ≥150 USD 就全平并进入 `HALTED`；
   - 每一笔开仓单发送前，在同一个函数里重新校验以上三项。平仓单和 reduce_only 单不受前两项限制。
   - 测试：每项超限都会拒发，并且确认没有调用下单接口。
6. **Bitget 保证金率**：用 `effEquity / mmr` 作为倍数，阈值和 CrossEx 相同（<3 停开，<2 全平）；`mmr==0` 时视为安全。在交接里写明这个口径，列为需要上线前确认的点。
7. **费率**：开仓门槛用两边真实接口返回的 taker 费率（CrossEx 先看 special，再看默认值）。读不到就不开仓。

## 测试与交付
- 两条 unittest 命令都要全部通过，贴原始汇总行。
- 交接文档追加本轮内容，单独一个 docs 提交。
