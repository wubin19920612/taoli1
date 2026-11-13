# crossex-exec 第十九轮任务书（2026-11-13）

背景：11-13 在服务器用线上库副本 + 新版代码（9a4ccc0 + 本轮 PM 已提交的 history-position 空页修复）手工跑 Engine.startup：启动成功，但外仓收益核实一直失败，停开锁 external_income_unverifiable 无法解除。逐层排查到以下真实形态（只读接口实测，生产 UTA/CrossEx 账户）。

## 已由 PM 修复（本轮不要改动其语义）
- Bitget `/api/v3/position/history-position` 空窗口同样返回 `{"list":null,"cursor":null}`，已加入 `history.NULL_TERMINAL_ENDPOINTS`，测试已更新。

## P1-A（必须修）CrossEx account_book 的 USDT 本位合约行 exchange_type 为 `CROSSEX`
实测 3 天 `/api/v4/crossex/account_book`（symbol, exchange_type, coin, statement_type → 行数）：
```
('BINANCE_FUTURE_OPENAI_USDT','CROSSEX','USDT','FUNDING_FEE') 3
('BINANCE_FUTURE_OPENAI_USDT','CROSSEX','USDT','TRADING_FEE') 1282
('BINANCE_FUTURE_OPENAI_USDT','CROSSEX','USDT','TRANSACTION') 182
('HYPERLIQUID_FUTURE_io:OAI_USDC','HYPERLIQUID','USDC', FUNDING_FEE/TRADING_FEE/TRANSACTION)
('HYPERLIQUID_CONVERT_USDC_USDT','CROSSEX','USDT','TRANSACTION') 1
('HYPERLIQUID_CONVERT_USDC_USDT','HYPERLIQUID','USDC','TRANSACTION') 1
('', 'CROSSEX','USDT','TRANSFER_IN'/'TRANSFER_OUT'); ('', 'LIGHTER','USDC','INTEREST_ISOLATED'/'TRANSFER_IN')
```
而 `/api/v4/crossex/history_trades` 同一币安合约的 exchange_type 是 `BINANCE`（business_type FUTURE，fee_coin USDT，DUAL，LONG/SHORT）；HYPERLIQUID_CONVERT 成交 business_type CONVERT、fee_coin ''、position_side ''。`history_positions` 无 exchange_type 字段。

后果：
1. `external_accounting._evaluate` 外仓分支 `row['exchange_type']!=i['exchange_type']` → `cash_identity`，有币安 USDT 外仓就永远核实失败（今天的现状）。
2. 本程序分支 `symbol.startswith(row['exchange_type']+'_FUTURE_')` → `program_cash_identity`：本程序在 CrossEx 的币安/OKX/Gate 等 USDT 腿**第一笔成交后**就会失败停开。必须修。

要求：USDT 结算的 CEX 合约行接受 `exchange_type=='CROSSEX' and coin=='USDT'`，且 symbol 前缀（`<EXCHANGE>_FUTURE_`）必须与登记身份/本程序合约的交易所一致、quote 为 USDT；USDC 结算（HYPERLIQUID 等）保持现有严格一致。不要放宽成“任意 exchange_type”。检查 cash_balances / funding_positions / 资金费检查点 / program_activity / equity.py 等所有用 account_book exchange_type 的地方是否有同类假设，一并修正。

## P1-B（必须处理）CONVERT 与 Lighter 利息
- `HYPERLIQUID_CONVERT_USDC_USDT`：一笔兑换产生两条 account_book（USDC 减、USDT 加）和一条 business_type=CONVERT 的成交。当前会落到 unattributed_cashflow / trade_ownership_unknown 而停开。PM 裁定：把它当作币种间资本兑换（同 capital_transfer 处理思路：不计合约收益、不计本程序盈亏），但现金余额连续性校验仍要成立；成交表里 business_type=CONVERT 的行不参与持仓连续性。不能借此放过其他未知 statement_type。
- `INTEREST_ISOLATED`（symbol 空，LIGHTER USDC）：借贷利息，属于外部账户负债成本。PM 裁定：按外部现金流处理（不计本程序盈亏、不阻塞开仓），保持余额连续性校验；其他未知类型仍 fail-closed。

## P2（第十七轮终审遗留）
历史校验失败时缓存可超 25000 行/8MiB 上限：改为全部校验成功后统一替换缓存。

## 验收
- 新增测试用上面的真实形态做夹具（外仓币安 USDT + HL USDC、本程序 USDT 腿资金费/手续费/成交、CONVERT 一对、利息行、未知 statement_type 仍拒绝、symbol 前缀与登记交易所不符仍拒绝）。
- 3.12 与 3.10 两条全量命令全过：`python -m unittest discover -s tools/crossex_exec/tests -v`、`python -m unittest discover -s tools/fill_sim/tests -v`；3.10 解释器 `C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/125141d8-f3d5-4518-98a0-6a4761963215/scratchpad/uvpy/cpython-3.10-windows-x86_64-none/python.exe`。
- 模拟器三种子照旧全过。
- 不改 limits.py 硬上限常量；不改下单路径。
- 交接文档 docs/task-handoff-2026-11-12-crossex-exec-r8.md 追加“第十九轮”一节（改了什么、原始测试汇总行）。小步提交。
