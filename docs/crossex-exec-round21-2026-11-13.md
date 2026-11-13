# crossex-exec 第二十一轮任务书（2026-11-13，用户：赶紧做）

前情：第二十轮 P1-A/B/C 已提交（7af31bc..5f5dfb2：逐仓外部持仓、启动不因外部状态失败、刷新失败安全诊断）。第二十轮 P2（核实抖动）**取消**，被本轮取代；其交接节由本轮一并补写（先写第二十轮已完成内容，再写第二十一轮）。

用户原话："不是说我开的仓位和你没关系吗"。用户在同一 CrossEx 账户用 Astro 高频交易（含 HL 子市场逐仓）。现状仍要求每个新外部仓位手工登记、并逐条核对整个账户现金流来证明本程序盈亏，导致用户每开一笔就停开、核实随用户交易抖动（流水约 40% 读不全、核实延迟 4–25s 超 10s 窗口）。

## A. 仓位归属自动判定（替代手工登记，作为默认）
- 归属依据：本地库订单。某 (route, symbol) **只要本程序有过任何非 SHADOW/UNSENT 的订单记录（含 INTENT/SENT/UNKNOWN/终态，发送前已落库）即为“程序合约”**；否则为“外部合约”。
- 外部合约上的任何仓位（任意方向、全仓或逐仓、任意数量变化）自动视为外部仓位：不需要登记、不触发 position_mismatch、不停开、不计入本程序任何盈亏/限额。
- 程序合约上的仓位必须与本程序账本精确一致；不一致照旧停开（这是丢腿保护，不能放松）。
- **禁止混仓**：CrossEx DUAL 下同合约同方向仓位会合并。准入时：若某合约当前存在外部仓位（任意方向），本程序不得在该合约开仓；本程序在某合约有未平仓位时，若该合约出现超出本程序账本的数量（用户也在交易它），停该合约开仓并告警（中文可读原因），本程序仍按自身账本数量平仓，绝不减到穿零/反向。
- 手工登记表保留兼容（已登记的照样视为外部），但不再是开仓前提；ADMIN/前端接口不删除。
- 缓存程序合约集合时注意：新 INTENT 落库与归属判定之间不得有竞态（同一把锁或先落库后判定）。

## B. 本程序盈亏只按本程序自己的成交与持仓计算
- 日亏 20U、累计亏 100U、权益回撤 150U（改为本程序口径）、600U 名义额：全部只用本程序订单的成交（价格、数量、手续费，来自各订单的成交查询，已有）+ 本程序合约的资金费 + 本程序仓位按标记价的浮动盈亏。
- 资金费只读本程序合约的记录（CrossEx account_book 按 symbol 过滤、Bitget financial-records 按 symbol 过滤）；**不再要求整户流水完整、不再做整户现金连续性证明**。本程序合约上读不到/读不全资金费时：保守处理（按不利方向估计或停开），不能当零。
- 外部仓位、外部现金流、划转、兑换、利息、用户盈亏一律不进入上述任何指标。
- 移除/停用由整户现金核实驱动的停开锁（external_income_unverifiable / external_baseline_unverifiable / history_coverage 等）作为开仓前提；相关代码可保留为诊断但不得阻塞开仓。rebaseline 后不再需要外部锚点。
- 保留：**整户保证金覆盖倍数**（全仓账户物理强平风险），低于阈值时本程序停新开并只减本程序仓位；口径保守，写入交接。
- 保留所有硬上限常量（limits.py 不改）、发送前二次校验、不改下单路径。

## C. 线上真实形态（夹具）
- 当前账户外部仓位：BINANCE_FUTURE_OPENAI_USDT LONG CROSS 5x；HYPERLIQUID_FUTURE_io:OAI_USDC SHORT CROSS 5x；HYPERLIQUID_FUTURE_UNITREE_USDC LONG **ISOLATED** 5x（目录 tag=xyz、support_cross=false）；ASTER_FUTURE_UNITREE_USDT SHORT CROSS 2x。本程序在 LIVE=0 下从未下单。
- CrossEx account_book USDT 合约行 exchange_type=CROSSEX（第十九轮已处理）。

## 验收
- 测试先行，至少覆盖：外部合约上开/加/减/翻向/逐仓都不停开；程序合约出现多余数量停该合约且不穿零；外部仓位存在的合约拒绝开仓；本程序日亏/累计/回撤只随本程序成交与资金费变化、不随外部盈亏与流水变化；本程序合约资金费缺失不当零；崩溃后 INTENT 已落库的合约仍判程序合约（丢腿保护）；保证金覆盖倍数低时只减本程序仓位。
- 模拟器（INVARIANTS / PROGRAM_INVARIANTS）三种子全过，必要时扩展场景：用户在外部合约随机交易。
- 3.12 与 3.10 两条全量命令：`python -m unittest discover -s tools/crossex_exec/tests -v`、`python -m unittest discover -s tools/fill_sim/tests -v`；3.10 解释器 `C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/125141d8-f3d5-4518-98a0-6a4761963215/scratchpad/uvpy/cpython-3.10-windows-x86_64-none/python.exe`。
- 旧测试中与本轮裁定矛盾的断言（例如“外部仓位未登记必须停开”“整户流水不完整必须停开”）可改，但每一处在交接写明原因；其余不得放宽。
- 看板契约（docs/live-exec-contract-2026-11-11.md 与 /api/v1/summary 等字段）如有变化，只增不删，并在交接列出。
- 交接文档 docs/task-handoff-2026-11-12-crossex-exec-r8.md 追加“第二十轮”“第二十一轮”两节。小步提交。
