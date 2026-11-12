codex
**可影子部署：是（实盘开关关闭的独立影子环境）。代码层面可开实盘：否。**

审查范围：`b61662d..f559f95`。**无 P0，发现 1 项 P1，无新增 P2。**

1. **P1｜旧版本错误滚动的起点，会被自动认证为有效分日检查点。**

   位置：[funding.py:146](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/funding.py:146)、[funding.py:169](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/funding.py:169)、[external_accounting.py:469](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external_accounting.py:469)。

   **触发场景：** r15 遇到同合约前日 `+25`、当日 `−25` 首次同时漏报，错误核验成功并将 `external_equity_anchor` 滚到当天。升级或恢复该旧库后，没有 `funding_checkpoint`，新代码直接回退到这个未经分日认证的起点。累计增量为零、收取时间早于起点，相关检查被跳过，随后写入 `funding_daily_verified=true`。

   **已复现：** 用 `b61662d` 的真实核算代码生成旧状态，再由 HEAD 的实际 `startup/reconcile` 接续，得到：

   ```text
   verified=true
   funding_daily_verified=true
   program_daily=0.000       # 实际应为 -25
   risk=RUN
   can_open=True
   Engine.check_order 通过
   ```

   **修复建议：** 为起点保存分日证明版本和来源；缺失检查点时，只能回退到已完成分日认证的起点。旧净额起点不得自动升级为新证明，无法补足证据应持续停开。补充“r15 已错误滚日起点→升级／备份恢复”的回归测试。

其余复核结果：

- **原反例直接运行于 r16 已阻断**；重启、迟到流水、检查点事务失败，以及正常 r16 起点下的检查点丢失／回滚探针均保持安全。上述旧库迁移路径仍使修复不完整。
- **分页：** 短页带游标继续读取；重复 ID／游标、缺游标、预算耗尽等拒绝认证。未发现重复计数、无界循环或绕过原限频。
- **停机：** 已登记外仓可保留并退出 0；未登记仓位、程序残仓、未决订单及待写义务仍阻止成功退出。
- **全局：** 150／600 开仓上限、UNKNOWN 不重发、两腿共享屏障、外仓永久禁单保留；未发现新增锁序环。程序亏损不被低估仍受上述 P1 阻断。
- **测试未放宽：** AST 核对原 51 个测试／夹具文件，2,631 个断言、1,281 个类／函数入口均保留。

指定 Python 3.10 下，只读内存适配的 **222 项相关测试通过**，另 8 项边界探针通过；两套三种子模拟器通过，103 个 Python 文件语法检查通过。未复验文件持久化、跨进程或真实账户；写 STOP 文件的用例未运行。

**未修改文件、未提交；HEAD 未变，工作区干净。**
tokens used
176,202
**可影子部署：是（实盘开关关闭的独立影子环境）。代码层面可开实盘：否。**

审查范围：`b61662d..f559f95`。**无 P0，发现 1 项 P1，无新增 P2。**

1. **P1｜旧版本错误滚动的起点，会被自动认证为有效分日检查点。**

   位置：[funding.py:146](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/funding.py:146)、[funding.py:169](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/funding.py:169)、[external_accounting.py:469](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external_accounting.py:469)。

   **触发场景：** r15 遇到同合约前日 `+25`、当日 `−25` 首次同时漏报，错误核验成功并将 `external_equity_anchor` 滚到当天。升级或恢复该旧库后，没有 `funding_checkpoint`，新代码直接回退到这个未经分日认证的起点。累计增量为零、收取时间早于起点，相关检查被跳过，随后写入 `funding_daily_verified=true`。

   **已复现：** 用 `b61662d` 的真实核算代码生成旧状态，再由 HEAD 的实际 `startup/reconcile` 接续，得到：

   ```text
   verified=true
   funding_daily_verified=true
   program_daily=0.000       # 实际应为 -25
   risk=RUN
   can_open=True
   Engine.check_order 通过
   ```

   **修复建议：** 为起点保存分日证明版本和来源；缺失检查点时，只能回退到已完成分日认证的起点。旧净额起点不得自动升级为新证明，无法补足证据应持续停开。补充“r15 已错误滚日起点→升级／备份恢复”的回归测试。

其余复核结果：

- **原反例直接运行于 r16 已阻断**；重启、迟到流水、检查点事务失败，以及正常 r16 起点下的检查点丢失／回滚探针均保持安全。上述旧库迁移路径仍使修复不完整。
- **分页：** 短页带游标继续读取；重复 ID／游标、缺游标、预算耗尽等拒绝认证。未发现重复计数、无界循环或绕过原限频。
- **停机：** 已登记外仓可保留并退出 0；未登记仓位、程序残仓、未决订单及待写义务仍阻止成功退出。
- **全局：** 150／600 开仓上限、UNKNOWN 不重发、两腿共享屏障、外仓永久禁单保留；未发现新增锁序环。程序亏损不被低估仍受上述 P1 阻断。
- **测试未放宽：** AST 核对原 51 个测试／夹具文件，2,631 个断言、1,281 个类／函数入口均保留。

指定 Python 3.10 下，只读内存适配的 **222 项相关测试通过**，另 8 项边界探针通过；两套三种子模拟器通过，103 个 Python 文件语法检查通过。未复验文件持久化、跨进程或真实账户；写 STOP 文件的用例未运行。

**未修改文件、未提交；HEAD 未变，工作区干净。**
