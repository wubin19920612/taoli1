# crossex-exec 第二十九轮任务书（2026-11-13）

背景：5771c81 部署影子（18:03 CST）后快照新鲜、account_snapshot_unavailable 解除，但 account_liability 每约 40s 抖动一次（10 分钟内 3 次停开、4 次恢复，告警反复）。

## P1 外部负债归属的保证金交叉校验过严
program_risk.py debt_attribution 要求 `dec(asset.futures_initial_margin) == budgets[group]`（外部持仓 initial_margin 之和）精确相等。两者来自不同接口、读取有先后，标记价变化导致短暂不等。线上实测（HYPERLIQUID USDC，用户 io:OAI 空仓，负债 146.85）：snapshot 3460 资产 266.5940728 vs 持仓 266.6139872 → blocking → 全局停开，3 个干净快照后恢复，循环往复。
要求：
- 允许读取时序差：|资产 futures_initial_margin − 持仓 initial_margin 之和| ≤ 容差（相对 1%，写成命名常量，不进 limits.py），且负债 ≤ 二者较小值（保守）。超容差、缺字段、混入程序合约/在途订单仍 blocking（保持现有 fail-closed）。
- 不得放宽：负债必须 ≤ 外部保证金、同组不得混入程序仓位/在途订单、不做跨币种换算。
- 告警去重：同一负债事件抖动不得反复推送（沿用现有 active_key 去重机制）。
- 测试：用上面的真实数值复现（偏差 0.0199 应通过；偏差 >1% 应 blocking；负债 > 较小值应 blocking）。

## 验收
测试先行；不改 limits.py、不改下单路径、不放宽旧测试；3.12 与 3.10 两条全量（crossex_exec、fill_sim），模拟器三种子全过；交接追加“第二十九轮”；小步提交并推送。
