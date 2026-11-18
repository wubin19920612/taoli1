# crossex-exec：接受 1–3 倍杠杆（2026-11-18）

## 背景
实盘 11-18 12:06–13:30：CrossEx 对 EVAA/BLESS/BTW 等小币 `POST /api/v4/crossex/positions/leverage`（leverage=3）返回 HTTP 422，当前杠杆多为 2（OKX_FUTURE_BTW 为 5）。推断这些合约在 CrossEx 上杠杆上限低于 3。用户决定：**接受 ≤3 倍**（杠杆越低越安全）。仓位大小与限额不依赖杠杆（保证金按账户实算），无需改。

## 规则（替换"必须恰好 3 倍"）
- 合格杠杆：`1 <= leverage <= 3`（Decimal 比较）。Bitget 另需 `marginMode == 'crossed'`（不变）。
- `check_instruments()`：合格即通过，不再要求 == 3。只有 `>3`、未知（CrossEx 缓存缺失/非法，Bitget 无行）时才算 mismatch。
- `ensure_opening_leverage()`：
  - 首次读取（before）若已在 [1,3] → 不写，继续。
  - before > 3 或未知 → 照旧写 3（同样的前置检查、5 秒时限、冷却、每小时上限、审计）。
  - 回读：在 [1,3] 即确认通过（不要求恰好 3）；Bitget 回读仍须恰好 1 行 crossed。
  - 写请求返回 HTTP 422（或交易所"参数/超上限"类业务错误）时：不要触发 10 分钟 429 全局冷却（那只给限频）；按现有逻辑记失败、单合约 10 分钟冷却。之后若该合约杠杆读回在 [1,3] 照常可用；>3 则继续拒绝。
- 不新增"降到 2 倍/1 倍"的写请求；目标值仍是 3，只是接受更低的现值。
- 审计、诊断事件字段不变；`LEVERAGE_SET` 的 target 仍记 3。

## 不要做
- 不改 transport.py、下单、限额、停开锁、对账；不改 sizing/limits。
- 不接受 >3 倍、不接受逐仓。

## 验收
- 测试（加到 tools/crossex_exec/tests/test_auto_leverage.py，并相应调整原先断言"必须 == 3"的用例——只改那些把 1/2 倍当作不合格的断言，并在注释写明规则变更）：
  - CrossEx 现值 2 → 不发写、复核继续；现值 1 → 同；现值 0/负数/非数字 → 拒绝。
  - 现值 5 → 写 3 → 回读 3 继续；回读 2 也继续；回读 5 → `leverage_set_failed`。
  - 写返回 422 → `leverage_set_failed`，不设 `leverage_set_cooldown_until`，单合约冷却生效。
  - Bitget crossed/2 → 不写、继续；crossed/5 → 写；isolated/2 → 仍跳过。
- 全量：PYTHONPATH 设为 tools/crossex_exec/tests，`python -m pytest tools/crossex_exec/tests -q -p no:cacheprovider` 全绿。
- 只改 tools/crossex_exec/accounts.py 与 tests；不要 git commit；不要联网、不要对真实交易所发请求。
- 报告：改动文件、行号、调整了哪些已有断言及原因、测试汇总行。
