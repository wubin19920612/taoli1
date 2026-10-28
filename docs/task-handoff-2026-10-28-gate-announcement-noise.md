# Gate 公告失败降噪交接（2026-10-28）

## 目标、范围与基线

- 模块：新币公告中的 Gate 直连失败日志去重与 300 秒退避。
- 任务书：`docs/gate-announcement-noise-spec-2026-10-28.md`，用户已确认。
- 工作区：`.worktrees/gate-announcement-noise`；分支：`codex/gate-announcement-noise`。
- 开始时 HEAD：`4887e877a81e3f13af9d55d0625803b4d26b0bf2`，工作区干净。
- 任务书记载生产基线为 `4ad88b7`；本地 HEAD 比它多一条任务书文档提交。本次没有连接生产服务器核验版本。
- 仅交付以下三个文件：
  - `backend/app/services/announcements.py`
  - `backend/tests/test_gate_announcement_noise.py`
  - 本交接文档

## 实现与关键入口

`backend/app/services/announcements.py` 仅新增模块常量 `GATE_DIRECT_BACKOFF_SECONDS = 300`，并修改 `GateAnnouncementProvider` 的构造函数和 `fetch`：

- 实例维护 `_category_failures: dict[str, str]`。签名为异常类型名，HTTP 状态异常另附状态码，例如 `HTTPStatusError:567`。
- 某分类首次失败或签名变化时记录带 traceback 的 WARNING；相同签名重复失败仅记录不带 traceback 的 DEBUG。
- 分类恢复成功时记录 `gate announcement category recovered: %s` 的 INFO，并清除该分类的失败记录；恢复后再次失败会重新记录 WARNING。
- 五个直连分类全部失败时设置 `_direct_retry_at`；300 秒内跳过所有直连请求，每轮仅记录一条退避 DEBUG，并继续原有读取器两页回退。
- 到期时按原顺序重试全部直连分类。任意分类成功即清除退避；全部再次失败则重新退避 300 秒，保留已有失败签名以继续去重。
- 通过新增可选构造参数 `monotonic_fn` 注入单调时钟，默认 `time.monotonic`；原有 `client`、`now_fn` 调用兼容。

重要行为边界：

- 部分直连成功时不进入退避，保持原有请求顺序和公告业务字段。
- general 成功时不调用读取器；general 失败或处于直连退避期时，仍调用 general、dividenddistribution 两页读取器。
- User-Agent、所有请求头、请求地址、详情抓取、解析、读取器失败日志均未修改；未增加任何绕过风控的处理。
- 未修改其他交易所、轮询间隔或 Binance 429 处理。
- 去重和退避状态属于 provider 实例内存，实例重建后重新记录首次失败。

## TDD 与测试结果

全程使用 Windows 原生 Python 3.12.10，未运行 WSL。先创建新测试并运行 RED，再修改业务代码。新测试使用 `httpx.MockTransport` 和 `caplog`，不访问外网。

新文件共 7 个参数化测试实例，覆盖任务书 §5 测试 1–4：

1. `fetch`、`fetch_headlines` 的全部 567、连续三轮、300 秒边界、到期重试及再次退避；逐分类首条 WARNING 带 traceback，退避期零直连请求，读取器返回业务字段一致。
2. 到期后单分类恢复，INFO 仅一次，清除失败记录及退避，下一轮继续请求全部分类；恢复后重新失败重新 WARNING。
3. 567 变 ConnectTimeout 或 503 时重新 WARNING，之后相同签名仅 DEBUG。
4. 仅 delisted 或仅 general 失败时，三轮均请求全部分类，不进入退避；首轮 WARNING、后续 DEBUG，结果及读取器调用符合原有行为。

公告模型每次解析生成随机存储 `id`；测试比较除该字段外的全部模型字段及列表顺序。

在 `backend` 目录依次运行：

```powershell
python -m pytest -q --basetemp=../.pytest-tmp/x tests/test_gate_announcement_noise.py --tb=short
```

RED：业务实现修改前 **7 failed**。5 个用例因尚无时钟注入参数失败，2 个部分失败用例复现旧实现重复 WARNING。

```powershell
python -m pytest -q --basetemp=../.pytest-tmp/x tests/test_gate_announcement_noise.py tests/test_announcements.py tests/test_announcement_public_sources.py
```

GREEN：**80 passed in 1.66s**。

```powershell
python -m pytest -q --basetemp=../.pytest-tmp/x
```

后端全量：**2061 passed, 4 deselected, 12 warnings in 125.30s**。4 个用例由仓库默认标记配置排除；12 条警告为 API、指数成分测试中的 `datetime.utcnow()` 弃用提示。

另完成 `git diff --check` 和 AST 范围复核：除 Gate 构造函数、`fetch` 和一个模块常量外均与开始时 HEAD 一致；直连 `client.get` 参数也完全一致。

## 本地交付、未跟踪文件与未做事项

- 提交标题按用户指定：`fix: dedupe gate announcement failures and back off direct api`。
- 正文末尾 trailer：`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。
- 本文档与代码、测试一并提交；提交号可用当前分支 `git log -1 --oneline` 查询。
- `.pytest-tmp/x/` 是本次按指定 basetemp 生成的未跟踪测试产物，保留在本地，不暂存、不提交。
- 按用户要求未 push；未备份生产数据库、构建镜像、部署或进行线上接口及一小时观察验收。本次只能确认本地测试结果。
- 未修改 `docs/next-task.md` 或其他交接文件，遵守仅三个文件的范围约束。
- 没有已知未解决的本地测试失败。Gate 直连被拒绝的原因及生产读取器可用性未在本次现场验证；本次实现处理失败后的日志和重试频率。

如继续上线，请另开服务器运维任务，先按任务书取得部署批准并复核实际生产版本、备份及发布流程，再单独观察一小时：分类失败 WARNING 数量、Gate 公告最新入库时间。其他功能模块也应在新任务继续。
