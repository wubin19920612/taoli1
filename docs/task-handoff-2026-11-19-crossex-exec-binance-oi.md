# CrossEx 开仓复核的 Binance 持仓量

本次在分支 `codex/crossex-exec-r8` 的 `6eb8a11` 上，将每条 Binance 腿的 OI 请求从三个减为两个。耗时对照版本是 `5d3b5ca`，该版本同样为每条 Binance 腿发送两个请求。

## 请求与估值

`oi_urls` 按顺序为原始合约代码生成 `/fapi/v1/openInterest` 和 `/fapi/v1/premiumIndex` 两个 URL。`parse_oi` 要求两个响应的 `symbol` 都精确匹配原始代码，数量和标记价均大于零。估值使用 `openInterest × markPrice`，单位为 `native_base`，估值时间取实时 OI 的 `time / 1000`。证据字段为 `quantity`、`mark_price`、`mark_at`、`quote`。 [Binance OI 请求与解析](../tools/crossex_exec/open_interest.py)

缺失或非法字段返回 `oi_unit_unverified`。实时 OI 和标记价分别接受年龄在 0 至 300 秒之间的时间戳，包含两端。超出范围时，实时 OI 抛出原因是 `oi_missing` 的 `OIStaleError`，标记价的原因是 `oi_unit_unverified`，异常均携带对应字段的年龄。 [估值与原始合约测试](../tools/crossex_exec/tests/test_round8_liquidity.py) · [时效诊断测试](../tools/crossex_exec/tests/test_oi_diagnostics.py)

## 队列与限频模拟

两版分别使用各自的 Binance 和 Bitget 夹具，经 `MarketData.open_interest_payloads` 获取同一批次的两腿数据。普通工作线程为 4 个，盘口专用线程为 2 个，开启盘口保留名额，OI 批次使用默认的 3 秒截止时间。模拟传输在每个请求返回前等待指定时长。占用场景在计时前连续调用两次 Binance 的 `admit_send` 和 `finish_send`，使普通名额进入一秒限频窗口。 [批次入口](../tools/crossex_exec/market.py) · [原始响应夹具](../tools/crossex_exec/tests/test_engine.py) · [队列回归测试](../tools/crossex_exec/tests/test_opening_book_latency.py)

每组每版各运行 5 次，轮流交换版本顺序。下表两列实测值为批次耗时中位数。另用虚拟时钟复跑相同的队列、限频器和截止时间，单独核对等待时间。

| Binance 普通队列 | 单请求耗时 | `5d3b5ca` 实测 | 本次实测 | 虚拟时钟，两版相同 |
| --- | ---: | ---: | ---: | ---: |
| 空闲 | 150 ms | 0.157 s | 0.156 s | 0.150 s |
| 两个请求刚完成 | 150 ms | 1.156 s | 1.157 s | 1.150 s |
| 两个请求刚完成 | 600 ms | 1.609 s | 1.609 s | 1.600 s |

30 个实测批次全部取得两腿数据，失败记录均为空。两个普通名额刚被使用时，两版的两条 Binance 请求都在同一轮名额释放后发出。600 ms 场景中新版最慢为 1.610 s，距离截止时间还有 1.390 s。

## 验收与交付

```sh
PYTHONPATH=tools/crossex_exec/tests python -m pytest tools/crossex_exec/tests -q -p no:cacheprovider
```

```text
1083 passed in 257.50s (0:04:17)
```

本次交付为本地提交。验收覆盖模拟传输、估值和诊断，线上状态未核验。工作开始时没有未跟踪文件。后续如安排上线，可用开仓复核的 Binance `oi_failures` 对照实际请求超时及时间戳过期情况。
