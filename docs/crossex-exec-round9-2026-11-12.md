# crossex-exec 第九轮（待第八轮合并后执行）：CPU、停机解除、划转后重设基线（2026-11-12）

## 背景
- 11-12 影子运行（abf8dba）：单进程持续占用约 80–87% 单核（宿主机 2 核）。py-spy 20s：refresh_baselines → baseline.status → storage.set_meta → encode → security.clean 约占 20%+ 样本，即每轮把全部交易对的基准状态/最新报价整个 JSON 化并递归脱敏后写 meta。
- 11-12 09:46 用户在 Bitget 与 CrossEx 之间划转资金（Bitget 1514→517，CrossEx 753→29753），重启时触发 account_equity_drop → HALTED。程序没有人工解除或重设基线的命令，只能挪开数据库重来（已挪到 backups/db-halted-20261112-0946）。

## 要做的
1. **CPU 预算**：基准状态与报价不再整块写 meta；增量写 SQLite 表、只在变化时写；脱敏只作用于含凭据风险的字段（行情/基准数据不需要递归扫描）。目标：187 币 + 动态采样规模下，影子稳态单进程 ≤15% 单核（给出 Linux/py-spy 或本机等价测量方法），不得降低采样频率以外的任何风控。
2. **人工解除 HALTED**：CLI `python -m tools.crossex_exec release-halt --env-file ... --confirm <当前停机原因>`。前提：本地与两边远端均无持仓、无非终态/待记账订单；重新读取两边权益作为新基线；写审计事件（旧/新基线、操作时间、原因）。条件不满足拒绝并说明。不允许解除 position_mismatch/unhedged 等仓位类锁（它们按原规则解除）。
3. **划转后重设基线**：CLI `rebaseline --confirm` 与写接口 POST /api/v1/equity/rebaseline（ADMIN 令牌，taoli1 页面按钮，二次确认）。仅在无持仓、无非终态订单时允许；审计记录；运行中生效。实盘期间建议划转前先置 STOP 文件。
4. 测试：每条前提不满足各一组；审计内容；重启后基线保留；与第八轮参数/令牌一致。
5. **复审 P2（11-12）**：两腿 `sent_ms` 在共享屏障内批量生成，实际发送间隔被掩盖（一腿延迟 250ms 报 0ms）。在每腿请求写出前采集内存时间戳，随 ACK/UNKNOWN 持久化，观测接口用它；不得在最终放行与发送之间新增同步写库。
