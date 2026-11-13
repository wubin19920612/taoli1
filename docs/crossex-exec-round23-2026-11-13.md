# crossex-exec 第二十三轮任务书（2026-11-13）

第二十二轮（e14796f）astra max 只读复审：原 4 个 P1 已修，剩 1 个 P1。

## P1 program_funding.py:57、:76 混仓结束后采样间隔内的资金费仍按旧混仓比例折减
复现（SQLite :memory:，round15_simulator.ProgramWorld）：advance→open→advance；venue0.quantity=own*9；settle('-9')；advance；venue0.quantity=own；now+=0.5；settle('-21')；advance(0.5)；再 advance×3。应认证 program_daily = -22（混仓期 -9 按 1/9 分摊 -1 + 撤仓后程序独付 -21），实际 -3.3333，can_open=True，漏触发日亏。
要求：混仓区间的结束边界未被采样精确确认时（最后一次“混仓”采样与第一次“干净”采样之间），落在该间隔内的每笔资金费都按对风控不利的方向归属：费用（负）全额计给程序，收入（正）按该间隔内可能的最小程序比例计。不能确定时保持停开，不能当零。同理检查混仓区间开始边界（最后一次干净采样与第一次混仓采样之间）。
先把上面的复现写成失败测试，再修。

## 验收
- 3.12 与 3.10 两条全量（crossex_exec、fill_sim），3.10 解释器同前几轮；模拟器三种子全过。
- 不改 limits.py、不改下单/发送路径；不放宽已有测试。
- 交接文档追加“第二十三轮”一节。小步提交并推送。
