# 大价差提醒消息精简交接

## 目标、分支与范围

- 任务书：`docs/big-spread-compact-spec-2026-11-13.md`，已完整阅读。
- 模块：价差与资金费率告警，仅修改 `rule_id=big_spread` 的消息呈现及对应事件 message。
- 工作树：`.worktrees/announce-filter`；分支：`codex/big-spread-compact`。
- 基线：`477f86642bafdac73f24a2e83d0b9998448cb3ed`。
- 用户明确要求本地 commit，不 push、不部署、不运行 WSL。本次遵守这些限制。

## 已完成功能与代码入口

- `backend/app/services/alert_messages.py`：独立大价差精简格式，正常无风险 5 行、有风险 6 行。标题去掉 USDT 后缀，开/平价差保留 2 位小数；按实际 `huge_spread_pct` 判断人工核实提示。
- 第二行展示交易所显示名、中文市场类型、买入 ask 和卖出 bid；价格保留 6 位有效数字并展开科学计数法。HL 保留非 main DEX 和原始市场，价格及合约倍率在有差异时保留于同一行。
- 资金费显示当前费率及周期；持仓净复用 `_hourly_funding_difference(..., next_cycle=False)`，沿用卖腿减买腿的符号含义及小时线性估算。未知费率/周期、现货无适用周期写 `-`，不使用预测费率代替。
- 成交额使用万/亿，最多 2 位小数；不足 1 万直接显示数值，缺失/非有限数写 `-`。
- 风险使用既有中文映射；无风险不显示该提示。任务书同时要求最多 6 行和受限交易提示，因此两者同时存在时共用一行，以分号隔开；仅有交易提示时单独成行。
- `backend/app/services/opportunity_trade_availability.py`：增加默认空元组的 `opening_restricted_legs`，从已有精确市场开仓动作诊断提取受限/未知腿；普通诊断全文及判断逻辑不变。诊断整体失败时两腿均按未知处理，消息提示两腿开仓受限。
- `backend/app/main.py`：诊断及 Astro 处理完成后统一构造精简消息，复用卡片摘要。补建缺失变体时保留已有卡片的开仓状态；未创建原因截断 40 字。不再附加交易与充提全文或 Astro 原始结果。
- 飞书发送和事件 message 使用同一文本；大价差发送失败保留尝试发送的原文，并用事件 status 和错误类型日志记录失败。实时发送关闭/发送前路线屏蔽的审计说明附在卡片行，避免增加行数。普通规则保持原有行为。

## 验证

全部使用 Windows 原生 PowerShell/Python，未运行 WSL。仅后端文本变更，无前端改动，因此未运行前端构建。

任务书指定测试，工作目录 `backend/`（PowerShell 不为原生程序展开通配符，显式展开相同的测试文件集合）：

```powershell
$alertTests = @(Get-ChildItem -Path 'tests/test_alert*.py' -File | Sort-Object Name | ForEach-Object { 'tests/' + $_.Name })
pytest tests/test_big_spread_alert.py @alertTests -q -p no:cacheprovider --basetemp ../.tmp-bscompact
```

最终结果：

```text
242 passed in 15.74s
```

补充诊断字段验证，工作目录 `backend/`：

```powershell
pytest tests/test_opportunity_trade_availability.py -q -p no:cacheprovider --basetemp ../.tmp-bscompact-diagnostics
```

```text
12 passed in 0.77s
```

静态检查，工作目录仓库根目录：

```powershell
ruff check backend/app/main.py backend/app/services/alert_messages.py backend/app/services/opportunity_trade_availability.py backend/tests/test_big_spread_alert.py backend/tests/test_opportunity_trade_availability.py --select E9,F63,F7,F82,F401
git diff --check
```

Ruff 输出 `All checks passed!`，差异检查退出码为 0。

- `backend/tests/test_big_spread_alert.py`：覆盖正常消息全文、数值阈值边界/自定义阈值、有/无/多个风险、单/双腿开仓受限、诊断超时、HL 双侧 DEX/原始标的、万/亿单位、不同价格量级、当前资金费缺失与正负号、现货、卡片原因 40 字截断、补建变体、发送失败及关闭发送时的事件文本。
- `backend/tests/test_opportunity_trade_availability.py`：验证 available/blocked/unknown/conditional 状态只影响对应开仓腿，反方向动作不会错误限制开仓。
- `backend/tests/fixtures/big_spread_compact_ordinary_messages.json`：从 `git show 477f866:<path>` 加载原始 main、alert_messages、opportunity_trade_availability 三个模块，在固定模拟诊断下捕获普通规则 compact/detailed 发送及事件全文。回归测试逐字比较两种格式，均通过。
- 首轮为 `1 failed, 239 passed in 17.60s`：补建变体时已有卡片状态原先只在 Astro 全文中，删除全文后丢失。已把已有状态合入卡片摘要，并补强全文断言；最终复验通过。

## 示例消息全文（构造数据）

```text
【大价差】BARD  开 3.01% / 平 3.11%
OKX 合约 买 0.1282 → Bybit 合约 卖 0.1321
资金费：买 -0.48%/1h · 卖 -2.00%/4h（持仓净 -0.017%/h）
24h 成交额：1.36 亿 / 1.12 亿 U
风险：资金费率逆风
卡片：已有卡片 BARD，当前开仓：关
```

示例买腿原始费率为 -0.4834%/1h，卖腿为 -2%/4h；小时净值先按原始精度计算再显示，避免从展示时四舍五入后的数值反推。示例不是当前实盘行情。

## 线上状态、未跟踪产物与后续

- 本次仅本地提交，未 push、未部署、未备份或验证生产；生产版本未因本次工作改变。
- 未向真实飞书或 Astro 发送网络写入；测试使用模拟客户端和既有本地测试依赖。
- 开始时已存在的未跟踪任务书 `docs/big-spread-compact-spec-2026-11-13.md` 保持原样，不纳入提交。
- 本地 `.tmp-bscompact/` 为本次 pytest 产物，保留且不暂存。`output/big-spread-compact-example.txt` 是被忽略的本地示例文本。
- 未修改告警资格、建卡权限、冷却、订单簿校验及数据库结构。
- 本模块本地验收已完成。后续如需推送/部署或处理其他模块，请另开任务，并引用本交接；发布需另行授权及生产验收。
