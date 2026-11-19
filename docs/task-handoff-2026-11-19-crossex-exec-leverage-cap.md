# CrossEx 开仓复核的合约杠杆上限

分支为 `codex/crossex-exec-r8`，基线为 `cb58ee6090561be9e5302a6d5df56aad44063a23`。本次修复针对开仓复核需要调整杠杆的 CrossEx 合约。任务记录显示，部分目录默认杠杆为 2 的合约拒绝设置为 3，返回 HTTP 422。

## 目录与开仓复核

`mapped_instrument` 从精确匹配的 CrossEx 规则行读取 `default_leverage`，用项目的 `dec` 解析。缺失、无效、非有限或非正值均视为未知。有效值随合约目录保存和恢复。未知值省略该字段，兼容旧目录及 Bitget 的序列化格式。 [规则匹配与合约模型](../tools/crossex_exec/policy.py) · [目录发布](../tools/crossex_exec/market.py)

开仓复核从同一份目录选取两腿，将选定的合约传给账户检查。需要设置杠杆时，CrossEx 默认值为整数 1 或 2 就发送对应的整数字符串，其余情况发送 `"3"`。Bitget 仍发送 `"3"` 和 `marginMode=crossed`。`LEVERAGE_SET_ATTEMPT` 和 `LEVERAGE_SET` 的 `target.leverage` 均记录实际发送值。 [开仓入口](../tools/crossex_exec/engine.py) · [目标选择、发送与审计](../tools/crossex_exec/accounts.py)

两端的读回验收区间均为闭区间 [1, 3]。现有的发送前审计、持仓和挂单检查、两腿共享的 5 秒时限、每小时 30 次上限和冷却规则继续生效。写请求返回 422 时只进入单合约失败冷却。

## 本地验收与交付

在 Windows PowerShell 和 Python 3.12.10 下运行完整执行器测试。新增用例覆盖从 5 倍调整到 2 倍、1 倍及回退目标，核对请求、审计和读回，也覆盖目录在复核中途刷新、Bitget 目标及旧目录往返。 [回归测试](../tools/crossex_exec/tests/test_auto_leverage.py)

```powershell
$env:PYTHONPATH = 'tools/crossex_exec/tests'
python -m pytest tools/crossex_exec/tests -q -p no:cacheprovider
```

```text
1097 passed in 316.29s (0:05:16)
```

交付范围为本地提交。开始时工作树干净，没有未跟踪文件。线上效果待部署后核验。后续若安排上线，可在单独的部署任务中核对自然触发的 2 倍合约的审计目标、读回值和 422 记录。
