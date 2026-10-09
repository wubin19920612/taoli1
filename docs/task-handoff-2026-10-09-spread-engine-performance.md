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
- 全量后端测试与最终性能复测进行中，结果在部署验收后补充。最终回归通过前不部署。
- 初测公开行情 11,840 条，旧/新版本均输出 7,509 条机会，全部字段及顺序一致；9 轮中位耗时约 615 → 317 ms，Python 峰值分配约 35.10 → 33.77 MB。这是初测，最终数值以完成其他测试后的独立复测为准。
- 对照命令：`python script/benchmark_spread_engine.py --markets output/spread-engine-markets-20261009.json --output output/spread-engine-benchmark-20261009.json`。
- 峰值测量包含返回的机会对象、不包含预加载输入；不能视作容器 RSS。引擎耗时也不等同于接口/整站耗时或网络采集周期。

## 发布与线上状态

- 发布前生产 `/api/health` 为 `ok`，约 11,840 条行情、7,630 条机会，交易所状态 healthy、错误为空；前后端容器健康，后端内存单点约 399 MiB / 768 MiB。
- 提交、推送、固定 SHA 的两份预构建镜像、数据库备份与部署信息待最终验收补充。生产机不得本机构建。
- 必须使用现有 `deploy/linux-update.sh`：校验备份 integrity/SHA-256，`git pull --ff-only`，拉固定 SHA 镜像并 Compose `up -d --no-build --wait`。不删除数据卷或修改 `.env`。

## 工作区与下一步

- 任务开始已有 `frontend/src/pages/PairMonitorPage.tsx` 和 `frontend/tests/PairMonitorPage.test.tsx` 的修改，保留且不纳入本次提交/部署。
- 既有 `.worktrees/`、pytest 临时目录、两份未跟踪交接草稿、`output/` 大量截图/日志和 `script/dexe_bybit_bitget_chain.py` 均保留。部分旧 pytest 临时目录存在 ACL 警告，不做清理。
- 本次 `output/spread-engine-*` 行情、测量、测试和部署记录以及 `backend/.pytest_spread_engine_perf_20261009/` 属于验证产物，不提交仓库。
- 本模块完成后，下一模块在新任务继续；可以引用本文档与既有架构评估草稿，优先实测采集等待、重复查询或历史维护开销。这里没有证明整个系统已不存在优化点。
- 后续修改须先复核工作区/线上版本，并保留完整市场身份和数据真实性。不要为了降低内存而跳过机会模型验证或删减交易字段。
