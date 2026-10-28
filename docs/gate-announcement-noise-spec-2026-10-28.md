# Gate 公告 567 降噪：任务书（Claude，2026-10-28，待用户确认）

基线：`4ad88b7`（当前生产版本）。worktree `.worktrees/gate-announcement-noise`，分支 `codex/gate-announcement-noise`。

## 1 线上证据（10-28，只读）

- 最近 60 分钟，`failed to fetch gate announcement category` 出现 **280 次**，每次都带完整 traceback（`httpx.HTTPStatusError: Server error '567 Unknown Status'`）。5 个分类（`newspotlistings`、`newfutureslistings`、`newconvertlistings`、`delisted`、`""`）每次轮询都失败，是目前日志里最大的噪声来源；10-27 起就一直这样。
- 回退路径 `_fetch_reader_page`（r.jina.ai）同期 **0 次失败**，所以 Gate 公告目前靠第三方读取器还能拿到（`general` 和 `dividenddistribution` 两页）。
- 从服务器主机直接请求同一个地址返回 200，后端容器内请求返回 567。客户端的 UA 是 `taoli1-radar/0.1`（`announcements.py:1195`）。567 很可能是 Gate 对非浏览器客户端的风控拦截。**本任务不伪装 UA，也不做任何绕过风控的处理**；是否改用 Gate 官方 API，由用户另外决定。

## 2 现状（`backend/app/services/announcements.py`）

- `GateAnnouncementProvider.fetch`（2036 行起）：逐个分类请求；任何异常都会 `logger.warning(..., exc_info=True)` 后 `continue`；如果 `""`（general）分类失败，就回退到读取器的两页。
- 轮询间隔至少 30 秒（`announcement_polling.py:55`）。也就是说，每次轮询都会向 Gate 直连接口发 5 个注定失败的请求，并打 5 条 traceback。

## 3 设计（最小改动，只改 GateAnnouncementProvider）

1. **按分类去重日志**：实例上维护 `_category_failures: dict[str, str]`，值是失败签名（异常类型，HTTP 错误再加上 status code，例如 `HTTPStatusError:567`）。第一次失败或签名变化时：`logger.warning(..., exc_info=True)`，和现在一样。签名相同时：`logger.debug`，不带 traceback。分类恢复成功时：`logger.info("gate announcement category recovered: %s", category)`，并删除记录。
2. **直连退避**：如果一次轮询中 5 个直连分类**全部**失败，记录 `_direct_retry_at = now + 300 秒`（模块常量 `GATE_DIRECT_BACKOFF_SECONDS = 300`）。在这个时间之前，`fetch` 跳过直连分类，直接走读取器回退，行为等同于 general 分类不可用；每次跳过只打一条 debug。到时间后再试一次直连：任意一个分类成功就清除退避；如果又全部失败，就再退避 300 秒，并且只有在签名变化时才打 warning。
3. 读取器回退的日志（`_fetch_reader_page` 的 warning）、详情抓取、解析逻辑**不变**。
4. 不做：UA、请求头、换接口、其他交易所、轮询间隔、Binance 429。

## 4 设计约束

- 只要有任意直连分类成功，返回结果就和旧实现完全相同（同样的请求顺序、同样的 `announcements` 列表）。
- 退避期间返回结果等同于“5 个直连分类都失败”时旧实现的结果，也就是只有读取器的两页。
- 时间要能注入，方便测试：用模块内的 `_monotonic = time.monotonic` 或者构造参数，二选一。

## 5 文件范围与测试

- 修改：`backend/app/services/announcements.py`（只改 `GateAnnouncementProvider`，加一个模块常量）
- 新建：`backend/tests/test_gate_announcement_noise.py`、`docs/task-handoff-2026-10-28-gate-announcement-noise.md`
- 禁止修改：其他所有文件。

测试先写（用 httpx.MockTransport，不联网，用 `caplog` 断言日志）：
1. 5 个分类连续 3 次轮询都返回 567：第 1 次轮询每个分类各 1 条带 traceback 的 WARNING；之后每轮直连请求数为 0，直到退避到期；退避期间读取器照常被调用，返回内容和旧实现在“全部失败”时一致。
2. 退避到期后，一个分类恢复成功：有一条 INFO“recovered”；退避被清除；下一轮恢复请求全部 5 个分类。
3. 失败签名变化（567 变 ConnectTimeout）时，再打一条 WARNING。
4. 部分失败（例如只有 `delisted` 是 567）：不进入退避；`delisted` 只在第 1 次打 WARNING；其他分类的结果和旧实现相同。
5. 回归：`tests/test_announcements.py`、`tests/test_announcement_public_sources.py`，以及后端全量（`--basetemp=../.pytest-tmp/x`，先 `mkdir -p .pytest-tmp`）。

## 6 上线验收（部署需用户批准）

- 可以和 M2-4b 一起排队，但要逐个上线、分开观察。用 `output/sqlitefix-isolated-deploy-20261028.sh` 这份副本部署。
- 上线后 1 小时：`failed to fetch gate announcement category` 从每小时约 280 条降到个位数；Gate 公告入库没有中断（只读查询 `/api/announcements` 中 gate 的最新时间）。
- 回滚：revert 这个提交。

## 7 派发

- Codex：gpt-6.1-sol xhigh（单个类，边界清晰）。不要跑 WSL。
