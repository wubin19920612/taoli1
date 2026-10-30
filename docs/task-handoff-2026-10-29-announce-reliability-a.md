# 公告抓取可靠性 A 交接（2026-10-29）

## 目标、范围与基线

- 按 `docs/announce-reliability-a-spec-2026-10-29.md` §2 实现 Gate reader 预算保护、失败日志去重，以及 OKX / Bitget 网站逐来源诊断。
- 工作目录：`.worktrees/announce-a`；分支：`codex/announce-reliability-a`。
- 开始时 HEAD：`cd03d26`（任务书提交）；代码基线：`dd981ae`。开始时工作区干净。
- 严格限定三个文件：`backend/app/services/announcements.py`、新增 `backend/tests/test_announce_reliability_a.py` 和本文档。已有测试未修改。
- 未修改 UA、请求头、接口、分类列表、其他交易所逻辑或 `announcement_polling.py`，未加入任何绕过风控的处理。

## 实现与关键入口

- `GATE_FETCH_BUDGET_SECONDS = 17.0`；`GateAnnouncementProvider.fetch` 使用注入的单调时钟计算截止时间。每个分类开始前剩余不足一秒就停止后续分类；reader 前不足一秒直接保留并返回已获取的分类结果。
- reader 的 HTTP 超时为 `min(15.0, remaining - 0.5)`，连接超时不超过该值或原有三秒；两个 reader 的 `gather` 使用 `asyncio.timeout(remaining)`。预算超时只记 debug，保留分类结果；外部取消正常传播，reader 子任务一起取消。
- `GateAnnouncementProvider._fetch_reader_page` 用 `_category_failures[("reader", category)]` 存储异常类型及 HTTP 状态签名，与分类失败互不覆盖。首次或签名变化记录带 traceback 的 warning，重复记录 debug，恢复只记录一次 info。
- `_announcement_source_error` 将 HTTP 错误压缩为 `HTTP <status>`，其他错误为异常类型及单行消息，总长最多 200 字符。
- `OKXAnnouncementProvider.fetch` 每轮刷新 `last_source_errors`，记录异常和空结果来源；仅在原先应抛错的条件下附加逐来源摘要。原先按 slug 去重的行为不变。
- `BitgetWebsiteAnnouncementProvider.fetch` 丰富已有 `last_source_errors` 并在全部失败的异常中追加摘要；中英文合并、来源文章记录和空页面视为成功的规则不变。Bitget 公共 API provider 未改动。

## 测试与审查

- 先写新测试并在实现前运行，修正测试 HTML 以符合既有解析规则后，红灯结果为 `15 failed, 3 passed in 3.23s`。
- 全部新请求使用 `httpx.MockTransport`，Gate 使用注入时钟。慢 reader 使用真实 asyncio 超时和不主动结束的 mock 请求，验证返回分类结果及子任务取消。
- 覆盖分类提前耗尽 / 全部分类后耗尽、reader 超时、分类与 reader 阶段的外部取消、重复失败 / 签名变化 / 恢复、全来源 HTTP / 网络 / payload 失败、部分成功合并及错误状态清除。
- 指定回归加新测试：`python -m pytest -q tests/test_announce_reliability_a.py tests/test_announcements.py tests/test_announcement_public_sources.py tests/test_gate_announcement_noise.py --basetemp=../.pytest-tmp/regression`。
- 回归结果：`101 passed in 3.00s`。
- 全量：在 `backend` 下用 PowerShell `New-Item -ItemType Directory -Force ../.pytest-tmp` 创建目录（等价于要求的 `mkdir -p ../.pytest-tmp`），再执行 `python -m pytest -q --basetemp=../.pytest-tmp/x`。
- 全量原始汇总：`2247 passed, 6 deselected, 12 warnings in 151.10s (0:02:31)`，退出码 0。12 条 warning 来自既有 API / 指数成分测试中 `datetime.utcnow()` 的弃用提示，不在本次修改范围。
- 既有 `conftest.py` 阻止外部网络；pytest 默认排除 network / benchmark 标记。全程未使用 WSL、未安装依赖、未联网。
- `git diff --check` 已通过；已逐段审查超时 / 取消路径、诊断对原合并行为的影响及文件边界。

## 交付与线上状态

- 用户要求仅离线实现并提交；提交信息为 `fix: keep gate partial results within fetch budget and record source failures`，末尾添加指定 Co-Authored-By 行。
- 本文档与实现、新测试一并提交；最终提交号以包含本文档的 Git 提交及交付回复为准。
- 遵守禁止联网的要求：未推送、未访问服务器、未备份线上数据库、未部署，也未做线上验收。任务书提到的生产基线未在线复核。

## 已知边界与后续

- 严格按 §2，分类阶段只在请求前检查预算，没有新加对已经进行中的分类请求或详情请求的强制总超时；因此 17 秒不是任意网络情况下整个 `fetch(include_details=True)` 的硬时限。
- reader 总超时时保留已累积的分类结果；没有额外引入单个 reader 先完成结果的收集机制。
- 初始无用户未提交文件或未跟踪文件。测试期间生成 `.pytest-tmp` / pytest 缓存等运行产物，不纳入提交，不清理无关证据。
- 后续上线需单独获得批准，再按项目部署文档完成推送、备份、部署和线上接口验收。其他公告可靠性模块应在新任务中继续。
