codex
**可影子部署：是（仅关闭实盘开关的独立影子环境）。代码层面可开实盘：否。**

审查范围 `65747b2..c85ff7c`。**无 P0，发现 1 项 P1，无新增 P2。**

1. **P1｜跨日相反资金费首次同时漏报，仍会低估当天亏损并允许开仓。**

   位置：[funding.py:105](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/funding.py:105)、[external_accounting.py:394](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external_accounting.py:394)、[external_accounting.py:430](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external_accounting.py:430)。

   **触发场景：** 同一程序合约上一 UTC 日收到 `+25`，当日支付 `−25`，期间没有成功采样，两条资金费流水首次都缺失。生命周期累计资金费、现金余额净变化均为零，因此新增核验通过；但累计净额相等不能证明每日流水完整。

   **已复现：** 使用相隔八小时的结算时点，重启后仍得到 `verified=true、funding_verified=true、program_daily=0、can_open=True`，`Engine.check_order` 通过。完整流水对照组正确得到当天 `−25`，触发日亏 **20** 停开锁。漏报组直到流水补报后才停止放行。

   **修复建议：** 增加可证明 UTC 分日归属的独立结算证据，例如逐结算事件完整性或日界累计值；无法证明跨日区间完整时保持停开，不能仅凭生命周期净变化为零认证、滚动起点。补充跨日、重启及迟到流水回归。

其余复核结果：

- **上轮五项：** 原“不同合约首次漏报抵消”场景已修，但资金费完整性仍受上述 P1 阻断；身份冲突不豁免、HALTING 后台持续运行且保留停机原因、明确拒单零记账及受校验恢复、默认 `program` 执行覆盖均通过本次检查。
- **HL 空上限：** 放宽限定在外仓登记目录；其他身份、唯一性、新鲜度和有效性校验保留。本程序交易映射对 `max_limit_size=null` 实测拒绝，没有解释为无限大。
- **历史接口：** 新增调用均为 GET；未发现新增交易写路径或凭据泄漏，历史账户标识未进入所检查的只读展示接口。真实权限和结算语义仍需影子验证；Bitget 官方文档对该 GET 标注了交易读写权限要求。[接口文档](https://www.bitget.com/docs/catalog/trading/position-management)
- **全局不变量：** 开仓 150/600 额度检查、UNKNOWN 不重发、两腿共享屏障、外仓永久禁单保留，未发现新增锁序环。减仓沿用原契约的额度豁免；**程序日亏不被低估这一项不成立。**
- **测试未放宽：** 核对原 46 个测试/夹具文件，2,491 个断言、1,212 个类/函数入口均保留。

验证：指定 Python 3.10 下 **141 项相关测试通过**；旧模拟器三种子各 2,712 回合，默认程序模拟器三种子各 12 场景通过，100 个 Python 文件语法检查通过。数据库夹具替换为内存实现；未复验文件持久化、跨进程，一项写 STOP 文件的用例未执行。

**未修改文件、未提交、未调用真实账户；HEAD 未变，工作区干净。**


tokens used
191,098
**可影子部署：是（仅关闭实盘开关的独立影子环境）。代码层面可开实盘：否。**

审查范围 `65747b2..c85ff7c`。**无 P0，发现 1 项 P1，无新增 P2。**

1. **P1｜跨日相反资金费首次同时漏报，仍会低估当天亏损并允许开仓。**

   位置：[funding.py:105](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/funding.py:105)、[external_accounting.py:394](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external_accounting.py:394)、[external_accounting.py:430](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external_accounting.py:430)。

   **触发场景：** 同一程序合约上一 UTC 日收到 `+25`，当日支付 `−25`，期间没有成功采样，两条资金费流水首次都缺失。生命周期累计资金费、现金余额净变化均为零，因此新增核验通过；但累计净额相等不能证明每日流水完整。

   **已复现：** 使用相隔八小时的结算时点，重启后仍得到 `verified=true、funding_verified=true、program_daily=0、can_open=True`，`Engine.check_order` 通过。完整流水对照组正确得到当天 `−25`，触发日亏 **20** 停开锁。漏报组直到流水补报后才停止放行。

   **修复建议：** 增加可证明 UTC 分日归属的独立结算证据，例如逐结算事件完整性或日界累计值；无法证明跨日区间完整时保持停开，不能仅凭生命周期净变化为零认证、滚动起点。补充跨日、重启及迟到流水回归。

其余复核结果：

- **上轮五项：** 原“不同合约首次漏报抵消”场景已修，但资金费完整性仍受上述 P1 阻断；身份冲突不豁免、HALTING 后台持续运行且保留停机原因、明确拒单零记账及受校验恢复、默认 `program` 执行覆盖均通过本次检查。
- **HL 空上限：** 放宽限定在外仓登记目录；其他身份、唯一性、新鲜度和有效性校验保留。本程序交易映射对 `max_limit_size=null` 实测拒绝，没有解释为无限大。
- **历史接口：** 新增调用均为 GET；未发现新增交易写路径或凭据泄漏，历史账户标识未进入所检查的只读展示接口。真实权限和结算语义仍需影子验证；Bitget 官方文档对该 GET 标注了交易读写权限要求。[接口文档](https://www.bitget.com/docs/catalog/trading/position-management)
- **全局不变量：** 开仓 150/600 额度检查、UNKNOWN 不重发、两腿共享屏障、外仓永久禁单保留，未发现新增锁序环。减仓沿用原契约的额度豁免；**程序日亏不被低估这一项不成立。**
- **测试未放宽：** 核对原 46 个测试/夹具文件，2,491 个断言、1,212 个类/函数入口均保留。

验证：指定 Python 3.10 下 **141 项相关测试通过**；旧模拟器三种子各 2,712 回合，默认程序模拟器三种子各 12 场景通过，100 个 Python 文件语法检查通过。数据库夹具替换为内存实现；未复验文件持久化、跨进程，一项写 STOP 文件的用例未执行。

**未修改文件、未提交、未调用真实账户；HEAD 未变，工作区干净。**


