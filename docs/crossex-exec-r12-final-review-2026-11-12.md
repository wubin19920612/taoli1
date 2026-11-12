codex
**可影子部署：是，仅限 `LIVE=0` 验证。代码层面可开实盘：否。**

审查范围 `33933b1..8046f3d`。**上次 3 项 P1＋1 项 P2 已修复；本次发现 5 项 P1、1 项 P2，无 P0。**

1. **P1｜旧快照对账可以重新启用已被新快照撤销的收益证明。**  
   位置：[engine.py:694](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/engine.py:694)、[external_accounting.py:345](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external_accounting.py:345)。  
   两线程复现：协调器读取快照 A；账户线程发布 B 并撤销证明；协调器随后把 A 写成 `verified=true`。**B 只有一家 USDC 报价，最终仍 `can_open=True`，订单风控检查通过。**  
   建议：证明绑定账户快照版本；提交核实结果时原子比较当前版本，最终发送再次要求证明与缓存版本一致。

2. **P1｜起点之后已核实的流水消失，仍可被当作完整证据。**  
   位置：[external_accounting.py:142](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external_accounting.py:142)。  
   历史完整性只对比计量起点的 `history_index`，没有核对之后已经见过的流水。复现：同交易所、同币种外仓资金费 `+5`、程序资金费 `−5`，首次核实日亏为 `−5`；下一次响应遗漏两条流水，余额不变，**仍核实成功，日亏变成 0，允许开仓**。  
   建议：另存持续更新的已核实流水 ID／内容摘要，拒绝消失或改写；保留原计量起点，不能通过滚动起点修补。

3. **P1｜USDC 外仓名义额未折算，可突破 600 USD 总上限。**  
   位置：[accounts.py:196](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/accounts.py:196)、[limits.py:98](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/limits.py:98)。  
   `数量×标记价格` 的 USDC 金额直接参与 USD 求和。复现：外仓 **500 USDC**，两家汇率均 **1.01**，程序两腿各 **50 USD**；代码合计 **600** 并通过两腿检查，中位价折算已是 **605 USD**。此前外仓会阻断开仓，登记隔离后这个缺口可以实际影响准入。  
   建议：保留原币金额，同时生成保守 USD 名义额；缺价、过期或来源不足拒开，并覆盖最终发送关口。

4. **P1｜Bitget 正负债遗漏，管理操作的“无负债”前提不成立。**  
   位置：[accounts.py:199](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/accounts.py:199)、[equity.py:93](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/equity.py:93)。  
   负债列表只读取 CrossEx `liability`，忽略 Bitget UTA `assets[].debt`。复现 `debt=28.5`：`liabilities=[]`、开仓检查通过、rebaseline 成功；release-halt 共用这项检查。该字段由 [Bitget 官方账户文档](https://www.bitget.com/docs/catalog/account/assets-balance)明确提供。  
   建议：统一汇总两边负债，供开仓、管理前提和三快照恢复共同使用。

5. **P1｜停机核实失败后，可能复用旧“全户已平”结果成功退出。**  
   位置：[engine.py:1348](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/engine.py:1348)、[__main__.py:249](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/__main__.py:249)。  
   `flatten_all` 读取失败只告警，没有撤销旧 `flatten_status`。复现：保留一小时前的 `program_flat=true/account_flat=true`，本次 shutdown 仓位读取失败，主程序判断仍走 **退出码 0**，并输出旧全平状态。  
   建议：停机必须消费本次核实的明确结果；失败或未执行核实不得复用历史成功状态。

6. **P2｜重基线审计遗漏旧外仓计量基线。**  
   位置：[equity.py:13](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/equity.py:13)、[equity.py:178](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/equity.py:178)。  
   审计 `new` 写入外仓起点及程序浮盈亏基线，但 `old` 不含 `external_equity_anchor/program_daily_upnl/program_last_upnl`。覆盖后无法从本次审计还原旧归属计量状态。  
   建议：将这些旧值纳入同事务审计，覆盖 rebaseline 和 release-halt。

其他重点：未发现登记可隐藏已有程序订单／pair 历史，永久禁单覆盖减仓、别名和重启；未发现管理操作直接清除仓位类锁。原告警死锁已通过共用 Store RLock 消除，本次未发现新的锁序环。USDC **收益贡献**的正数取上界、负数取下界方向正确，但保证仅限 PM 授权估值区间；公开请求复用原限频队列。

验证使用指定 Python 3.10：**122 项专项测试通过**，仅将文件夹具替换为内存夹具、保留原断言；5 项跨进程文件数据库测试未运行。原模拟器三种子各 **2,664 回合**通过，85 个 Python 文件语法检查通过。对照基线逐方法检查 **499 个既有 `test_` 定义**，未发现删除或额外放宽。

**未修改文件、未提交、未调用真实账户；最终 HEAD 未变，工作区干净。**
tokens used
242,942
**可影子部署：是，仅限 `LIVE=0` 验证。代码层面可开实盘：否。**

审查范围 `33933b1..8046f3d`。**上次 3 项 P1＋1 项 P2 已修复；本次发现 5 项 P1、1 项 P2，无 P0。**

1. **P1｜旧快照对账可以重新启用已被新快照撤销的收益证明。**  
   位置：[engine.py:694](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/engine.py:694)、[external_accounting.py:345](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external_accounting.py:345)。  
   两线程复现：协调器读取快照 A；账户线程发布 B 并撤销证明；协调器随后把 A 写成 `verified=true`。**B 只有一家 USDC 报价，最终仍 `can_open=True`，订单风控检查通过。**  
   建议：证明绑定账户快照版本；提交核实结果时原子比较当前版本，最终发送再次要求证明与缓存版本一致。

2. **P1｜起点之后已核实的流水消失，仍可被当作完整证据。**  
   位置：[external_accounting.py:142](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external_accounting.py:142)。  
   历史完整性只对比计量起点的 `history_index`，没有核对之后已经见过的流水。复现：同交易所、同币种外仓资金费 `+5`、程序资金费 `−5`，首次核实日亏为 `−5`；下一次响应遗漏两条流水，余额不变，**仍核实成功，日亏变成 0，允许开仓**。  
   建议：另存持续更新的已核实流水 ID／内容摘要，拒绝消失或改写；保留原计量起点，不能通过滚动起点修补。

3. **P1｜USDC 外仓名义额未折算，可突破 600 USD 总上限。**  
   位置：[accounts.py:196](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/accounts.py:196)、[limits.py:98](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/limits.py:98)。  
   `数量×标记价格` 的 USDC 金额直接参与 USD 求和。复现：外仓 **500 USDC**，两家汇率均 **1.01**，程序两腿各 **50 USD**；代码合计 **600** 并通过两腿检查，中位价折算已是 **605 USD**。此前外仓会阻断开仓，登记隔离后这个缺口可以实际影响准入。  
   建议：保留原币金额，同时生成保守 USD 名义额；缺价、过期或来源不足拒开，并覆盖最终发送关口。

4. **P1｜Bitget 正负债遗漏，管理操作的“无负债”前提不成立。**  
   位置：[accounts.py:199](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/accounts.py:199)、[equity.py:93](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/equity.py:93)。  
   负债列表只读取 CrossEx `liability`，忽略 Bitget UTA `assets[].debt`。复现 `debt=28.5`：`liabilities=[]`、开仓检查通过、rebaseline 成功；release-halt 共用这项检查。该字段由 [Bitget 官方账户文档](https://www.bitget.com/docs/catalog/account/assets-balance)明确提供。  
   建议：统一汇总两边负债，供开仓、管理前提和三快照恢复共同使用。

5. **P1｜停机核实失败后，可能复用旧“全户已平”结果成功退出。**  
   位置：[engine.py:1348](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/engine.py:1348)、[__main__.py:249](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/__main__.py:249)。  
   `flatten_all` 读取失败只告警，没有撤销旧 `flatten_status`。复现：保留一小时前的 `program_flat=true/account_flat=true`，本次 shutdown 仓位读取失败，主程序判断仍走 **退出码 0**，并输出旧全平状态。  
   建议：停机必须消费本次核实的明确结果；失败或未执行核实不得复用历史成功状态。

6. **P2｜重基线审计遗漏旧外仓计量基线。**  
   位置：[equity.py:13](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/equity.py:13)、[equity.py:178](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/equity.py:178)。  
   审计 `new` 写入外仓起点及程序浮盈亏基线，但 `old` 不含 `external_equity_anchor/program_daily_upnl/program_last_upnl`。覆盖后无法从本次审计还原旧归属计量状态。  
   建议：将这些旧值纳入同事务审计，覆盖 rebaseline 和 release-halt。

其他重点：未发现登记可隐藏已有程序订单／pair 历史，永久禁单覆盖减仓、别名和重启；未发现管理操作直接清除仓位类锁。原告警死锁已通过共用 Store RLock 消除，本次未发现新的锁序环。USDC **收益贡献**的正数取上界、负数取下界方向正确，但保证仅限 PM 授权估值区间；公开请求复用原限频队列。

验证使用指定 Python 3.10：**122 项专项测试通过**，仅将文件夹具替换为内存夹具、保留原断言；5 项跨进程文件数据库测试未运行。原模拟器三种子各 **2,664 回合**通过，85 个 Python 文件语法检查通过。对照基线逐方法检查 **499 个既有 `test_` 定义**，未发现删除或额外放宽。

**未修改文件、未提交、未调用真实账户；最终 HEAD 未变，工作区干净。**
