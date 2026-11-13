# 公告相关性过滤交接（2026-11-13）

## 目标与范围

- 任务书：[announce-filter-spec-2026-11-13.md](announce-filter-spec-2026-11-13.md)。仅处理交易所公告模块。
- 工作树：`.worktrees/announce-filter`；分支：`codex/announce-filter`；基线：`b4c2edd08e9a9532be04aa261129173ebe48ea40`。
- 用户明确要求本地实现、验证、commit；禁止 push、部署、运行 WSL。本次未连接生产环境。

## 已完成与代码入口

- `backend/app/models/announcement.py`：增加 `other_alerts_filter: Literal["all", "arbitrage_relevant"]`，默认 `arbitrage_relevant`。原有 JSON 设置缺少字段时自动补默认值，无需数据库迁移。
- `backend/app/services/announcement_relevance.py`：纯函数合并原文标题、中文标题、分类；忽略大小写并规范空白、下划线及连字符；排除规则优先，未命中保留规则时返回 false。保留、排除正则片段均为模块常量，编译一次。
- `backend/app/services/announcements.py`：只在 `should_alert_other` 路径增加过滤。保留记录范围、白名单、历史窗口、持久化去重、上新/下架、Launchpool、事件提醒流程。
- `frontend/src/api/types.ts`、`frontend/src/pages/AnnouncementsPage.tsx`：补齐字段类型、旧数据默认值，在“交易所公告 → 抓取与推送设置”增加“只推对套利有用的 / 全部其他公告”选择和中文说明。
- `backend/tests/test_announcement_relevance.py`：覆盖 282 条样本的逐条固定期望、中英文规则、排除优先、币名子串、设置旧数据/往返、发送前 pending 入库、muted 持久化、白名单独立路径、切换设置不重放历史。
- `backend/tests/test_api.py`：公告设置 API 覆盖旧请求省略字段及两种显式值。
- `backend/tests/test_announcement_public_sources.py`：任意官方频道帖子去重测试显式选择 `all`，保留原有发送断言。
- `frontend/tests/AnnouncementsPage.test.tsx`：覆盖旧设置补默认值、两种设置的加载/切换/保存/重新加载，以及其他设置不被覆盖。

## 样本与预览

- 用户提供的 `docs/announce-titles-7d.json` 原样移动到 `backend/tests/fixtures/announce-titles-7d.json`，保留顺序、原始三列和重复标题。
- [announce-filter-preview.md](announce-filter-preview.md) 为保留/排除两列表，带原始行号。282 条中保留 **91** 条，排除 **191** 条。
- 可在仓库根目录运行 `python script/preview_announce_filter.py` 重新生成；实际验证时使用 `python -X utf8 script/preview_announce_filter.py`，输出 `total=282 retained=91 excluded=191`。
- 除任务书直接列出的表述，还覆盖同类业务的 `step size`、`size decimal places`、`collateral ratio`、`stock consolidation`、`rename`、`Rebase`、`futures index` 等表述。英文使用单词边界，避免把 Clearpool、LEARNUSDT 误认为学习/理财推广。

## 重要业务边界

- `other_alerts_enabled` 原默认值 false 不变；生产旧 JSON 若原为 true，则保持 true 并补上新的相关性过滤默认值。
- `alert_exchanges` 白名单仍独立接收对应交易所的全部新公告，不受相关性过滤和分类开关限制。`record_exchanges` 继续优先约束是否入库。
- 非白名单交易所中，开启其他公告提醒并选择 `arbitrage_relevant` 时，活动类仍入库为 muted；充提类先 pending 入库，再尝试发送。`all` 沿用旧行为。
- 已入库 muted 的公告不会因切到 `all` 而补发；未修改历史数据。
- 相关性是词表规则，不是全文语义判断。282 条样本只有标题，预览没有中文标题和分类；生产还会结合这两个字段。出现未知表述时可能漏报，可补规则和固定期望用例。

## 验证记录

全部在 Windows PowerShell 下执行，使用已有 Python 与前端 node_modules 目录链接，未运行 WSL。

| 工作目录 | 实际命令 | 结果 |
| --- | --- | --- |
| `backend/` | `pytest tests -q -p no:cacheprovider --basetemp ../.tmp-annf -k announce` | `599 passed, 3296 deselected in 36.15s`（最终代码复核） |
| `frontend/` | `npx.cmd tsc --noEmit` | 退出码 0，无错误输出 |
| `frontend/` | `npx.cmd vitest run --no-cache` | `Test Files 1 failed \| 43 passed (44)`；`Tests 1 failed \| 686 passed (687)`。未修改的 SettingsPage 提交告警规则用例在 15000ms 超时 |
| `frontend/` | `npx.cmd vitest run --no-cache --maxWorkers=2 --minWorkers=1` | `Test Files 44 passed (44)`；`Tests 687 passed (687)`；`Duration 560.34s`；退出码 0，未修改测试超时门槛 |
| 仓库根目录 | `ruff check --no-cache backend/app/services/announcement_relevance.py backend/tests/test_announcement_relevance.py script/preview_announce_filter.py` | `All checks passed!` |
| 仓库根目录 | `git diff --check` | 通过 |

- 首轮后端回归发现 COPPERUSDT 的纯英文 `futures index` 表述漏匹配，已补规则和用例；原有 Bitget 去重与超时恢复测试在默认过滤下重新通过。
- 前端全量存在 React act、Ant Design 弃用等告警；本任务没有扩大范围处理这些提示。

## 线上状态、产物与后续

- 仅本地提交，未 push、未备份生产库、未部署、未验证线上接口或真实飞书发送；线上状态未变更，本地测试不作为线上验收。
- 初始工作区只有任务书和标题 JSON 两个未跟踪文件，均属于本任务并纳入提交；标题 JSON 按上文移动。
- 本地测试日志 `output/announce-filter-vitest-retry.log` 位于已忽略的 output 目录，不提交；保留已有 output 产物。没有改动 node_modules 链接。
- 无功能简化；以确定性词表过滤为实现边界。后续如需上线，在新的服务器运维任务中按仓库部署流程单独授权和验证；其他功能模块请另建任务。
