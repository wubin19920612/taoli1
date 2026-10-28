# 任务书：人工建卡预览/创建使用实时双腿复核（2026-10-28）

## 现象
合约查询页 JIOUSDT，Bitget 永续买 / Hyperliquid 永续卖，"创建 Astro 卡片"弹窗报：
资产身份待核验或证据已过期；价格与合约单位待核验（两腿各一次，重复显示）；仅有参考价格，缺少真实买卖报价；双方行情时间差过大。

## 根因（Claude 已确认）
1. `backend/app/api/routes_astro.py:_build_astro_preview` 调 `market_validation.qualify_opportunity(opportunity, risk_settings)` 没传 `fresh=True`，只用快照校验。
2. HL 非 `priority_order_book_markets` 的永续快照来自 `metaAndAssetCtxs` midPx（`exchanges/hyperliquid.py:_parse_perp_rows`，`quote_kind="reference"`），时间戳是轮询本地时间 → 必然 REFERENCE_QUOTE_ONLY，且与 Bitget 腿常超 10 秒 → LEG_TIME_SKEW。
3. 身份/单位未核验：JIO 是股票/RWA 类，`market_identity_catalog.catalog_bindings` 有意跳过；**这是策略，不改**，用户会到 设置→市场身份核验 人工绑定。

## 要做
1. 人工建卡的预览和实际创建（`/astro/instrument/preview`、对应的 instrument create 路径，以及其他走 `_build_astro_preview` / `_create_astro_card` 的人工入口）改为 `qualify_opportunity(..., fresh=True)`，用复核返回的新鲜双腿盘口做校验；若复核返回的腿价格与原 opportunity 不同，用新腿重建 opportunity（参考 `_review` 内 `build_directional_opportunity` 用法），保证预览价差、Astro 阈值与校验所用盘口一致。
2. 复核失败（队列满 `ValueError`、l2Book 失败等）时返回明确中文 blocker（如"实时盘口复核失败：…，请稍后重试"），不能 500，也不能退回成通过。
3. `astro_planner.plan` 中 `validation.blockers` 追加到 blockers 时按 message 去重（同一 message 两腿只显示一次，可带"买入侧/卖出侧/双方"前缀合并）。
4. **硬约束（来自 docs/task-handoff-2026-10-15-market-identity-validation.md）**：身份、单位、必需行情阻断仍不能被 manual override 清空；未核验身份的 JIO 修复后仍应被"资产身份待核验"拦住——只是不再出现参考价/时间差这两条假阻断。不改 pair_validation 判定规则、不改 catalog 自动核验范围、不放宽现有测试。
5. 遵守限频：复核走现有 `review()`（semaphore + inflight 合并），不要新开绕过限频的直连请求。

## 测试（先写失败用例）
- HL 腿快照为 reference、复核返回 book 且时间对齐、两腿身份已核验 → 预览 `can_submit=True`，无 REFERENCE_QUOTE_ONLY / LEG_TIME_SKEW。
- 同上但身份未核验 → 只剩身份/单位 blocker，且每条 message 只出现一次。
- 复核抛 ValueError → 预览返回 blocker、HTTP 200、`can_submit=False`。
- 创建路径同样使用 fresh 复核（mock 断言 fresh=True 或 review 被调用）。

## 验收命令
- `cd backend && python -m pytest tests -q -k "astro or validation or instrument" --basetemp=.pytest_preview_fresh_a -p no:cacheprovider`
- `cd backend && python -m ruff check app/api/routes_astro.py app/services/astro_planner.py`
- 不要跑 WSL；不要跑前端（除非改了前端）。

## 范围
可改：`backend/app/api/routes_astro.py`、`backend/app/services/astro_planner.py`、必要时 `backend/app/services/market_validation_review.py`（仅限加辅助方法，不改现有判定）、对应 tests。
不改：`pair_validation.py`、`market_identity_catalog.py`、前端、部署文件、.env。
完成后在本工作树 commit（`fix(astro): fresh two-leg review for manual card preview`），不要 push、不要部署。最后输出：改动文件、实际执行的测试命令与汇总行。

## 补充授权（Claude，2026-10-28 第二轮）
批准扩大以下两处，其余范围不变：
1. `market_validation_review.py`：未核验身份提前返回的分支，可加关键字参数（如 `fetch_books_when_unverified=False`），**默认值保持原行为**；只有人工预览/创建入口传 True。后台 `_background_review`、自动建卡、告警路径不得改变行为或增加请求。
2. `astro_alerts.py`：只改人工创建路径（重新读取旧快照处）改为使用 fresh 复核结果；自动建卡 / 告警路径（含 :486 已有 fresh=True 处）不动。
仍然禁止：清空身份/单位/行情硬阻断、放宽已有断言、改 pair_validation / catalog。
新增测试：后台复核在未核验时仍不拉盘口（断言 fetch 未被调用）。

## 更正（Claude，2026-10-28 审查后）
第 4 条"未核验身份的 JIO 修复后仍应被拦住"写错。按 10-15 交接 §开头用户决定（未知身份先观察，保留现有未知市场流程），shadow 模式下仅剩 ASSET_IDENTITY_UNVERIFIED/UNIT_UNVERIFIED 时 `should_enforce()` 为 False，人工建卡放行是预期行为；enforce 模式或 different/conflict 仍拦截。只读审查 P1 据此判定为非缺陷。
