# M2-1 交易可用性缓存治理交接（2026-10-25）

## 目标、范围与基线

- 任务书：`docs/m2-1-trade-availability-cache-spec-2026-10-25.md`，含 §3.1 RuntimeError 只读调查。
- worktree：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\m2-1-cache`。
- 分支：`codex/m2-1-cache`；起始提交：`fb0f3c6a0df2241f185a6e65472a5ab3598cc39e`。
- 开始时工作区与暂存区干净，没有已有未跟踪文件。交付仅涉及下面三个允许文件。
- 用户明确要求本地 conventional commit，不 push、不部署，不运行 WSL 或 Docker。无需执行项目默认生产交付流程，也不更新本任务范围外的 `docs/next-task.md`。

## 改动与关键入口

1. `backend/app/services/trade_availability.py`
   - 构造函数仅新增缓存配置：keyword-only `max_entries=128`、`retention_seconds=120`，不新增环境变量；原四个位置参数保持兼容。
   - `_cache` 改为 `OrderedDict[str, tuple[datetime, datetime, Any]]`，保存 `(stored_at, expires_at, payload)`。
   - `_get_json` 仍以 `now - stored_at < timedelta(seconds=cache_seconds)` 判断命中，返回同一 payload 对象；命中和成功写入均移至 LRU 尾部。
   - 成功响应解析后记录时间；清理期限为 `stored_at + max(cache_seconds, retention_seconds)`。写入后在原 `_cache_lock` 内调用 `_prune`，先扫描删除所有过期项，再从 LRU 头淘汰至容量上限。不能假设 LRU 顺序也是过期时间顺序。
   - 请求、JSON 解析和重试等待仍在锁外；两次尝试、0.2 秒重试间隔、异常类型及异常链保持不变。失败不会写入；刷新失败也保留原条目，仍允许更长 TTL 的读取方按旧语义命中。
   - 同步只读 `cache_stats()` 返回 resident entries 及前缀计数，例如 `{"entries": 3, "keys_sample": [{"prefix": "binance:", "count": 2}, {"prefix": "url", "count": 1}]}`。交易所前缀只接受既有 `EXCHANGE_ORDER` 白名单；其他自定义键归为 `other`，HTTP(S) URL 归为 `url`，不输出 URL、币种、任意未知前缀或 payload。
   - `cache_stats()` 不清理、不更新 LRU、不返回内部可变对象；同一服务事件循环中同步执行，没有 await，不会与缓存修改协程交错。它不是跨线程访问接口。
2. `backend/tests/test_trade_availability_cache.py`
   - 19 个缓存用例，使用 fake clock 和 `httpx.MockTransport`；无真实 HTTP 请求或 TTL 睡眠。
   - 20 组只读调查用例：真实 `fetch_status`、匹配、诊断和 `TradeAvailabilityMonitor.check_once`，配合内存 SnapshotStore、fake 上游和 fake repository；不修改监控或业务代码。
3. 本交接文档。

AST 对比确认：既有方法只改变 `TradeAvailabilityService.__init__` 和 `_get_json`，只新增 `_prune`、`cache_stats`；其他类方法未变。构造函数的改动限于缓存参数和缓存字段，另加 `OrderedDict` 导入。

## TTL 调用点核对

任务书写的是 21 处；对当前基线及完成代码做 AST 计数，实际均为 **20 处调用**，其中 17 处默认 20 秒、3 处显式 60 秒：

| 调用函数 | 调用数 | cache_seconds |
| --- | ---: | --- |
| `_transfer_binance` / `_transfer_gate` / `_transfer_bitget` | 3 | 60 |
| `_info_binance`：spot exchangeInfo、spot ticker、future exchangeInfo | 3 | 默认 20 |
| `_info_okx` / `_info_bybit` | 2 | 默认 20 |
| `_info_gate`：spot / future | 2 | 默认 20 |
| `_info_bitget`：spot / future | 2 | 默认 20 |
| `_info_aster`：spot exchangeInfo、ticker；future exchangeInfo、ticker、premiumIndex | 5 | 默认 20 |
| `_info_arcus`：markets / feetiers | 2 | 默认 20 |
| `_info_lighter`：按实例 profile 查询 orderBookDetails | 1 | 默认 20 |

三处 60 秒请求可由以下固定 cache_key 入口定位：`binance:spot:transfer-assets`、`gate:spot:transfer:{asset}`、`bitget:spot:transfer:{asset}`。120 秒清理下限覆盖所有现有读取 TTL。不同 TTL 的对照测试直接使用旧命中表达式，覆盖 20→60、60→20、严格边界、零/负读取 TTL，以及清理期限已到但没有写入清理时仍按读取方 TTL 命中的情况。

## RED → GREEN 与验收

运行环境：Windows PowerShell、Python 3.12.10，直接使用本机 Python；未运行 WSL、Docker，未安装依赖或访问线上系统。

先仅添加缓存测试，在尚未修改服务时运行：

```powershell
cd backend
python -m pytest -q tests/test_trade_availability_cache.py
```

RED：**10 failed, 9 passed in 1.79s**。失败为原实现缺失 `cache_stats`、构造缓存参数；对应清理、容量、LRU、统计和失败重试观察断言。随后实现缓存，同一组 **19 passed in 0.97s**。再补充只读调查数据，调查用例 **20 passed, 19 deselected in 1.05s**。

最终验收：

| 命令（在 backend 下） | 实测结果 |
| --- | --- |
| `python -m pytest -q tests/test_trade_availability_cache.py tests/test_memory_probe.py` | **78 passed in 184.91s** |
| `python -m pytest -q tests/test_trade_availability.py tests/test_trade_availability_cache.py` | **48 passed in 130.74s**；包含真实 API 路由回归 |
| `python -m pytest -q tests/test_api.py -k trade_availability` | **110 deselected in 28.70s**；零用例选中，非通过结果 |

第二条实际通过 PowerShell 文件枚举展开通配符运行，避免 Python 在 Windows 中把 glob 当作字面路径：

```powershell
$testFiles = @(Get-ChildItem tests/test_trade_availability*.py | ForEach-Object { $_.FullName })
python -m pytest -q @testFiles
```

**验收命令的基线差异**：当前 `tests/test_api.py` 没有匹配 `trade_availability` 的测试，不能声称该过滤命令通过。实际交易可用性 API 用例是 `tests/test_trade_availability.py::test_trade_status_routes_return_exact_market_and_persist_watch`，已在上述 48 项中通过，覆盖状态查询、观察项创建/持久化、列出与删除。没有为使过滤命令变绿而修改不允许的测试文件或 pytest 配置。

其他检查：新增测试文件 Ruff 通过；`git diff --check` 通过。服务文件 Ruff 存在原有 `_info_arcus` 的 **TRY004**（当前 RuntimeError 提示类型），使用基线 Git blob 经 bytes stdin 再运行 Ruff，确认同一条告警原本就存在；按范围限制保留，未修复业务异常类型。

## §3.1 RuntimeError 只读调查

### 完整触发链

`TradeAvailabilityMonitor.run` 默认每 20 秒 → `check_once` 遍历 enabled watch → `fetch_status(watch.symbol, exchange, market_type, raw_symbol, dex)` → `_matching_markets` 读取 `SnapshotStore.get_all_markets()` → 逐候选诊断 → monitor 在 `result.markets` 中再按 exchange、market_type、raw_symbol.upper()、`dex or ""` 查找；找不到就抛出目标 RuntimeError，记录异常日志并更新 watch.last_error。

有三种不同阶段，日志末端相同：

1. **候选为空**：`_matching_markets` 首先要求当前 `market.symbol.upper() == normalize_pair_spread_symbol(watch.symbol)`，然后再筛交易所、类型、原始市场和非空 DEX。快照缺失、类型/原始市场错误、旧别名都会导致为空。
2. **候选诊断失败后被移除**：`fetch_status` 的 gather 使用 `return_exceptions=True`，异常只放入 `result.errors`，没有放入 `result.markets`。monitor 不读取这些 errors，最终以通用“未找到”覆盖了更具体的上游原因。例如 Hyperliquid `_diagnose_hyperliquid` 未收到指定 DEX 原始市场。
3. **候选和诊断都成功，monitor 二次匹配失败**：`fetch_status` 将 exchange strip/lower、raw_symbol strip/upper、dex strip/lower；monitor 对旧 watch 的 exchange 和 dex 做原值比较，raw_symbol 仅 upper、不 strip。Hyperliquid `_dex` 又按 raw_symbol 的冒号前缀派生 DEX，无冒号为 `main`；watch.dex 的 None/空串不等于 `main`。

`None` 与空串之间本身不会产生错误：monitor 对双方均使用 `or ""`。非 Hyperliquid 市场通常返回 None，None/空串都能匹配。Hyperliquid 的具体差异是 **None/空串 vs `main`**，不是 None vs 空串。普通 raw_symbol 大小写已经兼容。DEX 大小写则可能在 monitor 二次比较时失败。

### 可复制复现输入

所有输入和结果已固化在允许的新测试文件中：

```powershell
cd backend
python -m pytest -q tests/test_trade_availability_cache.py -k runtime_error
```

非 HL 基准快照：`{symbol: BTCUSDT, exchange: aster, market_type: future, raw_symbol: BTCUSDT, dex: null, bid: 100, ask: 101}`；基准 watch 使用同一 identity。fake HTTP 元数据返回 `symbols=[{symbol: BTCUSDT, status: TRADING}]`。没有订单簿适配器，真实诊断会保留行情回退行，不影响原始市场身份判断。

HL 基准：主 DEX 快照 `hyperliquid/future/BTCUSDT/raw_symbol=BTC/dex=null`，fake 上游返回 `raw_symbol=BTC, dex=main`；builder DEX 快照及 fake 上游为 `raw_symbol=xyz:BTC, dex=xyz`。fake action 设为 unknown，不发送恢复通知。

| case 参数 | 相对基准输入变化 | fetch_status markets | 目标 RuntimeError |
| --- | --- | ---: | --- |
| `baseline` / `raw_case` / `empty_dex` | 原样 / watch.raw_symbol=btcusdt / watch.dex="" | 1 | 否 |
| `missing_snapshot` | store=[] | 0 | 是 |
| `alias_changed` | 当前 snapshot.symbol=XBTUSDT、original_symbol=BTCUSDT、price_multiplier=1000；watch.symbol 仍 BTCUSDT，原始市场未变 | 0 | 是 |
| `alias_current` | 同上，watch.symbol 改为当前 XBTUSDT | 1 | 否 |
| `wrong_raw` / `wrong_exchange` / `wrong_market_type` | watch.raw_symbol=1000BTCUSDT / exchange=binance / market_type=spot | 0 | 是 |
| `raw_whitespace` / `exchange_case` | watch.raw_symbol=" BTCUSDT " / exchange=ASTER | 1 | 是 |
| `hl_main_none` / `hl_main_empty` | HL 主 DEX watch.dex=null / "" | 1 | 是 |
| `hl_main_explicit` | HL 主 DEX watch.dex=main | 1 | 否 |
| `hl_dex_case` / `hl_dex_explicit` | builder DEX watch.dex=XYZ / xyz | 1 | 是 / 否 |
| `hl_wrong_dex` | builder DEX watch.dex=other | 0 | 是 |
| `hl_upstream_missing` | 主 DEX watch.dex=main，但 fake HL 上游 markets=[] | 0 | 是 |
| `hl_delisted_present` | 主 DEX 上游仍有原始市场，is_delisted=true | 1 | 否，行状态为 DELISTED |
| `public_metadata_missing` | Aster 快照仍存在，HTTP 元数据 symbols=[] | 1 | 否，行状态为 NOT_FOUND |

`hl_upstream_missing` 的真实 `result.errors` 是：

```json
{"hyperliquid:future:main:BTC": "RuntimeError: Hyperliquid 公开接口未返回指定 DEX 原始市场"}
```

monitor 最终保存的却是 `RuntimeError: 聚合行情中未找到指定原始市场`。这说明日志本身不能证明交易所已下架，也不能证明纯粹是聚合快照缺失。

### 结论、最小修改建议与线上证据边界

- **已确认代码路径问题**：匹配使用可变的规范化 symbol，旧 watch 在别名调整后即使 exact raw market 还在也会失配；标准化规则在 fetch 与 monitor 不一致；诊断异常细节在 monitor 中丢失。这里只证明可复现条件，没有将缓存修改包装成这些问题的修复。
- `collector.py` 先 `apply_symbol_aliases`，再把 `aliased_markets` 发布到 `set_all_markets`；因此别名变化确实可以改变匹配字段。风险过滤后的列表单独放入 `set_markets`，不能把所有缺项简单归咎于成交量过滤。
- 当前 `routes_trade_availability.create_watch` 会标准化输入，并从诊断结果保存 canonical identity；缺省 HL DEX 的请求在其二次匹配处会被 404 拒绝。因此大小写/空格/缺省 DEX 的故障更应核对旧数据、迁移或其他写入路径，不能断言当前正常 API 会创建这些 watch。别名在创建后变化仍可触发失配。
- **最小修复建议（另开任务）**：当完整 exchange + market_type + raw_symbol + 可确定 DEX 被提供时，专用 exact-market 查询按这些字段选择唯一市场，避免旧 display symbol 额外挡住候选；保持实际 raw_symbol 与具体 DEX，拒绝歧义，绝不能退化为仅 ticker 匹配。缺省 HL DEX 只在与 raw_symbol 一致且无歧义时规范为 main/前缀 DEX；显式 DEX 冲突继续拒绝。
- 将 watch 创建与 monitor 二次匹配共用同一最小 identity 标准化规则（exchange strip/lower、raw strip/upper、DEX strip/lower 及上述 HL 规则）。普通交易所不要套用 HL main 规则，也不要自动忽略 DEX 冲突。
- 无市场但 `result.errors` 有对应 exact key 时，保留具体诊断原因；真正快照缺失与诊断失败分开处理。是否调整持续缺失策略应由后续任务决定，本次不改日志级别、不自动删观察项。
- **未确认线上根因**：本次没有读取线上 DB、watch 行、行情快照或原始 result.errors，也没有登录服务器。任务书所述每分钟约 4 次日志是背景输入，未在本次复测。后续需要在同一时间点对照四个 identity 字段、watch.symbol、当前快照 symbol/alias、候选数和 result.errors，才能把具体线上报错归入上述路径。

## 限制、线上状态与下一步

- 该方案约束条目数及成功写入时的保留期，**不是字节数硬上限**。单个大 payload 不做拒绝/裁剪，也没有后台清理定时器；完全没有成功写入时，旧条目可继续驻留。`cache_stats` 显示 resident 数，不偷偷清理。这与任务书指定的写入后清理机制一致。
- 默认最大读取 TTL 当前为 60 秒，低于 120 秒下限；未来增加更长读取 TTL 的调用时应复核下限。其他写入已清掉或 LRU 已淘汰的项会重新请求，不承诺跨淘汰命中。
- 没有增加同 key 并发请求合并，原有并发 miss 可分别请求的行为不变。
- 未修改前端、compose、部署脚本、其他服务或监控业务；未推送，未备份线上 DB，未部署，未做线上接口验收。无法声称已实现生产 RSS 降幅。
- 本地交付提交标题：`perf: bound trade availability cache`；可用 `git log -1 --oneline` 获取包含本交接的提交号。
- 完成后没有需保留的额外未跟踪产物。RED 结果和既有 lint 告警保存在本交接，未清理其他任务文件。
- 后续请在新任务中由 Claude 决定 RuntimeError 最小修复范围，并更正 API 回归的文件选择；部署审批与任务书 §5 的 1 小时内存观察仍由后续流程执行。M2-2 可调用 `cache_stats()` 获取脱敏分类计数。
