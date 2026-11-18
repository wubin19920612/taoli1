# crossex-exec：开仓前自动设 3 倍杠杆（2026-11-18）

## 问题
`tools/crossex_exec/accounts.py` 的 `check_instruments()`（约 313–326 行）要求：
- CrossEx 腿：`self.leverages[symbol] == 3`，否则 `crossex_leverage_mismatch`；
- Bitget 腿：`symbolConfigList` 中该合约 `marginMode=='crossed'` 且 `leverage==3`，否则 `bitget_symbol_config_mismatch`。

程序只检查不设置，交易所默认杠杆不是 3，于是凡是没被人工设过的币都会被拒。用户已决定：由程序自动设置。

## 先查文档（必须，允许联网）
- Gate CrossEx：<https://www.gate.com/docs/developers/crossex/en/>，确认设置杠杆的接口（路径、方法、参数、是否按 symbol、返回形态、是否需要无持仓/无挂单）。现有读取是 `GET /api/v4/crossex/positions/leverage`（adapters.py 约 158 行）。
- Bitget UTA v3：确认按合约设置杠杆、设置全仓保证金模式的接口（如 `/api/v3/account/set-leverage`、`/api/v3/account/set-margin-mode` 之类，以官方文档为准），hedge_mode 下 long/short 是否要分别设置。
- 在报告里写明使用的接口和文档出处。文档无法确认的部分不要猜：对应交易所的自动设置保持关闭，仍按原逻辑拒单，并在报告中说明。

## 行为
在开仓复核中、`check_instruments` 发现某腿杠杆（或 Bitget 保证金模式）不符时：
1. 只在下面全部满足时才尝试设置：
   - 当前 `can_open()` 为真（实盘开仓未暂停、无停开锁）；
   - 该交易所该合约**没有任何持仓**（两个方向）且**没有未完成挂单**（以本次读取的账户/挂单数据为准，读不到就不设）；
   - 该合约距离上次设置失败已超过 10 分钟；全局每小时最多尝试 30 次。
2. 设置目标：杠杆 3；Bitget 另需全仓 `crossed`。不改账户级模式（accountMode/holdMode/assetMode）。
3. 设置后必须重新读取并确认等于目标值；确认通过才继续本次复核，并更新内存中的 `leverages` / 设置缓存。
4. 任何失败（接口报错、超时、回读不符、条件不满足）：本次信号按可预期拒绝处理，原因 `leverage_set_failed`（或 `leverage_set_skipped`，区分是否发过写请求），加入 admission 的可预期拒绝原因；**不得**触发停开锁、告警刷屏、flatten、不得计入 single_failures、不得调用 balance()。
5. 写请求走现有 HTTP 客户端与限频分组（先排队限频再发送，不要绕过），超时按现有设置。
6. 每次写请求记录事件 `LEVERAGE_SET`（exchange、symbol、before、target、result、error 摘要），不得记录任何密钥/签名。

## 不要做
- 不改下单、限额、停开锁、负债、对账逻辑；不放宽"必须是 3 倍 + 全仓"的检查本身。
- 不改账户级设置；有持仓或挂单时绝不改杠杆。
- 测试中不得真的联网下单或改设置（测试全部用假 HTTP）。

## 验收
- 测试（新文件，命名清楚）：
  - CrossEx 杠杆 10 → 无持仓无挂单 → 发设置请求 → 回读 3 → 本次继续复核。
  - 回读仍为 10 → `leverage_set_failed`，无停开锁、single_failures 不变、未调用 balance。
  - 有持仓 / 有挂单 / 读不到持仓 → 不发写请求，`leverage_set_skipped`。
  - 失败后 10 分钟内同合约不重试；每小时 30 次上限。
  - 开仓暂停（STOP 文件）时不发写请求。
  - Bitget：杠杆不符、保证金模式为 isolated 两种情况各一条；hedge 模式下按文档要求设置。
  - 已经是 3 倍时不发任何写请求。
  - 事件不含密钥。
- 跑全量：PYTHONPATH 设为 tools/crossex_exec/tests，`python -m pytest tools/crossex_exec/tests -q -p no:cacheprovider`，必须全绿，不得放宽已有断言。
- 只改 tools/crossex_exec/accounts.py、adapters.py、engine.py（复核调用处）、admission.py（原因列表）及测试；不要 git commit。
- 报告：接口与文档出处、改动文件、入口函数与行号、测试汇总行。
