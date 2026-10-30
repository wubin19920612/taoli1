# Gate 公告解析卡住事件循环：别名正则预筛（2026-10-30）

## 背景（生产 54b9088，只读调查已确认）
- 24 小时内事件循环卡住（loop_stall）5 次，每次 2.0–2.6 秒。栈都一样：`announcement_polling.py:96 poll_source → announcements.py:2173 fetch_headlines → fetch → _parse_page → _announcement(1269) → extract_event_schedule(720) → _alias_pattern(702) → re.compile`。
- 根因：`_schedule_alias_groups` 给每个币种生成约 16 个别名，`_alias_pattern` 为每个别名临时 `re.compile(rf"(?<![A-Z0-9]){re.escape(alias)}(?![A-Z0-9])", re.I)`。用 4 个真实 Gate 分类页（52 条）在本地测：一轮约 1.0 秒，0.85 秒花在编译正则上，3769 次编译中 3721 次没命中缓存。一轮有 3255 个不同的别名，超过 Python 正则缓存（512），所以第二轮也一样慢。
- 只加 `lru_cache(4096)` 的本地实测：缓存热了以后一轮 0.05 秒，但冷启动的第一轮仍要 0.5 秒，不够。
- `_announcement` 是所有交易所 provider 共用的基类方法，修好后所有公告源都受益。

## 要做的事
1. **别名预筛（主修复）**，在 `backend/app/services/announcements.py` 中：
   - `extract_event_schedule` 每次调用只算一次 `folded = normalized.casefold()`。
   - 处理每个别名前，先检查 `alias.casefold() in folded`；不包含就直接跳过，不编译、不匹配。
   - 通过预筛的别名仍然用**原来的正则**（模式、标志都不改）做 `finditer`，后续逻辑一字不改。
   - `_alias_pattern` 加 `functools.lru_cache(maxsize=1024)`（必须有上限，不允许无上限缓存）。
   - 写注释说明：预筛是正则 `re.I` 匹配的超集，所以结果不变。
   - 保守处理：别名含非 ASCII 字符（如 `USDⓈ`）时不做预筛，直接走原正则（这类别名数量很少，不影响性能）。
2. **Gate 不取详情时不重复解析**：`GateAnnouncementProvider.fetch` 里，当 `include_details=False` 或 `content_by_key` 为空时，直接复用已经解析出来的 `page_rows`，不再调第二次 `_parse_page`。有详情内容时保持现状。
3. 不改别名生成规则、时间提取规则、分类规则，也不改其他 provider 的行为和输出。

## 验收（测试，加到 backend/tests/test_announcements.py）
- **等价性**：在测试里保留旧实现作为参照（直接用原正则做 `finditer`），对一组语料比较新旧 `extract_event_schedule` 的输出，必须完全相同。语料至少包括：现有测试里的中英文公告；大小写混写（`btc/usdt`、`Btc Usdt`）；前后紧贴字母或数字（`XBTCUSDT`、`BTCUSDT2`，不应匹配）；`USDⓈ`、`USD1`；`/` 和空格形式；同一币种出现多次；带全角字符和中文标点的文本；含 `ſ`（U+017F）、`K`（U+212A 开尔文符号）这类 `re.I` 会当成 ASCII 字母的字符；单字符币种（plain 别名为空）。
- **预筛真的生效**：用 monkeypatch 统计 `re.compile` 或 `_alias_pattern` 的调用次数。文本里只出现 1 个币种、但传入 50 个币种时，编译次数不超过 1 个币种的别名数。
- **缓存有上限**：断言 `_alias_pattern.cache_info().maxsize == 1024`。
- **Gate 不重复解析**：`include_details=False` 时，每个分类只调用一次 `_parse_page`（用 spy 断言）；`include_details=True` 且有详情内容时，行为与现在一致（已有测试保持通过）。
- 已有公告测试一个都不能放宽或删除。

## 范围
- 可改：`backend/app/services/announcements.py`、`backend/tests/test_announcements.py`、本任务书（末尾追加交接记录）。
- 不可改：其他所有文件。
- 提交信息：`perf: prefilter announcement alias regexes and skip duplicate gate parse`。
- 基线：分支 `codex/gate-loop-stall`，基于 `39e8301`（部署分支最新；与 54b9088 相比没有改动公告文件）。

## 测试环境（必须）
CI 用 `backend/requirements.lock` 锁定依赖（FastAPI 0.162）。在 `backend` 目录跑全量测试：

```powershell
& 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad\venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider
```

注意：这个 venv 是用 `pip install -e` 指向 `.worktrees/deploy-s7b/backend` 装的。在本工作树跑测试时要确保导入的是本工作树的代码：在 backend 目录运行，并先跑一次 `python -c "import app.services.announcements as a; print(a.__file__)"`，确认路径在 `gate-loop-stall` 下；不对的话就用 `$env:PYTHONPATH = (Get-Location).Path` 指过来。
日志里保留：执行的命令、上面这条路径的输出、汇总行。不要运行 WSL；不要访问生产环境或真实的交易所、Gate、飞书；**不要 git push**，只在本地分支提交。

## 输出（最后给我）
改动文件清单；预筛为什么是正则匹配的超集（一两句）；等价性测试覆盖了哪些语料；全量测试命令和汇总行。

## Claude 验收（Codex 不用做）
- 用会话 scratchpad 里的 4 个真实 Gate 页面跑基准：冷启动一轮 ≤ 0.1 秒（现在是 1.0 秒），新旧解析结果的字段完全一致（比较时排除抓取时间等随时间变化的字段）。
- 上线后 +1h：`announcements.py` 栈里不再出现 loop_stall；Gate 和其他公告源都在正常更新。

## 本地交接记录（2026-10-30）

- 模块与范围：公告别名正则预筛、Gate 分类页重复解析；只修改本任务书、`backend/app/services/announcements.py`、`backend/tests/test_announcements.py`。
- 分支与基线：`codex/gate-loop-stall`，基线 `39e830186541e78657e99f159d8e2e18c3e51096`。按任务书指定信息创建本地提交，不推送。
- 实现入口：`_alias_pattern` 使用 `lru_cache(maxsize=1024)`；`extract_event_schedule` 每次只计算一次 `normalized.casefold()`，不存在的 ASCII 别名跳过编译和匹配；`GateAnnouncementProvider.fetch` 在详情字典为空时复用首次解析的 `page_rows`，非空时保持原有重新解析路径。
- 等价性边界：预筛不检查字符边界，ASCII 字母的 casefold 包含关系覆盖原正则匹配（含 `ſ`、`K`）；最终仍由原模式和 `re.I` 判断。非 ASCII 别名直接使用原正则。另外，文本含 `İ`/`ı` 时也保守跳过预筛，因为这两个字符被 `re.I` 当作 ASCII I，而 casefold 不保证相同的包含关系。别名生成、时间提取及分类逻辑未改。
- 等价性语料：保留旧版正则循环作为测试参照，31 组语料逐项比较完整输出，并校验参照输出的币种列表以避免空结果掩盖问题。覆盖原中英文多币公告、大小写混写、前后字母/数字边界、USDⓈ/USD1/USDC/USD、斜杠/空格/连写、重复币种和多次出现、全角字符和中文标点、`ſ`/`K`/`İ`/`ı`、plain 别名、单字符币种、带报价币的输入、输入顺序、非 ASCII 币种、HTML 实体和空白、无交易提示/无时间/空输入。
- 预筛计数的限制与验证：现有别名生成器会为每个普通币种生成 3 个非 ASCII 的 USDⓈ 别名。因此，“非 ASCII 别名直接走原正则”和“50 币种冷缓存总编译数不超过单币别名数”无法同时成立。测试保留生成规则和保守兜底：在 49 个缺席币种加 BTC 的冷缓存场景，ASCII 正则调用恰好 1 次，非 ASCII 调用为 `49 * 3 = 147`；另一个测试只预热非 ASCII 缓存、保持 ASCII 缓存为空，此时总 `re.compile` 调用恰好 1 次，不超过单币别名数。两种测试都能检出删除预筛的回归；没有把冷缓存总成本记为 1 次。
- Gate 验证：MockTransport 与解析 spy 覆盖全部 5 个分类；`include_details=False`、`include_details=True` 且正文为空时每分类仅解析一次，并按对象身份确认复用；正文非空时每分类解析两次，使用详情中的 11:00 时间覆盖列表中的 10:00，详情请求行为保持一致。
- 测试环境：使用任务书指定的虚拟环境；FastAPI `0.162.0`，已安装的锁定依赖版本与 `backend/requirements.lock` 全部一致，锁文件中的 `uvloop` 在 Windows 下未安装。未安装或更新依赖。使用 `PYTHONDONTWRITEBYTECODE=1` 避免生成字节码；无需设置 `PYTHONPATH`，实际导入已指向本工作树。

在本工作树 `backend` 目录执行的导入确认及全量测试命令：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad\venv\Scripts\python.exe' -c "import app.services.announcements as a; print(a.__file__)"
& 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad\venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider
```

导入路径输出：

```text
C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\gate-loop-stall\backend\app\services\announcements.py
```

- 回归验证：新增缓存及 Gate 断言在实现前检出了缺失缓存和重复解析；31 组等价性语料在新旧实现中均通过。首次公告测试为 `97 passed in 1.37s`（随后补充 3 个预筛调用/编译测试）。AST 对比确认原有 54 个顶层测试/辅助定义未修改或删除。
- 全量汇总：`2527 passed, 6 deselected, 13 warnings in 140.72s (0:02:20)`，退出码 0。使用项目默认的 `not network and not benchmark` 选择条件，6 项因此排除；13 条警告为 FastAPI/Starlette TestClient 的 httpx 弃用提示及 `datetime.utcnow()` 弃用提示，未扩大范围修复。
- 工作区检查：`git diff --check` 通过；初始仅本任务书未跟踪，该文件随实现纳入提交，未发现其他未跟踪文件或范围外改动。
- 线上与后续：本任务只做本地实现和测试；未运行 WSL，未访问生产、交易所、Gate、飞书或其他外网，未推送或部署。真实 Gate 页面的冷启动基准及上线后 +1h 观察仍由 Claude 按任务书验收。
