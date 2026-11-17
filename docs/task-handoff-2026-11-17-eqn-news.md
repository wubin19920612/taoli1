# eqn.news 公告接入交接（2026-11-17）

## 范围与基线

- 任务书：`docs/eqn-news-spec-2026-11-17.md`。
- 工作树：`.worktrees/eqn-news`；分支：`codex/eqn-news`。
- 基线：`74ef0736f483fd0748ca76469e650bea0de85371`。
- 本次按任务书完成本地实现、离线验收和一个 commit。未推送、未操作生产数据库、未部署；线上版本和真实接口时延未验证。
- 未使用 WSL；未修改官方 provider 抓取逻辑、公告相关性词表、交易/执行/采集模块。

## 实现与入口

- `backend/app/services/announcement_eqn.py`：`EqnNewsClient` 和 `EqnAnnouncementProvider`。复用公告 HTTP 客户端构造、环境代理和超时；支持 JSON/SSE、会话头、失效后重建一次、工具错误、429 数字或 HTTP 日期退避。所有请求含 initialize/retry 至少间隔 0.21 秒。
- 固定轮询 `binance/korea/alpha/tier2/tier3`，每频道 20 条；来源按任务书白名单映射，未知来源只按 source 计数记 debug。使用原始 `no` 构造 `eqn-{no}`；频道重叠记录在本批去重。
- `backend/app/core/config.py`：`EQN_NEWS_ENABLED=true`、`EQN_NEWS_POLL_SECONDS=10`，间隔最小 5 秒。关闭开关时工厂不创建 eqn provider。
- `backend/app/services/announcement_polling.py`：eqn 激活只依赖公告总开关和 eqn 开关，使用自己的间隔；实际入库仍由 `record_exchanges` 过滤。其他 provider 的激活、间隔保持原样。
- `backend/app/services/announcements.py`：共用 HTTP 构造函数、工厂接入、扩充交易所选项、跨源去重、飞书交易所名后加 `（eqn.news）`。eqn 自带中文，不调用 localization。
- `backend/app/db/repositories.py`、`backend/app/db/schema.py`：跨源查询和时间索引；重复记录隐藏与提醒保护；设置读取迁移。原有唯一键未改。
- `backend/app/models/announcement.py`：新增默认记录交易所和 `exchange_catalog_version=2`。
- `frontend/src/pages/AnnouncementsPage.tsx`：仍优先使用 `/announcements/exchanges` 返回的选项；补齐默认记录范围与兜底选项的 Upbit/Bithumb/MEXC/Aster。

## 重要规则

- 同交易所、不同 source、发布时间闭区间 ±6 小时内，URL 相同，或任一中英文标题经小写、去标点和空白后相同，视为重复；空标题不参与匹配。
- Monitor 内仅锁住查重和入库，避免并行来源都先查到“无重复”。网络推送和研究在锁外进行；沿用现有单 Monitor 运行方式。
- 后到记录仍入库，`alert_status=duplicate`、提醒状态 `not_applicable`；不推送、不研究、不提醒，默认列表隐藏。后续 metadata 补全不会重新启用重复记录的提醒。
- `announcement cross_source` 日志包含来源与 `lead_seconds`；该值为本条 fetched_at 减去先入库匹配记录 fetched_at。
- 保留现有历史同步、告警时效和分类过滤。默认关闭首次启动告警时，新来源初始历史批次仍静默，不保证首批历史记录推送。
- 设置迁移位于公告设置仓储读取路径，`load_announcement_settings` 和设置 API 都经该路径。仅对缺少版本或版本低于 2 的已保存设置追加四家，不恢复旧交易所的用户取消选择；比较旧 payload 后写回，避免并发迁移覆盖新的保存。迁移后用户取消新交易所不会再被加回。

## 验证

所有后端测试沿用 `tests/conftest.py` 联网拦截；eqn 测试只使用 fixtures、MockTransport 和内存 SQLite。前端依赖使用 `npm.cmd ci --offline --ignore-scripts --no-audit --no-fund` 安装。

| 目录 | 命令 | 结果 |
|---|---|---|
| backend | `python -m pytest tests/test_announcement_eqn.py -q` | 61 passed in 2.21s |
| backend | `python -m pytest -q` | 4328 passed, 11 deselected, 12 warnings in 384.19s (0:06:24); 退出码 0 |
| frontend | `npx.cmd --no-install tsc --noEmit` | 退出码 0 |
| frontend | `npm.cmd test -- --run` | Test Files 48 passed (48); Tests 830 passed (830); Duration 254.60s |
| backend | `python -m ruff check app/services/announcement_eqn.py tests/test_announcement_eqn.py` | All checks passed! |

首轮后端全量为 `2 failed, 4313 passed, 11 deselected, 13 warnings in 457.97s`：一项是公告交易所接口旧断言，已同步；另一项是 `test_terminate_failure_escalates_to_kill_with_threaded_joins[1]` 的 Windows 子进程关闭超时，未修改该模块，独立复核两个参数及公告接口共 `3 passed in 20.56s`。最终全量在前端测试结束后重新运行，全部通过；剩余 12 个 warnings 都是既有 `datetime.utcnow()` 弃用提示。11 项 network/benchmark 用例按仓库默认配置排除。

前端全部用例通过，有现有 React/Ant Design 警告；PowerShell 5 的 stderr 重定向会使宿主命令误报退出码 1，此行为已用退出码为 0、只输出 stderr 的 Node 命令复现。Vitest 汇总无失败。

## 工作区与后续

- 开始时只有任务书和 `backend/tests/fixtures/eqn/` 未跟踪；保留原内容并纳入本次提交，确保检出后可独立离线测试。
- `eqn-*.log`、`frontend/node_modules/` 是本地忽略产物，不提交。未删除用户文件。
- commit 标题：`feat(announcements): ingest eqn.news exchange notices with cross-source dedup`。可用 `git log -1` 查本交接所在提交。
- 后续如要上线，另开部署任务，按 Git/部署文档确认分支和完整 SHA、备份并校验 `/data/radar.db`、快进更新和容器健康，再核对公告接口、来源观测与真实新公告推送。当前离线结果不代表线上接入已验收。
