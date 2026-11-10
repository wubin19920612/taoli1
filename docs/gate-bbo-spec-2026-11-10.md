# Gate 实时买一卖一价（gate-bbo）任务书 2026-11-10

设计稿（必读，业务规则以它为准）：`D:/吴彬工作/claude/套利/research/small-lot-quant/quote-freshness-design.md`。用户已按建议拍板 D1–D6：
- D1：用 WS book_ticker。
- D2：放在 spawn 子进程，200ms 批量回传。
- D3：订阅成交额 ≥500 万美元且跨所有同币的市场，合约、现货各 ≤300。
- D4：不做 P0 止血，本任务不加 SHADOW_EXCLUDED_EXCHANGES。
- D5、D6：与本任务代码无关。

本文件只写工程约定。

## 1. 参照实现（先读）
- `backend/app/exchanges/hyperliquid_bbo.py`：只读内存缓存，不发 REST；`set_coins` 保留已订阅；单条报价有效期 120 秒，连接要活着；统计日志格式。
- `backend/app/exchanges/hyperliquid.py`：采集时怎么用 bbo 覆盖买一卖一价，怎么设 `quote_time_source`。
- 影子子进程：`services/shadow_runtime.py`、`shadow_worker.py`、`shadow_ipc.py`。看 spawn、IPC、子进程守护、RSS 上限、有界关闭（`SHADOW_SHUTDOWN_TIMEOUT_SECONDS`，总关闭 ≤6 秒，Docker 宽限 10 秒），以及 `main.py` lifespan 的接入方式。
- `backend/app/exchanges/gate.py`：现有批量 tickers 采集。
- `services/spread_engine.py` 的 `_has_executable_book`，以及 `services/fake_opportunity.py` 的 `prepare_markets`：estimated 报价在这里变成不可执行。

## 2. 要做的
1. **子进程侧**（新文件，例如 `exchanges/gate_bbo.py`）：
   - 两条 WS：合约 `wss://fx-ws.gateio.ws/v4/ws/usdt` 的 `futures.book_ticker`，现货 `wss://api.gateio.ws/ws/v4/` 的 `spot.book_ticker`。
   - 订阅：每 100 个市场一批，批间隔 200ms；断线按 1/2/5/10/30 秒重连；每 30 秒发一次 `futures.ping` / `spot.ping`。
   - 推送字段：`t`（毫秒）、`u`、`s`、`b/B`、`a/A`。价、量要是有限正数；空串或 0 视为没有该侧报价。
   - 只保留每个市场的最新值。每 200ms 把有变化的市场批量发给主进程，单批 ≤2000 条，同时带上每条连接最后收到消息的时间。
2. **主进程侧**（新文件，例如 `services/gate_bbo_runtime.py`）：
   - 用 spawn 拉起子进程，接收批量数据并更新内存字典。**主进程不解析逐条推送**。
   - 子进程崩溃时按 1/2/5/10/30 秒退避重启，期间报价视为不新鲜。
   - 子进程 RSS 超过 `GATE_BBO_CHILD_MAX_RSS_MB`（默认 120）就重启。
   - 订阅名单由主进程每 600 秒用最新采集结果计算，再发给子进程。规则：Gate 上 `volume_24h_usdt` ≥ `GATE_BBO_MIN_VOLUME_USD`（默认 5000000），且同一 symbol 在非 Gate 交易所也有市场；按合约、现货分开，各取 ≤ `GATE_BBO_MAX_FUTURES` / `GATE_BBO_MAX_SPOT`（默认 300）；已订阅的优先保留。
   - 关闭与影子并行，自身 ≤2 秒，总关闭仍 ≤6 秒。
3. **合并规则**（改 `exchanges/gate.py`，合约、现货都改）：
   - 买一卖一价新鲜时：用 WS 值覆盖 bid/ask/bid_size/ask_size，`upstream_timestamp = t`（UTC），`quote_time_source="ws"`。
   - “新鲜”同时满足：该连接 5 秒内收到过消息；`now - t` 在 [-2, `GATE_BBO_QUOTE_MAX_AGE_SECONDS`=120] 秒内。
   - 不新鲜、开关关闭或子进程不在时：保留批量价，但设 `is_estimated=True`、`estimated_fields` 含 `"bid"` 和 `"ask"`、`quote_time_source="received_batch"`。
   - 回滚开关 `GATE_BATCH_QUOTES_EXECUTABLE`，默认 0。设为 1 时完全恢复今天的行为：不标 estimated，`quote_time_source` 照旧。
   - 不新增 `MarketSnapshot` 字段。
4. **配置与观测**：
   - 配置写进 `core/config.py`，带校验。`GATE_BBO_ENABLED` 默认 0。
   - 每 300 秒打一行 JSON 日志 `{"gate_bbo":...}`，含：每条连接的状态、订阅数、消息/秒、重连次数、批大小、子进程 pid、RSS、CPU 秒、重启次数、合并命中数和回退数。
   - `/api/health` 增加 gate_bbo 摘要；参照 HL bbo 在 health 里的做法，没有就加在同一位置。

## 3. 约束
- **可改**：上面列出的文件、`main.py` 的 lifespan 和 health、`core/config.py`、新测试。
- **不改**：影子账本和告警的业务逻辑、`orderbook_validator.py`、`spread_engine.py` 的判定、compose、部署脚本、`.env`、前端。
- 已有测试和断言不得放宽。不 push、不 SSH、不跑 WSL。按 test/feat/docs 分开 commit。
- 允许联网（只用公开 WS 和 REST）录制真实推送作夹具，放 `backend/tests/fixtures/gate_bbo/`。**单元测试不得联网**。

## 4. 测试
- 解析：夹具，含空侧、非法值、ack 和 error 消息。
- 合并：新鲜时覆盖；超时、连接静默、开关关闭时回退并标 estimated；`GATE_BATCH_QUOTES_EXECUTABLE=1` 时与改前行为逐字段一致。
- `prepare_markets` 和 `_has_executable_book`：Gate 回退报价判为不可执行，ws 报价判为可执行。
- 订阅名单：门槛、跨所条件、上限、保留已订阅。
- 子进程：spawn 后能收到批量数据；单批上限；崩溃后退避重启；RSS 超限重启；关闭 ≤2 秒；与影子一起关闭总 ≤6 秒。
- 守护：AST 或计数测试，保证主进程代码路径不调用逐条推送的解析函数。
- 测试环境：用锁定依赖的 venv
  `C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/435b98f6-0522-4055-9b28-46a94def1380/scratchpad/venv-lock/Scripts/python.exe`
  在 `backend` 目录、`PYTHONPATH=.` 下跑：
  `-m pytest tests -q -p no:cacheprovider --basetemp=../.pytest-tmp/gate-bbo-full`
  贴原始汇总行，并单独贴新测试的汇总行。
- 联网冒烟一次：子进程订阅 AKE_USDT、JCT_USDT、BTC_USDT 的合约和现货，跑 30 秒，打印批次数、消息/秒、`now - t` 的中位和 p90。

## 5. 交付
交接文档 `docs/task-handoff-2026-11-10-gate-bbo.md`，写清：文件清单、提交 hash、配置项、合并规则、关闭时间实测、冒烟结果、剩余风险。
