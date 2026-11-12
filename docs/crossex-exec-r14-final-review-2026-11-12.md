codex
**可影子部署：是，仅限新数据库、`CROSSEX_LIVE_ENABLED=0` 验证。代码层面可开实盘：否。**

审查范围 `c7654f4..679791a`。**发现 4 项 P1、1 项 P2，无 P0；本次实盘终审不通过。**

1. **P1｜首次缺失的资金费仍可能被当成零，低估程序亏损。**  
   位置：[external_accounting.py:187](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external_accounting.py:187)、[external_accounting.py:217](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external_accounting.py:217)、[external_accounting.py:426](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external_accounting.py:426)。  
   已复现：同交易所、同币种外仓资金费 **+25**、程序资金费 **−25**，两条流水在首次响应中同时缺失，现金余额不变。即使原始仓位累计资金费已变化，仍得到 `verified=true`、程序日亏/累计 **0**、`can_open=True`，`Engine.check_order` 通过。流水出现后才识别为 −25。  
   原修复只能防止**已见流水**消失，不能证明首次返回完整。建议增加逐合约结算累计值或独立资金费证据核对；结算完整性无法确认时停开，不能仅凭整域现金净变化为零放行。

2. **P1｜原始仓位身份矛盾时，仍可获得外仓负债豁免。**  
   位置：[program_risk.py:89](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/program_risk.py:89)、[external.py:151](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external.py:151)。  
   归属主要依赖 symbol，没有核对仓位行的 `exchange_type/business_type/tag`。已复现：登记的 Hyperliquid `io` 合约，原始行却声明 BINANCE/SPOT/xyz，50 负债仍被归为外仓，证明成功且允许开仓；同一行在新接口中却显示 `identity_verified=false、ownership=unregistered`。  
   建议风险归属与展示共用严格身份校验；身份冲突时禁止排除仓位、禁止负债豁免。正常身份下，超额负债、混合仓位域和 Bitget debt 的阻断检查存在。

3. **P1｜HALTING 重启后不启动后台线程的问题仍存在。**  
   位置：[engine.py:557](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/engine.py:557)、[engine.py:1451](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/engine.py:1451)。  
   startup 提前返回，tick 只重试 flatten 后返回。已复现 HALTING＋外仓目录过期：`workers=0`，后续三个 tick 的完整账户读取次数为 **0**；目录无法刷新，flatten 持续无法核实，状态卡在 HALTING。  
   建议 HALTING 保持账户、目录和采样刷新，独立运行减仓重试；开仓继续由状态锁禁止，不能自动清除停机原因。

4. **P1｜一次明确拒单会永久破坏后续程序损益证明。**  
   位置：[program_risk.py:28](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/program_risk.py:28)、[execution.py:231](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/execution.py:231)。  
   明确拒单保存为 `REJECTED、filled=0、accounting_pending=false`，没有 fee；新统计却要求每条 REJECTED 都有 fee。该状态不会进入记账恢复。已复现拒单后证明持续失败，执行 recover 和 rebaseline 后仍失败，与拒单率阈值无关。  
   建议对已确认零成交的明确拒单保存完整零成交记账证据，并为已有此类记录提供受校验的恢复路径；未知订单不能套用此处理。

5. **P2｜旧回归切换到兼容模式，削弱了默认程序模式的覆盖。**  
   位置：[test_engine.py:104](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/tests/test_engine.py:104)、[test_invariants.py:293](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/tests/test_invariants.py:293)。  
   多组旧测试及模拟器主体改用 `dedicated_account`，因此通过结果不能证明默认 `program` 模式具有同等故障恢复能力，上述拒单问题就是缺口。建议保留旧回归，同时让默认模式执行真实开平仓、明确拒单、流水延迟及重启场景。

其他重点的结论：

- **上次六项按原触发场景已修复**：快照版本绑定、已见流水连续性、非 USD 名义额折算、Bitget debt、停机仅消费本次核实结果、审计 old 补齐。600 的外仓排除按第十四轮授权改变。
- 单腿 **150**、程序总额 **600**、日亏 **20**、累计亏损 **100**、回撤 **150** 的发送前检查仍在；但第 1 项会向这些检查提供低估的损益，不能据此判定硬限额可靠。
- 默认模式下，**外仓浮亏本身不再按累计亏损触发 HALTING**。外仓拉低整户保证金仍会触发程序减仓；程序因此实现的自身亏损达到阈值，仍应停机。旧 HALTING 不会自动解除。
- 减仓遍历程序 pair，保留外仓永久禁单；失败保留退出义务并退避重试。HALTING 的恢复仍受第 3 项影响。
- `remote-positions` 使用只读事务及字段白名单，未发现账户秘密泄露或写入路径。未发现新增锁序环。
- 逐项检查旧测试差异，未发现删除原定义或额外放宽金额阈值；业务断言变化符合所列 PM 口径，覆盖问题见第 5 项。

验证：指定 Python 3.10 下 **120 项相关测试通过**，文件夹具替换为内存 SQLite、原断言不变；原模拟器三种子各 **2,712 回合通过**，93 个 Python 文件语法检查通过。文件持久化、跨进程及真实账户行为未在本次复验。

**未修改文件、未提交、未调用真实账户；最终 HEAD 未变，工作区干净。**
tokens used
198,720
**可影子部署：是，仅限新数据库、`CROSSEX_LIVE_ENABLED=0` 验证。代码层面可开实盘：否。**

审查范围 `c7654f4..679791a`。**发现 4 项 P1、1 项 P2，无 P0；本次实盘终审不通过。**

1. **P1｜首次缺失的资金费仍可能被当成零，低估程序亏损。**  
   位置：[external_accounting.py:187](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external_accounting.py:187)、[external_accounting.py:217](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external_accounting.py:217)、[external_accounting.py:426](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external_accounting.py:426)。  
   已复现：同交易所、同币种外仓资金费 **+25**、程序资金费 **−25**，两条流水在首次响应中同时缺失，现金余额不变。即使原始仓位累计资金费已变化，仍得到 `verified=true`、程序日亏/累计 **0**、`can_open=True`，`Engine.check_order` 通过。流水出现后才识别为 −25。  
   原修复只能防止**已见流水**消失，不能证明首次返回完整。建议增加逐合约结算累计值或独立资金费证据核对；结算完整性无法确认时停开，不能仅凭整域现金净变化为零放行。

2. **P1｜原始仓位身份矛盾时，仍可获得外仓负债豁免。**  
   位置：[program_risk.py:89](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/program_risk.py:89)、[external.py:151](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external.py:151)。  
   归属主要依赖 symbol，没有核对仓位行的 `exchange_type/business_type/tag`。已复现：登记的 Hyperliquid `io` 合约，原始行却声明 BINANCE/SPOT/xyz，50 负债仍被归为外仓，证明成功且允许开仓；同一行在新接口中却显示 `identity_verified=false、ownership=unregistered`。  
   建议风险归属与展示共用严格身份校验；身份冲突时禁止排除仓位、禁止负债豁免。正常身份下，超额负债、混合仓位域和 Bitget debt 的阻断检查存在。

3. **P1｜HALTING 重启后不启动后台线程的问题仍存在。**  
   位置：[engine.py:557](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/engine.py:557)、[engine.py:1451](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/engine.py:1451)。  
   startup 提前返回，tick 只重试 flatten 后返回。已复现 HALTING＋外仓目录过期：`workers=0`，后续三个 tick 的完整账户读取次数为 **0**；目录无法刷新，flatten 持续无法核实，状态卡在 HALTING。  
   建议 HALTING 保持账户、目录和采样刷新，独立运行减仓重试；开仓继续由状态锁禁止，不能自动清除停机原因。

4. **P1｜一次明确拒单会永久破坏后续程序损益证明。**  
   位置：[program_risk.py:28](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/program_risk.py:28)、[execution.py:231](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/execution.py:231)。  
   明确拒单保存为 `REJECTED、filled=0、accounting_pending=false`，没有 fee；新统计却要求每条 REJECTED 都有 fee。该状态不会进入记账恢复。已复现拒单后证明持续失败，执行 recover 和 rebaseline 后仍失败，与拒单率阈值无关。  
   建议对已确认零成交的明确拒单保存完整零成交记账证据，并为已有此类记录提供受校验的恢复路径；未知订单不能套用此处理。

5. **P2｜旧回归切换到兼容模式，削弱了默认程序模式的覆盖。**  
   位置：[test_engine.py:104](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/tests/test_engine.py:104)、[test_invariants.py:293](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/tests/test_invariants.py:293)。  
   多组旧测试及模拟器主体改用 `dedicated_account`，因此通过结果不能证明默认 `program` 模式具有同等故障恢复能力，上述拒单问题就是缺口。建议保留旧回归，同时让默认模式执行真实开平仓、明确拒单、流水延迟及重启场景。

其他重点的结论：

- **上次六项按原触发场景已修复**：快照版本绑定、已见流水连续性、非 USD 名义额折算、Bitget debt、停机仅消费本次核实结果、审计 old 补齐。600 的外仓排除按第十四轮授权改变。
- 单腿 **150**、程序总额 **600**、日亏 **20**、累计亏损 **100**、回撤 **150** 的发送前检查仍在；但第 1 项会向这些检查提供低估的损益，不能据此判定硬限额可靠。
- 默认模式下，**外仓浮亏本身不再按累计亏损触发 HALTING**。外仓拉低整户保证金仍会触发程序减仓；程序因此实现的自身亏损达到阈值，仍应停机。旧 HALTING 不会自动解除。
- 减仓遍历程序 pair，保留外仓永久禁单；失败保留退出义务并退避重试。HALTING 的恢复仍受第 3 项影响。
- `remote-positions` 使用只读事务及字段白名单，未发现账户秘密泄露或写入路径。未发现新增锁序环。
- 逐项检查旧测试差异，未发现删除原定义或额外放宽金额阈值；业务断言变化符合所列 PM 口径，覆盖问题见第 5 项。

验证：指定 Python 3.10 下 **120 项相关测试通过**，文件夹具替换为内存 SQLite、原断言不变；原模拟器三种子各 **2,712 回合通过**，93 个 Python 文件语法检查通过。文件持久化、跨进程及真实账户行为未在本次复验。

**未修改文件、未提交、未调用真实账户；最终 HEAD 未变，工作区干净。**
