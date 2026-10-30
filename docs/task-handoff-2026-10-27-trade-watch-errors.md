# 交易可用性观察项报错降噪交接（2026-10-27）

## 目标、范围与基线

- 依据：`docs/trade-watch-error-noise-spec-2026-10-27.md` §3、§4。
- 模块：交易可用性观察项轮询错误日志；验收覆盖日志去重、诊断保留、恢复日志与原有恢复行为。
- 工作区：`.worktrees/trade-watch-errors`；分支：`codex/trade-watch-errors`。
- 开始时 HEAD：`88aea559cf3de63d10914d80d0ca0f8224dacb61`，工作区干净。它是在任务书基线 `f7f56a9` 上增加任务书的文档提交，业务代码基线相同。
- 本次仅交付三个文件：
  - `backend/app/services/trade_availability.py`：仅 `TradeAvailabilityMonitor.check_once` 的匹配失败分支及异常处理结构。
  - `backend/tests/test_trade_availability_monitor_errors.py`：新增离线测试。
  - 本交接文档。
- 用户明确要求不联网、不调用 WSL、不 push；本任务只创建一个本地提交，不部署。

## 已实现行为与代码入口

- `TradeAvailabilityMonitor.check_once` 找不到精确市场时，仍抛出 `RuntimeError`。`result.errors` 非空则取按键排序的第一条诊断值，保留前 200 个字符，拼接 `；诊断：`；空字典保留原文案。
- 使用 `min(result.errors)` 取得排序后的首个键；不会修改诊断字典。
- 异常处理先生成与原实现相同格式的 `异常类名: 异常文本`。与该观察项持久化的 `last_error` 不同才调用 `logger.exception`；相同则调用 `logger.debug`，不传 `exc_info`。
- 每轮失败仍在原位置执行一次 `upsert`，更新 `last_checked_at`、`last_error`、`updated_at`；去重不跳过数据库更新，不引入内存缓存。
- 在 `try/except` 的 `else` 中，对原先 `last_error is not None` 且本轮完整成功的观察项记录一条带观察项 ID 的 INFO“已恢复”。重复成功不再记录；成功后再次失败会重新记录 ERROR traceback。
- 原成功主体、动作状态更新、事件、通知及转账查询逻辑均未改动。Hyperliquid DEX、原始市场匹配、轮询间隔和观察项启停规则均未改动。

## RED / GREEN 与回归结果

环境：Windows PowerShell、Python 3.12、pytest 8.4.2。所有 pytest 命令均在 `backend` 目录运行，均显式设置工作区内 `--basetemp=../.pytest-tmp/<子目录>`；未联网、未安装依赖。

新测试使用 fake status service、内存仓库、可控时间和 `caplog`，共 12 个参数化用例，覆盖：连续三次同错与逐轮持久化、重建 monitor 后仍能去重、错误原因及异常类型改变、恢复与再次失败、原恢复事件/通知/转账查询、诊断按键排序、空诊断字典、空诊断值、199/200/超长中文字符，以及观察项之间互不抑制。

先新增测试，未改业务代码即运行 RED；实现后先运行指定回归与新测试，再运行后端全量。静态检查建议将 `sorted(errors)[0]` 等价改成 `min(errors)` 后，又按相同顺序复验最终版本。

| 阶段 | 结果 | 本地日志 |
| --- | --- | --- |
| RED：§4.6 三个回归文件 + 新文件 | 53 passed、11 failed；原有 52 项全通过，新文件 1 通过、11 预期失败 | `.pytest-tmp/trade-watch-errors-red.log` |
| 首轮 GREEN：同一组文件 | 61 passed、3 failed；新文件 12 项全部通过 | `.pytest-tmp/trade-watch-errors-green.log` |
| 首轮后端全量 | 1935 passed、3 failed、4 deselected、12 warnings | `.pytest-tmp/trade-watch-errors-full.log` |
| 最终版本定向复验 | 61 passed、3 failed；新文件 12 项全部通过 | `.pytest-tmp/trade-watch-errors-final-focused.log` |
| 最终版本后端全量 | 1935 passed、3 failed、4 deselected、12 warnings；166.55 秒 | `.pytest-tmp/trade-watch-errors-final-full.log` |

复现最终定向和全量命令（按顺序运行）：

```powershell
python -m pytest tests/test_trade_availability.py tests/test_trade_availability_cache.py tests/test_opportunity_trade_availability.py tests/test_trade_availability_monitor_errors.py --basetemp=../.pytest-tmp/trade-watch-errors-final-focused -q --tb=short
python -m pytest --basetemp=../.pytest-tmp/trade-watch-errors-final-full -q --tb=short
```

项目默认排除 `network`、`benchmark` 标记，未改变该设置。全量 12 个 warning 均为既有 `datetime.utcnow()` 弃用提示。

## 未通过项与任务书偏离

1. **现有回归断言与新需求冲突，未达到全量全绿。** `tests/test_trade_availability_cache.py::test_runtime_error_read_only_reproduction` 的 `raw_whitespace-1-True`、`exchange_case-1-True`、`hl_upstream_missing-0-True` 三组仍精确要求 `RuntimeError: 聚合行情中未找到指定原始市场`，也要求 traceback 中异常文本没有诊断后缀。新需求 §3.2 要求存在 `result.errors` 时附加诊断，因此这三组失败。前两组附加 `实时订单簿请求失败：RuntimeError: 没有对应交易所订单簿适配器`，第三组附加 `RuntimeError: Hyperliquid 公开接口未返回指定 DEX 原始市场`。为严格保留文件范围，没有修改、跳过、标记 xfail 或从新测试文件干预旧用例。其余回归通过。
2. **恢复日志的代码位置说明。** 为在持久化及通知均成功后记录“已恢复”，同时保留原成功主体不变，在原异常处理结构末尾新增 `try/except/else` 的 `else`。若把任务书“异常块”严格理解成仅 `except` 的语句体，这是必要的三行结构扩展；成功主体未插入任何代码。
3. **基线说明。** 实际开始于含任务书的 `88aea55`，而非直接从生产基线 `f7f56a9` 开始；未改动分支或基线。
4. **交付边界。** 按本次明确指令只做本地提交，未执行项目默认的 push、生产备份、部署或线上验收。

新增测试文件的 Ruff 检查通过。对业务文件和新测试文件一起检查时，仅剩原有 `backend/app/services/trade_availability.py:1401` 的 `TRY004`（Arcus 响应校验）；该位置在禁止修改区域，保留原状。

## 线上状态、产物与下一步

- 本次未连接服务器，未核验当前线上版本或观察项状态，未创建线上数据库备份，也没有修改线上观察项。任务书中的线上证据属于任务书记录。
- RED、GREEN、全量和最终复验日志，以及 pytest 生成的临时文件均保留在工作区 `.pytest-tmp/`。该目录未跟踪，不纳入提交；现有数据库和日志产物不清理。
- 提交前只暂存上述三个文件，提交信息为 `fix: dedupe trade watch failure logs and keep diagnostic reason`。交付提交为当前分支中包含本交接文档的单个提交；用 `git log -1 --oneline` 查询。
- 后续应在另一个经授权的任务中更新三组旧断言以符合诊断保留规则，再运行同组回归和后端全量。当前不能将此交付标记为全部测试通过。
- 推送和上线需要后续明确授权；上线前按项目流程备份数据库并验证部署版本，之后观察至少一小时的日志与页面 `last_error`。其他模块应另建任务继续。
