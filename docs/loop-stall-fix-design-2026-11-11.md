# taoli1 主循环卡顿修复设计（2026-11-11）

状态：待拍板（§6）。未编码。生产 51d2226 + `.env` 中 `GC_THRESHOLDS=10000,20,50`。

## 1. 现状与证据

- 卡顿判定：主循环连续阻塞 >1 秒（`LOOP_STALL_THRESHOLD_SECONDS=1.0`）。实际卡顿都在 1.0–1.5 秒，刚过线。
- 调 GC 参数后（11-10 20:28 起 11.6 小时）：
  - 完整垃圾回收（gen2）从每小时 60 次降到 13 次，单次 p50 446ms、max 720ms。
  - 卡顿约 1.7 次/小时，20 次里只有 2 次碰上 gen2。
- 剩下的卡顿来自**一段同步计算本身就接近或超过 1 秒**，中途不让出主循环；碰上一次 0.2–0.7 秒的回收就过线。
- py-spy 采样 90 秒：主线程忙 45%，其中 `market_validation_review` 占 37%，`compact_markets` 占 11%，交易所 JSON 解析占 9%。
- 卡顿时的栈顶分布：

| 位置 | 是否让出主循环 | 问题 |
|---|---|---|
| `collector.py:262-276` 构建阶段，对每个机会调 `validate(a, b)` | 有 `maybe_yield`，但单次 `validate` 偏重 | 见下文“重复计算” |
| `market_validation_review.filter_opportunities`（告警循环 `main.py:695`） | **完全同步** | 复核结果过期时，3400–4000 个机会一次性全部重算；还要重建 1.25 万行情的 `_market_index` |
| `prepare()` 第 450–475 行，按资产分组两两组合找大价差候选 | **组合循环里不让出** | 大币种组可能有几十个市场，组合数是平方级 |
| `compact_markets.pack`（`collector.py:476`，每个交易所结果） | **完全同步** | 一个交易所几千行，逐字段复制 |
| `multi_exchange_premium._scan_eligibility` | **完全同步** | 遍历全部行情 |

**重复计算**（`validate()`，`market_validation_review.py:734`）
1. 两腿已经在 `prepare()` 里标注过，`validate()` 又各调一次 `annotate()`。这意味着 `registry.annotate`、汇率换算，再加两次 `model_copy`。同一个市场出现在几十个机会里，就要重复标注几十次。
2. `quote_fingerprint(a, b)`（排序、`json.dumps`、sha256）在 `validate()` 里算一次，`validate_pair()` 第 181 行又算一次。
3. 告警循环在复核结果过期时把采集循环刚做过的复核再整套做一遍。

## 2. 目标

1. 主循环上不再有超过 200ms 不让出的同步段。
2. 卡顿 ≤0.5 次/小时（现在约 1.7 次/小时）；告警 P50 不变差。
3. 复核结果（放行、拦截、原因码、各项能力）与改前**逐字段一致**，`validated_at` 除外。

不做：减少每轮重建的对象数量（改快照结构，范围太大），升级 Python 版本。

## 3. 方案

### F1 去掉重复计算（核心，只改 `market_validation_review.py`、`pair_validation.py`）
- `validate()` 新增关键字参数 `annotated: bool = False`。
  - 为 True 时不再调 `annotate()`，直接使用传入的行。
  - 只有采集循环构建阶段传 True：那时的行来自本轮 `prepare()`，而且同一个 `_mutation_epoch` 内的结果相同。
  - 如果调用时的 epoch 和 `prepare()` 时不同，退回完整路径。
- `validate_pair()` 新增可选参数 `quote_fingerprint`；调用方已经算过就直接传入，不再重算。
- 告警循环 `filter_opportunities` 先用快照里已有的 `item.validation`（现有逻辑）。过期需要重算时，从 `store` 取的行已经标注过，也走 `annotated=True`。
  - 前提：`store.get_all_markets()` 存的是 `prepare()` 的输出（`collector.py:290` 的 `aliased_markets` 就是它）。实现时要核对，并写测试固定这一点。
- `_market_index()` 按快照发布版本缓存：`store` 发布时版本号 +1；版本不变就复用，不再每次重建 1.25 万条。

### F2 让出主循环（只加让出点，不改业务结果）
- `filter_opportunities` 新增 `async` 版本 `filter_opportunities_async(..., budget)`，每个机会调一次 `maybe_yield()`。告警循环改用它。同步版保留给其他调用方。
- `prepare()` 的大价差候选组合循环（450–475 行）和 collision 无效化循环，加 `maybe_yield()`。只在 `cooperative` 尝试里加；第三次尝试保持无 await，这是现有的设计约定。
- `compact_markets.pack` 新增 `pack_async(markets, budget)`，每 N 行（例如 200）检查一次预算；`_store_adapter_result` 改用它，输出与 `pack` 完全一致。
- `multi_exchange_premium._scan_eligibility`：在调用方加让出点，或改成 async 版本。实现时先看调用链，挑改动小的做法。

### F3 观测
- 采集循环的 `stage_timings` 已经有 prepare/build/publish 三段。告警循环新增一行计时日志，含 `filter_ms` 和重算条数，每 300 秒一行。
- 部署后用 py-spy 采样 90 秒，对比 `market_validation_review` 的占比（现在是 37%）。

## 4. 验收

| 项 | 标准 | 方法 |
|---|---|---|
| 结果一致 | 用真实规模的假快照（约 1.2 万行情、约 4000 机会，含 collision、USDC 汇率、Gate `received_batch`、过期绑定），新旧路径的 `PairValidationResult` 除 `validated_at` 外逐字段相等；`filter_opportunities` 新旧版本输出相同 | 新增对照测试 |
| 让出主循环 | 假时钟下，`filter_opportunities_async`、`pack_async` 和 `prepare` 组合循环都会按预算让出；`pack_async` 与 `pack` 输出逐字节相同 | 单元测试 |
| 耗时 | 同一份假快照，构建阶段复核总耗时和告警循环重算耗时都至少下降 40%（锁定 venv 跑 3 次） | 基准测试，交接文档贴数字 |
| 生产 | 卡顿 24 小时 ≤0.5 次/小时；告警 P50 不变差；可用内存 >400MB | guard.json、alert_timing、free |
| 现有测试 | 全量通过，不放宽断言 | 锁定 venv |

## 5. 上线顺序

1. Codex（gpt-6-astra high：跨模块，涉及复核业务）编码，然后只读 Codex 审查。复核属于告警链路，Claude 亲自看核心 diff。
2. 部署时只重启 backend，GC 参数保持 50。fill-sim 期间如果 API 短暂不可用，会计入 `api_failures`，只要不连续失败就不会停。
3. 观察 24 小时。如果卡顿 ≤0.5 次/小时，再决定 GC 参数要不要改回 10（省内存约 90MB）。

## 6. 决策点

| 编号 | 问题 | 选项 | 建议 |
|---|---|---|---|
| D1 | 范围 | A. F1+F2+F3 一起做；B. 只做 F2（只加让出点，最安全，但总 CPU 不降） | **A**：F1 能直接减少约一半的复核 CPU，2 核机器上能顺带改善告警耗时 |
| D2 | 告警循环改成 async 过滤 | A. 改（要动 `main.py` 告警循环一行调用）；B. 不改，只靠 F1 降低成本 | **A**：这是唯一完全同步、规模随机会数增长的路径 |
| D3 | 部署时机 | A. 编码验收完就部署（fill-sim 会短暂断约 20 秒）；B. 等 11-17 fill-sim 报告后再部署 | **A**：卡顿多会让 fill-sim 被守护停掉，影响更大 |
| D4 | GC 参数 | 先保持 50，修复上线 24 小时后再评估 | **按建议** |
