# 品种历史统计性能交接

日期：2026-10-09。用户要求同一对话持续优化，本轮范围为品种历史统计。

## 目标、基线与实现

- 分支 `codex/frontend-localization-polish`；本地基线 `5d389e2`，生产基线 `5a3bc91f3630bc83f6d9645c2da07ef2c590b04c`。
- `backend/app/api/routes_instruments.py`：提取内部查询函数，仅统计路径跳过 Astro 卡片路由读取。统计服务不使用路由元数据；普通品种接口保留路由、错误提示、匹配顺序和既有 SDK 缓存。消除统计请求对一次最长 5 秒 SDK 超时的无关依赖，不声称所有请求均节省 5 秒。
- `backend/app/services/instrument_statistics.py`：每市场精确身份只生成一次，先排除不展示的配对，再交集分钟。普通有限正价格不排序全部分钟，仍以 `(spread, timestamp)` 取最大值，因此相同最大价差取最新分钟；首末时间分别取 min/max。
- 若价格超出保守安全范围，保留旧版排序后遍历，避免 NaN/溢出比较顺序变化。范围检查按参与配对的市场懒计算一次，没有配对时不增加整段价格扫描。
- 保留 Hyperliquid DEX、原始市场、别名/合约倍率、双向结果顺序、窗口边界、1440 分钟完整性、数据缺失错误、估算标记。历史收盘价统计不表示真实可成交价，未扣交易手续费。
- 未改变 2 分钟缓存、128 项 LRU、4 路并发、45 秒请求超时、单飞、取消语义和查询窗口。

## 验证与测量

- 冻结旧版 lookup 为 `backend/tests/instrument_statistics_reference.py`，只继承未修改的取数缓存部分。
- 新差分测试覆盖 30 组 dense/sparse/none × full/missing/disjoint/empty/extreme × 固定/随机价格；每组当前分钟和下一分钟完整 JSON（含顺序）一致，输入不被修改；另有最大值时间和窗口边界断言。
- SDK ready/error/blocked 三种故障注入证明统计不调用 SDK，普通查询保留调用和正常/错误路由信息。
- 统计、差分、新接口、品种价差、Astro 路由及历史查询回归共 **133 项通过**；既有完整应用品种接口另 **8 项通过**。最后将安全检查改为懒计算后，直接相关的统计/差分/新接口 **44 项再次通过**。
- 修改的业务文件、新测试和基准脚本 Ruff 通过；逐项审阅差异，未修改价格公式、历史读取或缓存策略。
- `python script/benchmark_instrument_statistics.py`：固定种子、预装分钟缓存、11 次交错顺序，无网络；完整输出与冻结版相同。结果在 `output/instrument-statistics-benchmark-20261009.json`。
- 最近一次测量：1 市场无配对 0.0415 → 0.0406 ms；2 市场 1.006 → 0.944 ms；20 市场 380 个双向结果 195.69 → 153.69 ms（约 -21.5%）；20 市场仅 4 个结果 91.67 → 3.68 ms；20 市场无配对 93.31 → 0.93 ms。系统负载会改变绝对数值，早一轮完整组合 154.23 → 118.65 ms，收益方向一致。
- 峰值 Python 分配：完整组合 791,102 → 772,463 bytes；无配对 200,755 → 23,609 bytes。以上只代表计算，不代表上游网络或全站 p95。

## 发布与线上状态

- 功能提交 `d3542a139c52b51badc54f8ba25ade75f5022c80` 已推送；CI `37894352010` 两镜像成功，部署前验证固定 SHA manifest。
- 主库备份 `backups/radar-before-d3542a139c52-20261009T063840Z.db`，615,809,024 bytes，SHA-256 `f46c8c6a4b4d34afacb75a7567de6560d3e81f93c97a962c6f8deea7168554fa`。
- Route 备份 `backups/squeeze-route-before-d3542a139c52-20261009T063840Z.db`，241,664 bytes，SHA-256 `dddc74b98bfe8246f778b5b022230c0a17439897acdccd21ed99e96807b0c5f3`。两库 integrity_check=ok，容器/主机校验一致。
- 服务器 fast-forward、拉取预构建镜像、Compose `up -d --no-build --wait` 成功。两容器 healthy，前端首页和代理 health 返回 200。既有备份保留策略保留每库 9 份、清理各 1 份旧自动备份；未更改该策略。
- 06:42:55 UTC 线上实际 OPENAI 查询包含 9 个精确市场、36 对品种价差、12 条 Astro 路由；统计 API 返回 72 个双向结果，无市场取数错误。公开历史共 11,540 点在新旧实现中完整字段/顺序一致。
- 健康检查 11,841 行情、7,583 机会、10 源 healthy、无行情源错误。单次健康 41 ms、普通查询 761 ms、统计 API 1.78 s 包括网络，仅作为功能验收样本，不作为前后性能差异结论。
- 部署日志 `output/instrument-statistics-deploy-20261009.log`；结果 `output/instrument-statistics-production-20261009.json`；探针 `output/instrument-statistics-live-probe-20261009.py`。验收文档另作 skip-ci 提交，应用镜像保持功能 SHA。

## 工作区与后续

- 只提交本轮两个业务文件、三个新增测试文件、基准脚本、本交接及总账。
- 原有 `frontend/src/pages/PairMonitorPage.tsx`、`frontend/tests/PairMonitorPage.test.tsx` 修改属于用户/其他任务，保留未暂存；原有 `.worktrees/`、pytest 目录、旧交接草稿和 output 产物均保留。
- 后续继续同一对话审计别名解析、指数观察名单过滤、外部等待与页面/API 负载；有证据再实施，不将本轮完成当作整体终点。
