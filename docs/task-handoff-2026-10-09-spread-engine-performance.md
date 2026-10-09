# 价差计算引擎性能优化交接

日期：2026-10-09（Asia/Shanghai）。

## 目标、范围与基线

- 模块：行情价差计算引擎。降低每轮 SF/FF/SS 机会计算的 CPU 时间和临时分配；完整结果、身份、方向及稳定排序不变。
- 分支：`codex/frontend-localization-polish`；本地基线 `0819151db4d176174090305f6189b7a7e2b2d7d0`；发布前生产版本 `6883583d1292b4b663f60f396b576f6a99905812`。
- 本次不调整采集频率、缓存有效期、陈旧阈值、资金费率算法、告警条件、通知或交易执行，不更改数据库结构。

## 实现及代码入口

- `backend/app/services/spread_engine.py::build_opportunities`：SF 按输入顺序遍历现货 × 合约；FF/SS 按输入顺序遍历同类型无序组合，避免双向重复和不适用类型的配对。
- 同一标的每条合格行情只构造一次用于配对去重的精确市场身份；身份不同的行情免做 Pydantic 完整对象相等比较。身份相同的不同盘口仍按旧逻辑处理，不能提前合并。
- 去重集合限制在当前标的内，结束后不累计保留全市场组合。仍按首次遇到的组合决定结果，再稳定按开仓价差降序排序。
- 保留 `orient_pair` 等原有帮助函数及公开调用签名，其他调用方不需迁移。
- `backend/app/services/market_sessions.py::us_stock_session_close`：剖析发现原先一轮 3 个模式重复计算约 8,394 次日期日历。为只依赖日期、返回不可变 `time`/`None` 的纯函数增加最大 128 项 LRU 缓存。`is_us_stock_market_open` 仍每次读取调用时间并检查开闭市边界，绝不缓存实时开市状态。
- `backend/tests/spread_engine_reference.py`：冻结基线配对遍历作为差分参考，仅用于测试/离线测量。引用的报价与模型构造帮助函数在本次没有变化；以后修改这些帮助函数时，不能把此参考误认为完整历史版本。
- `backend/tests/test_spread_engine_equivalence.py`：45 项新增回归，比较全部字段和排序，验证不修改输入。覆盖重复对象、重复身份但不同盘口、排列顺序、同价差、Hyperliquid DEX、倍率、资金费率周期、缺失值、预估盘口、时效和交易时段。
- `backend/tests/reference_market_sessions.py` 冻结原始无缓存日历，用于引擎差分和性能对照；`test_market_session_cache.py` 覆盖 2024—2026 的全部 1,096 个日期、128 项容量及淘汰后的重算，并逐分钟正反遍历 8 个关键交易日，验证 DST 前后、周末、节假日、正常及提前开闭市状态。
- `script/benchmark_spread_engine.py`：只读取公开行情 JSON 的离线对照，不启动 app/worker，不操作数据库或通知。重复交错测量旧/新版本；输出输入 SHA-256、完整输出一致性、耗时样本、中位数和 Python 峰值分配。

## 必须保留的业务语义

- 精确身份包含交易所、市场类型、原始 symbol、具体 DEX、价格倍率及存在时的合约数量倍率。Hyperliquid 主/子 DEX 和 Lighter/RH-Lighter 均不合并。
- SF 方向始终是买现货、卖合约；FF/SS 沿用旧开平仓价差判断方向的规则，包括交叉盘口及非正价差处理。
- 原始费率及 1h/4h/8h 周期、归一小时/日费率、预测字段、双方成交额、实际 Bid/Ask、手续费与滑点、深度、倍率、数据来源和时间戳均沿用旧对象构造。
- stale 判断仍优先使用上游时间，预估 Bid/Ask 和休市市场继续被排除；没有通过延长有效期、减少校验或降低刷新频率换取性能。

## 验证与性能

- 优化前基线测试 60 项通过；优化后引擎、差分、采集器、别名及交易时段定向测试 102 项通过。
- 增加日期缓存后，最终引擎/日历/采集器/别名/风险标签/数据过滤回归 124 项通过；原始无缓存日历与新实现输出一致。
- Ruff 变更文件检查通过；前端 24 个测试文件、230 项测试全部通过，TypeScript/Vite 生产构建通过。前端验证使用当前工作区；已有 PairMonitor 修改不纳入镜像提交。
- 配对重构版 `aee8cb5` 全量后端 1,061 项全部通过，用时 27 分 14 秒；此进程在追加日期缓存前启动，不能称为最终缓存版本的全量回归。最终 `3d9bf3b` 以此前全量通过加上述 124 项相关回归验收。既有 12 条 datetime.utcnow 弃用警告未在本任务修改。
- 所有测试结束后独立复测：公开行情 11,840 条；旧/新版本全量均输出 7,509 条机会，30 秒时效过滤后均输出 7,455 条；全部字段及顺序一致。
- 9 轮交错测量：全量中位耗时 **632.60 → 196.86 ms（3.21 倍、耗时减少 68.88%）**；30 秒时效过滤 **643.46 → 214.53 ms（3.00 倍、减少 66.66%）**。
- 全量 Python 峰值分配 **35,083,955 → 33,750,886 bytes（减少 3.80%）**；30 秒过滤 **34,851,240 → 33,525,184 bytes（减少 3.80%）**。最大 128 项日期缓存不会随运行时间无限增长。
- 原始输入 SHA-256：`4f577567ce0b5996ddffd50e27468eba64995a4c3972c9fff1e6d926de984ca4`。结果见本地 `output/spread-engine-benchmark-final-20261009.json`，包含每轮样本；桌面运行仍有耗时波动，不声称生产 p95 已降低同样比例。
- 对照命令：`python script/benchmark_spread_engine.py --markets output/spread-engine-markets-20261009.json --output output/spread-engine-benchmark-final-20261009.json`。最终剖析显示日历查询 8,393 次命中、1 次计算；剩余主要成本是机会对象构造/校验与市场识别，未跳过这些必要逻辑。
- 峰值测量包含返回的机会对象、不包含预加载输入；不能视作容器 RSS。引擎耗时也不等同于接口/整站耗时或网络采集周期。

## 发布与线上状态

- 发布前生产 `/api/health` 为 `ok`，约 11,840 条行情、7,630 条机会，交易所状态 healthy、错误为空；前后端容器健康，后端内存单点约 399 MiB / 768 MiB。
- 功能提交 `aee8cb59d454788f83ba39bb4640ae94d093d15c`（配对）及 `3d9bf3b99700eaae15e6e1903b3d92d19c4d69eb`（日期缓存）已推送；最终 GitHub Actions `37885547053` 构建成功，生产机验证两份固定 SHA manifest 可访问。第一次推送遇到 Schannel TLS 握手失败，以单次 HTTP/1.1 + OpenSSL 参数重试成功；未关闭证书校验或修改全局 Git 配置。
- 已按下述流程完成备份与部署，生产机未本机构建。
- 必须使用现有 `deploy/linux-update.sh`：校验备份 integrity/SHA-256，`git pull --ff-only`，拉固定 SHA 镜像并 Compose `up -d --no-build --wait`。不删除数据卷或修改 `.env`。

## 最终线上验收

- 生产源码及前后端镜像均为 `3d9bf3b99700eaae15e6e1903b3d92d19c4d69eb`，服务器受跟踪工作区干净。最终本文补充以 `[skip ci]` 文档提交推送，不代表第二次应用部署。
- `/data/radar.db` 备份：`backups/radar-before-3d9bf3b99700-20261009T050419Z.db`，622,333,952 bytes；`integrity_check=ok`，容器/主机 SHA-256 一致：`e79bf9e4c829f1c6198889ca4d9588cc7cdb925c41d32c9eceee01ff843faf77`。
- Squeeze route 库备份：`backups/squeeze-route-before-3d9bf3b99700-20261009T050419Z.db`，241,664 bytes；`integrity_check=ok`，容器/主机 SHA-256 一致：`e84807ccc47345fe27f1e03bac28c3e565ad341d2350cd7255aec761b7c754c9`。
- 服务器从 `6883583` 经 `git pull --ff-only` 快进，拉取已验证的固定 SHA 镜像，以 `up -d --no-build --wait` 启动成功；备份保留策略本次未删除任何备份。
- 两个容器 healthy，重启计数为 0，`OOMKilled=false`。后端及前端代理 `/api/health` 均正常，前端首页 HTTP 200。
- 2026-10-09 05:06:38 UTC，生产只读差分采样 11,841 条行情：SF 1,494、FF 5,221、SS 812，旧遍历/旧无缓存日历与线上新实现的所有机会字段及顺序一致。
- 实际机会接口抽查 1,000 条，双边原始市场、DEX、价格倍率、成交额、资金周期、费用、买卖报价及时间字段完整，价差为正并按降序排列。
- 后续复查 10 个交易所全部 healthy、错误为空，全部 `last_success_at` 比上次观测前进，行情仍为 11,841 条；机会数量随市场更新变为 7,118，未冻结旧结果。
- 后端内存后续单点观测 352.6 MiB / 768 MiB，前端 5.336 MiB / 96 MiB。重启后缓存预热程度与部署前不同，不能据此把 399.3 → 352.6 MiB 的差异全部归因于本次优化；可确认的分配降幅以同输入离线测量为准。
- 线上本机请求的三次样本：机会列表约 49.7/30.9/30.9 ms，行情列表 365.3/182.5/180.8 ms，健康接口 27.8/29.4/497.1 ms。仍可见其他负载下的延迟波动，样本不足以声称整站 p95 已改善；本次只对引擎计算和有界日期缓存给出性能结论。
- 证据保存在 `output/spread-engine-deploy-20261009.log`、`output/spread-engine-production-20261009.json`、`output/spread-engine-api-before-20261009.json`、`output/spread-engine-api-after-20261009.json`。生产只读探针使用冻结参考实现，没有触发通知、建卡或订单。

## 工作区与下一步

- 任务开始已有 `frontend/src/pages/PairMonitorPage.tsx` 和 `frontend/tests/PairMonitorPage.test.tsx` 的修改，保留且不纳入本次提交/部署。
- 既有 `.worktrees/`、pytest 临时目录、两份未跟踪交接草稿、`output/` 大量截图/日志和 `script/dexe_bybit_bitget_chain.py` 均保留。部分旧 pytest 临时目录存在 ACL 警告，不做清理。
- 本次 `output/spread-engine-*` 行情、测量、测试和部署记录以及 `backend/.pytest_spread_engine_perf_20261009/` 属于验证产物，不提交仓库。
- 本模块完成后，下一模块在新任务继续；可以引用本文档与既有架构评估草稿，优先实测采集等待、重复查询或历史维护开销。这里没有证明整个系统已不存在优化点。
- 后续修改须先复核工作区/线上版本，并保留完整市场身份和数据真实性。不要为了降低内存而跳过机会模型验证或删减交易字段。
