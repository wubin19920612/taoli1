# crossex-exec：同币跨所继承加密货币类别（2026-11-17）

## 问题
实盘 0 单。`tools/crossex_exec/universe.py` 的 `category()` 只会在 okx（instCategory=1）、bitget（isRwa=NO）、binance（子类白名单）上判出 `crypto`；gate、bybit、aster 永远是 `unconfirmed`，连 BTC 都是。`Universe.eligibility()` 要求两腿都确认，所以 gate/binance/bybit/aster 参与的组合全部以 `category_unconfirmed` 被拒（过去 6 小时 92 个合格信号里拒了 78 个：BLESS、EVAA、AKE、BTW、JCT、RARE 等，这些币在 bitget/okx 上都是 crypto）。

## 要做：在 eligibility 判断时加"继承"规则（不改 discovery 写入的原始 category）
对某一腿 instrument `i`，当且仅当下面全部满足时，按 `crypto` 处理：
1. `i.category == 'unconfirmed'`（原始元数据没给出任何类别；`listed_equity`/`commodity`/`pre_ipo` 一律不继承）。
2. 没有管理员决定覆盖：`universe_decisions` 里该 base 没有 `category` 也没有 `excluded`（现有优先级不变：excluded → 拒；FORBIDDEN/PRE_IPO/COMMODITY 别名 → 拒；管理员 markets 确认 → 用管理员；管理员 listed_equity → 拒）。
3. 当前 mapping 目录里存在至少一个 **okx 或 bitget** 的合约（market_type=future），`spec.base` 与 `i.spec.base` 完全相同、`category == 'crypto'`、未 `trading_blocked`。
4. 同一 `spec.base` 在目录中**没有任何**一个市场的 category 是 `listed_equity`/`commodity`/`pre_ipo`（有冲突就不继承，保持 fail-closed）。
5. `alias(i.spec.base)[0]` 不在 `PRE_IPO_BASES|COMMODITY_BASES`（现有检查保留）。
6. 只按 `spec.base` 精确相等；不得通过 alias 前缀（1000 等）或 canonical_base 映射去继承。

## 同名不同币的防护
任一腿是继承来的 crypto 时，开仓复核（拿到两边盘口之后、下单之前）要求两腿中间价（按现有 multiplier/contract 换算后的单位价格）相对偏差 ≤ 5%，否则拒绝，原因 `identity_price_mismatch`（加入 admission 的可预期拒绝原因列表，不触发停开锁、不告警刷屏）。若现有代码已有等价的价格一致性校验覆盖此情形，说明位置并复用，不要重复造。

## 可观测
- eligibility 结果里能区分来源（例如返回值或 universe 读接口里给出 `category_source: metadata|admin|inherited:<exchange>`）；不改既有返回结构的字段含义，只加字段。
- 继承不写 meta，不改变 universe_decisions，不改 mapping 落库内容。

## 不要做
- 不放宽 FORBIDDEN、PRE_IPO、COMMODITY、excluded、trading_blocked 等任何现有拒绝。
- 不改下单、限额、停开锁、负债、对账逻辑。
- 不改 discovery 的 `category()`（原始分类保持可审计）。

## 验收
- 测试（新建或加到 tests/test_round8_universe.py 附近，命名清楚）：
  - gate BLESS(unconfirmed) + bitget BLESS(crypto) → 两腿都过；gate vs bybit 都 unconfirmed 但 bitget 有 crypto → 过。
  - 只有 gate/bybit 没有 okx/bitget crypto → 仍 `category_unconfirmed`。
  - 同 base 任一市场是 listed_equity/commodity/pre_ipo → 不继承。
  - bitget 那个 crypto 合约 trading_blocked → 不继承。
  - 管理员 excluded / category=listed_equity → 管理员优先，不继承。
  - alias 前缀不同（1000PEPE vs PEPE）不互相继承。
  - 继承腿中间价偏差 6% → `identity_price_mismatch`；4% → 不因此被拒。
  - 原有 okx/bitget/binance 判定行为不变。
- 跑：`python -m pytest tools/crossex_exec/tests -q -p no:cacheprovider`（全量，必须全绿；不得放宽或删除已有断言）。
- 只改 tools/crossex_exec/universe.py、engine.py（仅复核价格校验处，如需要）、admission.py（拒绝原因列表）、api/management 只读字段（如需要）及对应测试；不要 git commit；不要联网。
- 结束时输出：改了哪些文件、继承判定入口函数名与行号、价格校验位置、测试命令与汇总行。
