# crossex-exec 第二十二轮任务书（2026-11-13）

第二十一轮（cf8a3b0）astra max 只读终审：无 P0，4 个 P1 + 1 个 P2，均已用内存库复现。审查日志结论如下，逐条修复，每条先写失败测试再修。

## P1-1 ownership.py:105 bounded_pair_positions 把 UNKNOWN 未确认余量计入可平数量
实测：程序成交 0.100、用户在同合约加仓 0.400，程序减仓 0.490，误平用户 0.390。
要求：可平数量绝不超过“程序已确认成交净额”；UNKNOWN/SENT/ACK 的未确认余量只能用于“恢复/查询成交”，不能用于给减仓扩额。若合约出现超出账本的数量（混仓）且存在未确认委托，先停该合约开仓、查清委托成交，再按确认数量平；宁可暂留裸腿并告警，也不能平到用户仓位。

## P1-2 ownership.py:101 journal_positions 在“未知开仓未补齐 + 部分减仓已确认”时抛 negative_local_position
实测：剩余 0.245 裸腿，恢复持续失败，无法自动平仓。
要求：该中间态不能让恢复/平仓整体失败；按委托生命周期正确计算（未确认开仓成交以查询结果为准，查询前以保守方式处理），保证恢复能继续推进；不得因此把用户仓位算成程序仓位。

## P1-3 engine.py:690 附近跟踪路径只排除手工登记外仓，自动归属的外仓浮盈亏进入 program_last_upnl
实测：外仓 −9000 次日被认证为程序日盈 +9000，可掩盖日亏保护。
要求：所有程序盈亏/浮盈亏/日亏/累计/回撤的计算路径统一用 ProgramOwnership 拆分后的程序仓位；全局搜索 upnl、program_last_upnl、equity_baseline 等使用点，确保没有任何一处还按“手工登记表”判外仓。

## P1-4 program_accounting.py:151 只检查当前数量一致，未保留混仓期间资金费不可归属状态
实测：用户撤掉额外仓位后，混仓期间资金费 −90 被全部认证为程序亏损。
要求：某程序合约在混仓期间（远端数量 > 程序账本）发生的资金费按比例或保守方式归属，并记住“该区间不可精确归属”的状态；不能整笔归程序（误触发）也不能整笔排除（掩盖）。推荐：按程序数量/远端总数量比例分摊，并在不可确定时按不利方向估计；写进交接说明口径。

## P2 tests/test_round18_lifecycle.py:104 删掉了 POSITIONS 接口调用断言
恢复该断言（与裁定不冲突）。

## 验收
- 每个 P1 一个复现测试（审查已给出数值，可直接用），修复后通过。
- 3.12 与 3.10 两条全量：`python -m unittest discover -s tools/crossex_exec/tests -v`、`python -m unittest discover -s tools/fill_sim/tests -v`；3.10 解释器 `C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/125141d8-f3d5-4518-98a0-6a4761963215/scratchpad/uvpy/cpython-3.10-windows-x86_64-none/python.exe`。模拟器三种子全过。
- 不改 limits.py、不改下单/发送路径；不放宽已有测试。
- 交接文档 docs/task-handoff-2026-11-12-crossex-exec-r8.md 追加“第二十二轮”一节。小步提交并推送。
