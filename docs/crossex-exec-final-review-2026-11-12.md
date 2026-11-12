codex
按 `cde65ed` 和现有 service **原样部署**判断：

- **可部署 LIVE=0：否**，工作目录与计划布局不符。修正部署配置后，可以进行影子验证。
- **可开实盘：否**，发现两项需要修复的执行逻辑问题。
- **无 P0；3 项 P1、2 项 P2。**

本次未修改文件、未提交、未调用真实账户接口。

**问题清单**

1. **P1｜共享复核之后的写库耗时，仍可突破 1 秒盘口年龄。**  
   位置：[execution.py:127](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/execution.py:127)、[engine.py:121](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/engine.py:121)。  
   共享关口先检查年龄并放行，随后两个线程分别提交 `SENT`，完成后直接发送。**内存故障注入已复现：首次 SENT 提交耗时 1.01 秒，两腿仍发送，实际发送时盘口年龄均为 1.01 秒。** SQLite 使用 FULL 同步，不能把提交耗时视为零。  
   建议：把发送前持久化纳入整对协调，持久化完成后再统一检查年龄和硬风控，最后释放两个发送线程；失败整对拒发，避免重新引入各腿独立否决。补充慢 SENT 提交测试。

2. **P1｜第二腿 INTENT 落库失败，会留下运行期无法清理的孤立意图。**  
   位置：[execution.py:55](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/execution.py:55)、[execution.py:224](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/execution.py:224)。  
   两腿分别提交；第二腿失败后异常抛到主循环，第一腿保留 `INTENT`。运行期 `recover(startup=False)` 不处理它。**已复现：零发送、远端两腿均为零，连续五次到期恢复后仍为 HALTING/CLOSING，订单一直是 INTENT。** 重启才能走现有清理路径。  
   建议：两腿意图原子落库；失败回滚，并清理已经确认没有进入发送阶段的孤立意图。不得把 SENT/UNKNOWN 当作未发送清理。补“第二腿准备失败后无需重启即可完成恢复”的测试。

3. **P1｜service 会从错误代码目录加载模块。**  
   位置：[crossex-exec.service:11](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/deploy/crossex-exec.service:11)。  
   当前指向 `/home/ubuntu/wubin/taoli1`，可能加载旧版本或直接找不到模块。  
   建议改为：
   ```ini
   WorkingDirectory=/home/ubuntu/wubin/crossex-exec
   ```
   `ExecStart` 的 `-m tools.crossex_exec`、env 路径，以及第 22 行 `ReadWritePaths=/home/ubuntu/wubin/crossex-exec/var` 均符合新布局，可保留。

4. **P2｜历史数据全量解析，80M 缺乏足够余量证据。**  
   位置：[storage.py:87](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/storage.py:87)、[storage.py:113](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/storage.py:113)、service 第 15 行。  
   `pairs(active=True)` 先加载全部历史再过滤；订单也全量加载。合成 **2000 条 SHADOW 历史、每腿每侧 50 档**时，查询返回零活动仓位，但临时 Python 分配峰值达 **76.05 MiB**，尚未计解释器、其他线程等。这不是 Linux RSS 实测，却说明不能认定 80M 足够。  
   建议：活动仓位在 SQL 层过滤，历史读取分页或维护风险汇总；用实际白名单和 API 查询负载做影子内存验收，再结合 1.9G 宿主机可用余量确定限制。`MemoryMax` 超限可能触发服务内 OOM，不能靠自动重启保证仓位管理连续性。[systemd 官方说明](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html#MemoryMax=bytes)

5. **P2｜README 顶部仍报告第七轮失败。**  
   位置：[README.md:9](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/README.md:9)。  
   仍写“8 项失败、等待改为 0”，与已经批准的 **5bp** 适配及最终通过记录矛盾。交接文档第 3 行也仍以第六轮作为当前状态。  
   建议更新入口状态，历史失败记录明确标为历史。

**第七轮逐项核对**

除上述发送前写库窗口外，阈值、方向和边界实现符合任务书：

| 项目 | 核对结果 |
|---|---|
| 时间 | ≤300ms、每腿年龄≤1.0s，等号放行；未来时间、缺失、NaN/Inf 拒绝 |
| 重拉 | 初审和共享发送复核各最多重拉一次，两腿一起提交；仍失败才计放弃 |
| 资金费 | 按各腿真实周期折算；差值绝对值≤0.5bp/h，每腿折合8h绝对值≤0.05% |
| 常态基准 | 方向基准绝对值≤10bp、MAD≤10bp；反向使用倒数变换 |
| 单所异常 | ≥3家，包含两腿及外部参考；异常幅度≥超额的70%，正常腿偏离≤10bp；买低/卖高方向正确 |
| 配置 | MAX 只能降低，MIN 只能提高；非法和非有限值拒绝 |
| 平仓 | 不经过第七轮开仓门禁；仍记录盘口时间差 |
| 限频 | 重拉复用公共队列及每所2/s、全局8/s；私有请求保留在途额度，未发现绕过路径 |

阈值入口见 [admission.py:48](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/admission.py:48)，整对复核见 [engine.py:92](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/engine.py:92)。

**其他上线安全项**

- **硬上限比较正确**：实测 150/600 等号放行、超限拒绝；权益下降 149.99 不触发，下降 150 进入 HALTING。检查覆盖整对预检和共享发送关口；账户缓存上限10秒，重启保留权益基线。减仓不受额度和账户缓存读取限制。仍需修复问题1的放行后耗时窗口。[limits.py:93](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/limits.py:93)
- **UNKNOWN 不重发**：恢复查询原编号，未确认则撤单、再确认并读取实仓；未找到不被解释为从未受理。[execution.py:215](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/execution.py:215)
- **停开锁与补平**：锁按原因和仓位独立保存；补平串行按实际持仓重算，失败保留锁并退避恢复。主要遗留为问题2。
- **方向四组合正确**：开多 BUY/long、开空 SELL/short、平多 SELL/long、平空 BUY/short。CrossEx 平仓另带 `reduce_only=true`；Bitget hedge 使用 `posSide`，不误用单向模式专属的 `reduceOnly`。[适配器](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/adapters.py:139)、[Bitget 官方说明](https://www.bitget.com/legacy-docs/uta/trade/Place-Order)、[CrossEx 官方说明](https://www.gate.com/docs/developers/crossex/en/#create-order)
- **密钥与数据接口**：未发现原始凭据、签名或远端异常正文进入日志的路径。接口校验唯一 token、常量时间比较，仅接受 GET；SQLite 使用只读连接和 authorizer，接口异常隔离。[api.py:94](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/api.py:94)、[api_data.py:91](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/api_data.py:91)

**部署与 LIVE=0**

归档必须同时包含 `tools/crossex_exec/`、`tools/fill_sim/`；存在直接 import 依赖。**不要求启动 fill-sim 服务**，`FILL_SIM_DB` 只是可选对照数据。预建 `var/` 并确保 ubuntu 可写；env 必须由 ubuntu 所有、权限0600。白名单为空不会生成开仓意图。

LIVE=0 的零交易写请求有明确依据：

- [execution.py:115](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/execution.py:115)：新意图直接变成 SHADOW，在调用适配器前返回。
- [execution.py:205](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/execution.py:205)：影子模式撤单直接返回。
- [transport.py:67](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/transport.py:67)：非 GET 请求在建连、发送前被拒绝。

因此修正部署配置后可先跑影子；Python 3.10.12 实机、systemd 权限及24小时资源验收仍未完成。

**BITGET_RISK_RATE_CONFIRMED：就字段口径而言，可以置 1。**

官方 `GET /api/v3/account/assets` 明确：`effEquity` 是可作为全仓保证金的有效权益，单位 USD；账户级 `mmr` 是维持保证金金额，单位 USD。因此 [accounts.py:22](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/accounts.py:22) 的 `effEquity/mmr` 是正确的覆盖倍数，可与3、2比较；`mmr==0` 的处理符合第二轮任务书。[官方字段定义](https://www.bitget.com/legacy-docs/uta/account/Get-Account)

这个倍数不能直接称为网页风险率的倒数：官方全仓风险率还涉及部分清算费用。[官方保证金说明](https://www.bitget.com/support/articles/12560603839176)  
设置1不代表实盘验收通过；若要覆盖 Bitget 的影子意图，也需要该值为1，因为影子复核同样检查它。

**验证证据**

本次只读运行 **22项测试通过**，包含三种子各2448回合、全部不变量及六类拒绝的非空覆盖；写盘/联网尝试均为零。43个Python文件通过3.10语法检查。另复现了上述两项P1。

逐函数比较旧断言：除已批准的40→5外，未发现旧断言删除或放宽。历史完整日志的校验值与交接一致，结果为执行器270项、fill-sim158项通过；本轮没有重跑会创建临时文件的完整套件。
tokens used
236,327
按 `cde65ed` 和现有 service **原样部署**判断：

- **可部署 LIVE=0：否**，工作目录与计划布局不符。修正部署配置后，可以进行影子验证。
- **可开实盘：否**，发现两项需要修复的执行逻辑问题。
- **无 P0；3 项 P1、2 项 P2。**

本次未修改文件、未提交、未调用真实账户接口。

**问题清单**

1. **P1｜共享复核之后的写库耗时，仍可突破 1 秒盘口年龄。**  
   位置：[execution.py:127](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/execution.py:127)、[engine.py:121](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/engine.py:121)。  
   共享关口先检查年龄并放行，随后两个线程分别提交 `SENT`，完成后直接发送。**内存故障注入已复现：首次 SENT 提交耗时 1.01 秒，两腿仍发送，实际发送时盘口年龄均为 1.01 秒。** SQLite 使用 FULL 同步，不能把提交耗时视为零。  
   建议：把发送前持久化纳入整对协调，持久化完成后再统一检查年龄和硬风控，最后释放两个发送线程；失败整对拒发，避免重新引入各腿独立否决。补充慢 SENT 提交测试。

2. **P1｜第二腿 INTENT 落库失败，会留下运行期无法清理的孤立意图。**  
   位置：[execution.py:55](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/execution.py:55)、[execution.py:224](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/execution.py:224)。  
   两腿分别提交；第二腿失败后异常抛到主循环，第一腿保留 `INTENT`。运行期 `recover(startup=False)` 不处理它。**已复现：零发送、远端两腿均为零，连续五次到期恢复后仍为 HALTING/CLOSING，订单一直是 INTENT。** 重启才能走现有清理路径。  
   建议：两腿意图原子落库；失败回滚，并清理已经确认没有进入发送阶段的孤立意图。不得把 SENT/UNKNOWN 当作未发送清理。补“第二腿准备失败后无需重启即可完成恢复”的测试。

3. **P1｜service 会从错误代码目录加载模块。**  
   位置：[crossex-exec.service:11](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/deploy/crossex-exec.service:11)。  
   当前指向 `/home/ubuntu/wubin/taoli1`，可能加载旧版本或直接找不到模块。  
   建议改为：
   ```ini
   WorkingDirectory=/home/ubuntu/wubin/crossex-exec
   ```
   `ExecStart` 的 `-m tools.crossex_exec`、env 路径，以及第 22 行 `ReadWritePaths=/home/ubuntu/wubin/crossex-exec/var` 均符合新布局，可保留。

4. **P2｜历史数据全量解析，80M 缺乏足够余量证据。**  
   位置：[storage.py:87](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/storage.py:87)、[storage.py:113](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/storage.py:113)、service 第 15 行。  
   `pairs(active=True)` 先加载全部历史再过滤；订单也全量加载。合成 **2000 条 SHADOW 历史、每腿每侧 50 档**时，查询返回零活动仓位，但临时 Python 分配峰值达 **76.05 MiB**，尚未计解释器、其他线程等。这不是 Linux RSS 实测，却说明不能认定 80M 足够。  
   建议：活动仓位在 SQL 层过滤，历史读取分页或维护风险汇总；用实际白名单和 API 查询负载做影子内存验收，再结合 1.9G 宿主机可用余量确定限制。`MemoryMax` 超限可能触发服务内 OOM，不能靠自动重启保证仓位管理连续性。[systemd 官方说明](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html#MemoryMax=bytes)

5. **P2｜README 顶部仍报告第七轮失败。**  
   位置：[README.md:9](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/README.md:9)。  
   仍写“8 项失败、等待改为 0”，与已经批准的 **5bp** 适配及最终通过记录矛盾。交接文档第 3 行也仍以第六轮作为当前状态。  
   建议更新入口状态，历史失败记录明确标为历史。

**第七轮逐项核对**

除上述发送前写库窗口外，阈值、方向和边界实现符合任务书：

| 项目 | 核对结果 |
|---|---|
| 时间 | ≤300ms、每腿年龄≤1.0s，等号放行；未来时间、缺失、NaN/Inf 拒绝 |
| 重拉 | 初审和共享发送复核各最多重拉一次，两腿一起提交；仍失败才计放弃 |
| 资金费 | 按各腿真实周期折算；差值绝对值≤0.5bp/h，每腿折合8h绝对值≤0.05% |
| 常态基准 | 方向基准绝对值≤10bp、MAD≤10bp；反向使用倒数变换 |
| 单所异常 | ≥3家，包含两腿及外部参考；异常幅度≥超额的70%，正常腿偏离≤10bp；买低/卖高方向正确 |
| 配置 | MAX 只能降低，MIN 只能提高；非法和非有限值拒绝 |
| 平仓 | 不经过第七轮开仓门禁；仍记录盘口时间差 |
| 限频 | 重拉复用公共队列及每所2/s、全局8/s；私有请求保留在途额度，未发现绕过路径 |

阈值入口见 [admission.py:48](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/admission.py:48)，整对复核见 [engine.py:92](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/engine.py:92)。

**其他上线安全项**

- **硬上限比较正确**：实测 150/600 等号放行、超限拒绝；权益下降 149.99 不触发，下降 150 进入 HALTING。检查覆盖整对预检和共享发送关口；账户缓存上限10秒，重启保留权益基线。减仓不受额度和账户缓存读取限制。仍需修复问题1的放行后耗时窗口。[limits.py:93](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/limits.py:93)
- **UNKNOWN 不重发**：恢复查询原编号，未确认则撤单、再确认并读取实仓；未找到不被解释为从未受理。[execution.py:215](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/execution.py:215)
- **停开锁与补平**：锁按原因和仓位独立保存；补平串行按实际持仓重算，失败保留锁并退避恢复。主要遗留为问题2。
- **方向四组合正确**：开多 BUY/long、开空 SELL/short、平多 SELL/long、平空 BUY/short。CrossEx 平仓另带 `reduce_only=true`；Bitget hedge 使用 `posSide`，不误用单向模式专属的 `reduceOnly`。[适配器](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/adapters.py:139)、[Bitget 官方说明](https://www.bitget.com/legacy-docs/uta/trade/Place-Order)、[CrossEx 官方说明](https://www.gate.com/docs/developers/crossex/en/#create-order)
- **密钥与数据接口**：未发现原始凭据、签名或远端异常正文进入日志的路径。接口校验唯一 token、常量时间比较，仅接受 GET；SQLite 使用只读连接和 authorizer，接口异常隔离。[api.py:94](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/api.py:94)、[api_data.py:91](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/api_data.py:91)

**部署与 LIVE=0**

归档必须同时包含 `tools/crossex_exec/`、`tools/fill_sim/`；存在直接 import 依赖。**不要求启动 fill-sim 服务**，`FILL_SIM_DB` 只是可选对照数据。预建 `var/` 并确保 ubuntu 可写；env 必须由 ubuntu 所有、权限0600。白名单为空不会生成开仓意图。

LIVE=0 的零交易写请求有明确依据：

- [execution.py:115](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/execution.py:115)：新意图直接变成 SHADOW，在调用适配器前返回。
- [execution.py:205](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/execution.py:205)：影子模式撤单直接返回。
- [transport.py:67](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/transport.py:67)：非 GET 请求在建连、发送前被拒绝。

因此修正部署配置后可先跑影子；Python 3.10.12 实机、systemd 权限及24小时资源验收仍未完成。

**BITGET_RISK_RATE_CONFIRMED：就字段口径而言，可以置 1。**

官方 `GET /api/v3/account/assets` 明确：`effEquity` 是可作为全仓保证金的有效权益，单位 USD；账户级 `mmr` 是维持保证金金额，单位 USD。因此 [accounts.py:22](C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec/tools/crossex_exec/accounts.py:22) 的 `effEquity/mmr` 是正确的覆盖倍数，可与3、2比较；`mmr==0` 的处理符合第二轮任务书。[官方字段定义](https://www.bitget.com/legacy-docs/uta/account/Get-Account)

这个倍数不能直接称为网页风险率的倒数：官方全仓风险率还涉及部分清算费用。[官方保证金说明](https://www.bitget.com/support/articles/12560603839176)  
设置1不代表实盘验收通过；若要覆盖 Bitget 的影子意图，也需要该值为1，因为影子复核同样检查它。

**验证证据**

本次只读运行 **22项测试通过**，包含三种子各2448回合、全部不变量及六类拒绝的非空覆盖；写盘/联网尝试均为零。43个Python文件通过3.10语法检查。另复现了上述两项P1。

逐函数比较旧断言：除已批准的40→5外，未发现旧断言删除或放宽。历史完整日志的校验值与交接一致，结果为执行器270项、fill-sim158项通过；本轮没有重跑会创建临时文件的完整套件。
