# 行情计算与发布链性能交接

日期：2026-10-09。用户明确要求在当前对话持续优化；本轮作为独立模块完成验证和交付后继续下一轮。

## 目标、基线与范围

- 分支 `codex/frontend-localization-polish`，本地基线 `d46a1b3`，生产基线 `3d9bf3b99700eaae15e6e1903b3d92d19c4d69eb`。
- 模块：采集器的纯行情计算与风险标签阶段。减少重复筛选、排序及机会对象副本；完整字段、稳定排序、费用和风险语义不变。
- 不调整轮询、超时、退避、历史采样、通知或交易执行；不引入线程/后台队列、缓存陈旧行情或修改数据库结构。

## 实现入口与约束

- `spread_engine.py::build_all_opportunities` 统一筛选一次可用行情，按 SF、FF、SS 的原有模式顺序构造机会，最后稳定排序一次。原来的 `build_opportunities` 单模式签名、返回顺序保持兼容，共享筛选与配对帮助函数。
- 三种模式的同价差稳定次序保持原样：原来每模式稳定排序后再全局稳定排序，与按原始模式/组内遍历顺序做一次全局稳定排序等价。
- `risk_labels.py::risk_labels_for` 只计算并返回新标签列表；原有 `apply_risk_labels` 继续返回机会副本，不修改调用方输入。
- `collector.py::_build_labeled_opportunities` 仅在刚构造、尚未发布的新机会模型上填写标签；不修改之前发布的 store 数据。每轮重新准备大写同名风险集合，设置变化立即生效。
- 所有模型仍正常经过 Pydantic 构造与校验；双方实际报价、成交额、费率与周期、手续费/滑点、DEX/原始市场/倍率、时间、预估标志和风险标签顺序保持一致。

## 验证与可复现测量

- 新增 `backend/tests/test_collector_pipeline.py` 39 项回归：混合市场差分、同价差与重复身份、费用角色、时效/休市、输入所有权、标签列表独立、设置动态变化和已发布对象不被后续计算修改。
- 引擎/日历/采集器/风险/别名/告警/盘口/资金费率/标的价差/试运行相关测试 **227 项通过**；实际健康、机会列表、限制/筛选 API 回归 **6 项通过**。本轮不重复未改动的全量前端测试，上一轮前端 230 项与生产构建已通过。
- 本轮改动文件 Ruff 和 `git diff --check` 通过；逐行检查了批量排序与对象所有权边界。
- `script/benchmark_collector_pipeline.py` 在离线进程加载可信 Git 基线的 spread_engine、risk_labels、collector，比较完整纯计算方法，不启动 worker、不操作数据库或网络；默认基线为上面的生产提交。
- 命令：`python script/benchmark_collector_pipeline.py --markets output/spread-engine-markets-20261009.json --output output/collector-pipeline-benchmark-20261009.json`。
- 同一批 11,840 条公开行情，7,455 个机会的全部字段及顺序一致；9 次交错测量，中位耗时 **266.14 → 189.82 ms**，约 1.40 倍速度、耗时减少 **28.68%**；峰值 Python 分配 **63,152,868 → 33,620,900 bytes**，减少 **46.76%**。
- 输入 SHA-256 `4f577567ce0b5996ddffd50e27468eba64995a4c3972c9fff1e6d926de984ca4`。峰值包含返回对象，输入与解析不计入；此处是完整计算/标签链，与上一轮仅引擎指标的测量范围不同，不能直接把两张表相除。
- 这是离线同输入测量，不推断整站 p95 或长期容器 RSS 有相同降幅。

## Git、部署与线上验收

- 功能提交 `46ea5e4be3ba02c912a07028f1d59090d9b4b057` 已推送；GitHub Actions `37890414694` 的两份镜像构建成功，生产机验证固定 SHA manifest 可访问。
- 主库备份 `backups/radar-before-46ea5e4be3ba-20261009T055241Z.db`，615,432,192 bytes；`integrity_check=ok`，容器/主机 SHA-256 一致：`7d3432197b9e4904406947420083dfad60dc9032c58f4e43af343f843e468594`。
- Route 库备份 `backups/squeeze-route-before-46ea5e4be3ba-20261009T055241Z.db`，241,664 bytes；`integrity_check=ok`，SHA-256 一致：`b9677900933f10e0ed1c1b7aa22b0217829a3871ad70daa20312654f82588850`。
- 服务器 `git pull --ff-only` 至 `46ea5e4`，使用该 SHA 的前后端镜像 `up -d --no-build --wait` 成功，两个容器 healthy；生产无本地受跟踪修改，未现场构建。既有备份保留策略删除了 10 月 8 日较早的 `43f9345` 两份部署备份，保留本次和选定历史备份。
- 2026-10-09 05:54:47 UTC，生产 11,841 条行情、7,482 条机会、10 个交易所 healthy、错误为空；前后端健康接口与前端首页正常。
- 只读探针读取线上风险设置，在生产容器比较旧 `3d9bf3b` 与新计算链：7,482 条机会的完整字段、风险标签及稳定排序一致；实际机会接口 1,000 条记录的市场身份/双边交易字段完整并按价差降序。
- 日志与探针结果为 `output/collector-pipeline-deploy-20261009.log`、`output/collector-pipeline-production-20261009.json`。最终验收文档以单独 `[skip ci]` 提交推送，不代表再次应用部署。

## 工作区与继续方向

- 既有 `frontend/src/pages/PairMonitorPage.tsx`、`frontend/tests/PairMonitorPage.test.tsx` 修改保留，不纳入本轮。
- 既有 `.worktrees/`、旧 pytest 临时目录、两份旧交接草稿、`output/` 及研究脚本全部保留；本轮 `output/collector-pipeline-*` 与 `output/optimization-*` 是验证产物，不提交。
- 下一轮候选与证据见 `docs/system-optimization-ledger-2026-10-09.md`。目标仍在进行，不能把本模块完成写成整个系统已无优化点。
