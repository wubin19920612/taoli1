# 历史数据库维护性能交接

日期：2026-10-09。用户要求同一对话持续优化，本轮为独立的历史维护模块。

## 基线、目标与证据

- 分支 `codex/frontend-localization-polish`，本地基线 `68854ff`，生产基线 `46ea5e4be3ba02c912a07028f1d59090d9b4b057`。
- 目标：减少历史清理后的低收益整库 VACUUM，保留采样、保留期、实际查询数据和必要空间回收。
- 代码证据：`OpportunityHistoryRecorder._prune` 在删除至少一条记录后、距上次压缩够久或刚启动时，无条件调用整个 SQLite 数据库的 `VACUUM`；共享连接上的其他查询需要等待该操作。
- 只读线上审计：主库 150,207 页 × 4,096 bytes，约 615 MB，空闲页 3；opportunity_history 本体约 26 MB，大量空间是其他采样表/索引。仅清理少量机会历史也会重写其他表。证据在 `output/optimization-database-audit-20261009.json`，未读取账户凭证。

## 实现与业务边界

- `backend/app/db/repositories.py::OpportunityHistoryRepository.vacuum_if_beneficial`：使用 SQLite 元数据检查，空闲页至少 **16 MiB 且占全库至少 10%** 才执行整库压缩；查询游标显式关闭。
- 存在未提交事务时跳过自动压缩，不主动提交其他调用方的数据。既有显式 `vacuum()` 保持无条件执行，供明确的手动维护使用。
- `backend/app/services/history.py`：正常按原规则采样、排序、写入及删除超期历史；仅将自动压缩改为上述判断。只有实际成功压缩才更新计时器，跳过/失败后仍可重试；原来最短压缩间隔保持不变。
- 小规模空闲页留在数据库中由未来写入复用，因此物理文件不会像以前那样每次立即缩小；这不是删除业务数据或延长保留期。门槛不是严格的磁盘总量上限，实际空间仍受其他表和原压缩间隔影响。
- 不删除/迁移既有采样表，不改变 WAL/同步级别，不增加线程或连接，不在生产做试验性 VACUUM。

## 验证

- 历史记录/仓库/新增维护测试 **18 项通过**：无空闲页、回收量不足、回收比例不足、达到门槛、空闲页复用、保留行与完整性、事务不被提交、成功间隔、失败重试及无删除时无额外检查。
- 历史列表/统计及健康 API **5 项通过**。
- 新测试和基准脚本 Ruff 通过。两份原有业务文件分别有 11/1 条历史 Ruff 提示，已与 Git 基线逐项比较，未引入新增提示；没有扩大到无关格式修复。
- 仓库旧测试有一次 aiosqlite 连接在线程结束时遇到已关闭事件循环的警告，旧测试存在未关闭连接；新测试均用 finally 关闭连接。本轮未将这个测试夹具问题改为生产逻辑。
- `script/benchmark_history_maintenance.py` 使用临时合成数据库，交错测量 3 轮，验证剩余记录/完整性，随后只清理自己创建的临时目录。未使用任何生产数据库内容。
- 命令：`python script/benchmark_history_maintenance.py --output output/history-maintenance-benchmark-20261009.json`。
- 64 MiB 载荷、删除 2 MiB：原先无条件 VACUUM 中位 **651.91 ms**，新检查 **1.48 ms** 并跳过 VACUUM；逻辑文件暂留 67,207,168 bytes（旧压缩后为 65,105,920），对应空闲页已用测试验证可复用。
- 删除 24 MiB：两者均执行 VACUUM 并缩小到 **42,008,576 bytes**，中位耗时 608.13/532.30 ms；I/O 样本波动明显，不把这组差值宣称为压缩算法加速。
- 性能结论限定为减少低回收收益的整库重写；需要大量回收时仍承担 SQLite VACUUM 原有成本。

## 发布与线上验收

- 功能提交 `5a3bc91f3630bc83f6d9645c2da07ef2c590b04c` 已推送，GitHub Actions `37891748398` 两份镜像成功，生产机验证两份固定 SHA manifest 后部署。
- 主库备份 `backups/radar-before-5a3bc91f3630-20261009T060844Z.db`，615,297,024 bytes；`integrity_check=ok`，容器/主机 SHA-256 一致：`7ba9845badc15942db668978d942386fc6c3aa456858263f2ca5b3eb858d550a`。
- Route 备份 `backups/squeeze-route-before-5a3bc91f3630-20261009T060844Z.db`，241,664 bytes；`integrity_check=ok`，SHA-256 一致：`4126a81d8b0388db27a31c9e66b4ab96d79ff3e2f2e0480488eafeafee21ba72`。
- 服务器 `git pull --ff-only`、固定镜像 `up -d --no-build --wait` 成功，源码/两个镜像为 `5a3bc91`；两个容器 healthy、重启计数 0、无 OOM。未现场构建、未删除数据卷，保留策略本次未删除备份。
- 2026-10-09 06:10:46 UTC：11,841 条行情、7,486 个机会，10 个行情源 healthy、错误为空；前端首页及前后端健康接口正常。
- 只读连接同时设置 query_only，当前空闲页 0，调用新自动维护方法返回 False，SQL trace 中无 VACUUM；探针没有对生产执行试验性压缩。
- 历史启用且保留期仍为 3 天。部署前最新历史为 06:06:23，部署后为 06:10:01 UTC；历史接口与 DB 最新时间一致。最早记录推进到 2026-10-06 06:11:39 UTC，说明正常超期删除与新样本写入均在继续。
- 完整日志在 `output/history-maintenance-deploy-20261009.log`，前后只读结果在 `output/history-maintenance-before-20261009.json`、`output/history-maintenance-production-20261009.json`。最终验收文档另作 `[skip ci]` 提交，不再次部署应用。

## 工作区与下一轮

- 保留原有两份 PairMonitor 前端修改、旧交接草稿、`.worktrees/`、pytest 和 output 产物。
- 本轮 `backend/.pytest_history_maintenance_20261009/` 与 `output/history-maintenance-*` 不提交。
- 持续目标未完成；本模块上线后，按优化清单继续测量标的历史统计中的重复交集/排序。
