# OKX 历史价差统计限流修复

## 目标与范围

修复标的查询中 OKX 相关方向的 24h 最大/最小价差因历史 K 线限流而为空的问题。范围是历史查询请求调度、标的统计缓存和异常展示。

分支为 `codex/frontend-localization-polish`；本地基线 `6fbedb683984aa50971a8907a55e986844de7f61`，部署前生产代码及镜像为 `29ed920552ea6582d3ab8771741af0c01d7c2c4c`。

## 现场证据与原因

- 生产 `/api/instruments/CTUSDT/statistics` 中 OKX `CT-USDT-SWAP` 与 `CT-USDT` 返回历史接口 HTTP 429；相关方向 `point_count=0`。Binance → Aster 同时有 1440 点，极值约 +1.184% / -1.435%。
- 原实现每两分钟缓存失效后重新拉取 24h03m；OKX 每页 100 条，一个市场约 15 页。没有跨查询服务实例的同接口节流，失败只等待 0.15 秒重试；分页异常丢失本次已收集的页面，失败结果覆盖旧缓存。
- [OKX 官方历史 K 线接口](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks-history) 的限制为每 IP 每 2 秒 20 次。没有当时全部请求日志，不能精确还原哪组并发触发了该次 429。

## 实现与代码入口

- `backend/app/services/okx_history.py`：生产事件循环内所有查询实例共享 0.25 秒的请求间隔；HTTP 429 和业务码 50011 都进入共享冷却；最多三次尝试，2/4/8 秒退避并遵守数值或 HTTP-date 格式的 Retry-After。单次调用等待上限 30 秒，超时不会提前结束共享冷却。非零业务错误不再当成空数据。
- `backend/app/services/pair_spread_query.py`：只有 OKX `history-candles` 进入上述调度；分页失败通过 `PartialKlineHistoryError` 携带有效价格及准确的半开覆盖区间。既有图表消费者仍按原异常路径处理，标的统计消费部分数据。
- `backend/app/services/instrument_statistics.py`：按精确市场、DEX、价格倍率、合约倍率和绑定修订隔离缓存；保留原有请求合并及两分钟刷新节奏。缓存每次裁剪至 24h03m，记录已获取区间，只补缺口和最新三个已完成分钟；先更新最近价格再回补历史。部分失败保留有效样本，后续恢复同时修补中间和前缀缺口。价格与覆盖元数据一起按 128 个市场的 LRU 上限淘汰。
- 最大/最小仍只计算最近 24h 的双方共同分钟收盘价，1440 个共同点才标为完整；正常极值、方向、倍率、峰谷时间和估算口径与冻结参考一致。即使有可用样本，也把历史更新异常传到前端；超过窗口的数据不会保留用于统计。
- `frontend/src/pages/InstrumentLookupPage.tsx` 与 `frontend/src/styles.css`：区分限流、超时、获取失败与确无共同样本；有保留样本时显示数量、警告和样本截至时间。手机端列标题同步为最大/最小价差。
- 线上浏览器和持续监测进一步复现第二个原因：当前行情请求包含 Bybit 配对，随后的统计请求却因盘口跨过 30 秒过期门槛排除了所有 Bybit 配对；另一次统计甚至在历史样本齐全时把 OKX 方向数从 30 降到 0。`routes_instruments.py` 只为历史统计调用启用 `historical_statistics`，`build_instrument_spreads` 增加默认开启的 `require_fresh_quotes` 参数。历史统计跳过瞬时盘口年龄过滤，继续执行资产身份、精确市场和屏蔽交易所过滤；实时价差调用仍采用默认的新鲜度要求。

## 验证

- 后端历史请求、统计和既有价差查询回归：113 passed；补充正向分页和中间缺口案例后，请求/统计/冻结参考组为 55 passed（既有价差查询 60 项已通过）。
- 前端 `InstrumentLookupPage.test.tsx`：41 passed，包括限流空值和部分样本警告。
- 第二个原因的新增测试先复现失败，再验证历史统计包含过期盘口对应市场、实时价差仍排除它们、冲突资产和屏蔽交易所不得进入历史配对、多资产未选择时继续返回 409。路由/价差/身份/统计/冻结参考组共 66 passed；相关文件 Ruff 及差异检查通过。
- 前端 `npm run build` 成功；新文件及统计文件 Ruff 检查通过。价差查询大文件的既有 TRY004/RUF100 问题不属本次变更，排除这两个既有规则后检查通过；`git diff --check` 通过。
- 桌面 1440px、手机 390px 本地浏览器使用明确模拟的限流响应，验证有效极值、1438 点、警告和样本截至时间；无 pageerror 或页面横向溢出。证据见 `output/okx-history-local-*-20261019.png` 和浏览器 JSON。
- 在生产容器内独立验证进程中，仅把候选模块装载到该进程内存，未替换运行应用：使用两个真实已结束的历史窗口，首轮 OKX 现货/合约 30 次请求，耗时 7.601 秒；窗口推进两分钟后只需 2 次请求，耗时 0.492 秒；最短请求间隔 0.250 秒，无非 200 响应，两轮相关方向都达到完整 1440 点。原始证据 `output/okx-history-predeploy-20261019.json`。
- 标的查询路由、市场身份、价差和通用 API 扩展回归：131 passed / 5 failed。5 项均为 Astro 预览默认值、人工建卡默认值和 live-pilot 建卡旧用例。用 `git archive` 导出修改前完整基线 `6fbedb6`，逐项复跑得到完全相同的 5 项失败（前三项 409 而预期 200，后两项模拟建卡列表为空）。已证明不是本轮新增回归，留在独立模块处理；不能把扩展套件描述为全部通过。证据：`output/okx-history-expanded-api-20261019.txt`、`output/okx-history-baseline-api-20261019.log`，基线副本及测试失败产物保留。

## 交付状态

- 功能提交 `414aa1f1d1c45c094a7ef305c40e4698cfe92f38` 已推送至当前分支。
- [GitHub Actions 39277068437](https://github.com/wubin19920612/taoli1/actions/runs/39277068437)：backend/frontend 两个构建均成功；生产逐一核验两个精确 SHA 的镜像 manifest。
- 主库备份 `backups/radar-before-414aa1f1d1c4-20261019T152544Z.db`，641298432 字节，`integrity_check=ok`，容器/宿主机 SHA-256 一致：`3160ff00a735d3afa4b85a02d72123e3f410851bf3f95bcd5746313a02ff8dd9`。
- 研究库备份 `backups/squeeze-route-before-414aa1f1d1c4-20261019T152544Z.db`，241664 字节，`integrity_check=ok`，容器/宿主机 SHA-256 一致：`7116642f7488c443a3dc33e4409ea111fd3a5846c34cea09286ed47c92bf2a55`。
- `deploy/linux-update.sh` 成功完成备份、`git pull --ff-only` 和 `up -d --no-build --wait`；生产 HEAD 及两个运行镜像均为上述功能 SHA，两容器 healthy。既有保留策略清理一份较旧主库备份及一份较旧研究库备份，保留本次备份和命名手动备份。部署日志保留于 `output/okx-history-deploy-20261019.log`。
- 首轮部署初验：前端代理 `/api/health` 为 `ok`；CT 查询有 9 个精确市场、28 个价差组，包含 OKX `CT-USDT-SWAP` / `CT-USDT`。统计曾达到 30 个 OKX 方向、1440 点，并完成分钟增量更新且无上游错误；持续验收发现上述实时盘口过滤问题，因此没有把首轮部署标记为完成。失败截图、浏览器抓取的精确配对 ID 和监测记录保留在本任务 output 中。第二次修复待部署后补齐最终验收。

## 边界、工作区与下一步

- 调度器共享范围是单事件循环。当前生产 Uvicorn 为单 worker；若将来增加 worker、部署多个副本或共享出口的其他应用，应使用跨进程/跨实例的统一预算。
- 缓存位于内存，重启后需要首次回填；交易所持续故障仍可能返回部分样本或无样本，页面明确显示，不填零或伪造数据。
- 不改历史收盘价为真实盘口价差，不改变实际交易价格、资金费率或交易执行逻辑。
- 开始前已有 `frontend/src/pages/PairMonitorPage.tsx` 和 `frontend/tests/PairMonitorPage.test.tsx` 的未提交修改，完整保留并排除提交；既有未跟踪文件和测试日志也保留。
- 本任务产物仅在 `output/okx-history-*`、`output/verify-okx-history-20261019.mjs` 和 `output/pytest-okx-history-*`，不提交运行证据和本机配置，不记录凭据。
- 本模块交付后，下一个无直接依赖的模块应新建任务，以本文件继续核对线上状态。
