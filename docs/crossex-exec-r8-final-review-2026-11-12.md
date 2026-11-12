codex
**可影子部署：是，仅限 `LIVE=0` 验证部署。代码层面可开实盘：否。**

审查版本 `33933b145e86`，范围 `cde65ed..HEAD`。**无 P0；发现 3 项 P1、1 项 P2。**

1. **P1｜告警锁与数据库锁仍可死锁。**  
   位置：[execution.py:118](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/execution.py:118)、[engine.py:63](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/engine.py:63)。  
   权益触发熔断时，账户刷新线程持有 `_alert_lock` 等待 Store 锁；发送关口持有 Store 锁，调用权益检查后等待 `_alert_lock`。**内存并发复现得到两线程互相等待的堆栈**，可阻塞协调器及后续全平。`8e95ea7` 未覆盖这组锁序。  
   建议：统一 Store、告警及 StopLocks 的嵌套锁顺序，补账户刷新与发送屏障交错的回归测试。

2. **P1｜SENT 提交后没有复查成交额时效。**  
   位置：[engine.py:161](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/engine.py:161)。  
   `check_release()` 检查配置、映射、定量和盘口，但遗漏成交额。**复现：缓存初始年龄 299.75 秒，SENT 提交耗时 0.5 秒，最终年龄 300.25 秒，两腿仍发送并进入 OPEN；同刻成交额检查返回 `volume_unknown`。**  
   建议：提交后以冻结配置复查两腿成交额、时效及所数；最终关口只读缓存，失效则整对拒发，不在这里联网刷新。

3. **P1｜OKX 盘前标志被忽略，可绕过预上市排除。**  
   位置：[universe.py:26](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/universe.py:26)、[universe.py:33](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/universe.py:33)。  
   合成目录输入 `state=live、instCategory=1、ruleType=pre_market`，配合有效 CrossEx 规则，实际解析为 `crypto`，进入候选且通过 `resolve()`。代码未检查 `ruleType`；官方明确它表示盘前交易。[OKX 字段说明](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments)  
   建议：优先拒绝 `pre_market`、`rebase_contract`，保留不可交易标志，覆盖候选展示、人工分类和开仓拒绝测试。

4. **P2｜共享 SENT 时间被当作两腿发送时间，间隔指标失真。**  
   位置：[execution.py:126](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/execution.py:126)、[engine.py:702](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/engine.py:702)。  
   两腿 `sent_ms` 在共享提交前生成。**模拟屏障释放后第二腿晚 150ms 发送，记录的 `send_delta_ms` 仍为 0**，会误导影子阶段的执行时序验收。  
   建议：分别记录持久化时间和各腿实际发送时点；实际时间先存内存，随后落库，保持最终放行后无阻塞写库。

其他指定重点未发现额外绕过：硬上限、四约束取最小与向下取整、单腿有效 OI 裁决、双向倍率换算、独立 ADMIN 鉴权、原子 INTENT 清理和 SQL 分页均已核查。OI 保留交接批准的 Bybit 双边口径，OKX 成交额使用保守下界。

验证使用 Python 3.10.20：71 个文件语法检查通过；12 项原样测试通过，包含三种子各 **2,568 回合**；另 **50 项保留原断言、改用内存 SQLite 夹具运行通过**；管理请求完成 **248 个边界检查**。对照 `cde65ed` 及两个合并父提交，未发现测试方法删除或额外放宽。

完整套件未原样重跑：其中临时文件创建被只读钩子阻止，相关测试随后以内存夹具验证。**未修改文件、未提交、未调用真实账户；最终工作区干净。** Linux 资源与持续影子验收仍未完成，影子通过也不能替代上述 P1 修复。
tokens used
242,343
**可影子部署：是，仅限 `LIVE=0` 验证部署。代码层面可开实盘：否。**

审查版本 `33933b145e86`，范围 `cde65ed..HEAD`。**无 P0；发现 3 项 P1、1 项 P2。**

1. **P1｜告警锁与数据库锁仍可死锁。**  
   位置：[execution.py:118](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/execution.py:118)、[engine.py:63](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/engine.py:63)。  
   权益触发熔断时，账户刷新线程持有 `_alert_lock` 等待 Store 锁；发送关口持有 Store 锁，调用权益检查后等待 `_alert_lock`。**内存并发复现得到两线程互相等待的堆栈**，可阻塞协调器及后续全平。`8e95ea7` 未覆盖这组锁序。  
   建议：统一 Store、告警及 StopLocks 的嵌套锁顺序，补账户刷新与发送屏障交错的回归测试。

2. **P1｜SENT 提交后没有复查成交额时效。**  
   位置：[engine.py:161](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/engine.py:161)。  
   `check_release()` 检查配置、映射、定量和盘口，但遗漏成交额。**复现：缓存初始年龄 299.75 秒，SENT 提交耗时 0.5 秒，最终年龄 300.25 秒，两腿仍发送并进入 OPEN；同刻成交额检查返回 `volume_unknown`。**  
   建议：提交后以冻结配置复查两腿成交额、时效及所数；最终关口只读缓存，失效则整对拒发，不在这里联网刷新。

3. **P1｜OKX 盘前标志被忽略，可绕过预上市排除。**  
   位置：[universe.py:26](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/universe.py:26)、[universe.py:33](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/universe.py:33)。  
   合成目录输入 `state=live、instCategory=1、ruleType=pre_market`，配合有效 CrossEx 规则，实际解析为 `crypto`，进入候选且通过 `resolve()`。代码未检查 `ruleType`；官方明确它表示盘前交易。[OKX 字段说明](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments)  
   建议：优先拒绝 `pre_market`、`rebase_contract`，保留不可交易标志，覆盖候选展示、人工分类和开仓拒绝测试。

4. **P2｜共享 SENT 时间被当作两腿发送时间，间隔指标失真。**  
   位置：[execution.py:126](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/execution.py:126)、[engine.py:702](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/engine.py:702)。  
   两腿 `sent_ms` 在共享提交前生成。**模拟屏障释放后第二腿晚 150ms 发送，记录的 `send_delta_ms` 仍为 0**，会误导影子阶段的执行时序验收。  
   建议：分别记录持久化时间和各腿实际发送时点；实际时间先存内存，随后落库，保持最终放行后无阻塞写库。

其他指定重点未发现额外绕过：硬上限、四约束取最小与向下取整、单腿有效 OI 裁决、双向倍率换算、独立 ADMIN 鉴权、原子 INTENT 清理和 SQL 分页均已核查。OI 保留交接批准的 Bybit 双边口径，OKX 成交额使用保守下界。

验证使用 Python 3.10.20：71 个文件语法检查通过；12 项原样测试通过，包含三种子各 **2,568 回合**；另 **50 项保留原断言、改用内存 SQLite 夹具运行通过**；管理请求完成 **248 个边界检查**。对照 `cde65ed` 及两个合并父提交，未发现测试方法删除或额外放宽。

完整套件未原样重跑：其中临时文件创建被只读钩子阻止，相关测试随后以内存夹具验证。**未修改文件、未提交、未调用真实账户；最终工作区干净。** Linux 资源与持续影子验收仍未完成，影子通过也不能替代上述 P1 修复。
