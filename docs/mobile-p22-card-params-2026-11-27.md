# 手机 P22：改卡参数（后端，2026-11-27）

设计稿（已拍板，先读）：`D:/吴彬工作/claude/套利/research/mobile-app/card-params-design.md`。**这是真钱卡片的写操作**：任何校验不过都不能写 Astro；写入以 Astro 最新卡片为底，只改本次请求的字段。App 任务书：astro-mobile `docs/p22-card-params-2026-11-27.md`（接口以本文为准）。

## 现状（照着复用，不要另起一套）
- `services/mobile_card_control.py`：`MobileCardControl.control()` = 限频 → 解锁判断 → 卡片锁 `service.pair_lock(name)` → `_latest_pair()` 不走缓存重读 → 核对 expected（409 `CONFLICT`）→ `client.update_pair(dict(pair, **{field: value}))` → `_record_change()` 审计+提醒 → 锁外 `mobile_push.send_urgent(kind="card_control", …)`。
- `models/mobile.py:533 CardControlUpdate`；`api/routes_mobile.py:240` 路由挂在 `controls`；`core/security.py:90` 改卡路由清单。
- `services/mobile_summary.py`：`condition(pair, field)` 把 Astro 值换成百分比（普通卡 ×100；比值卡 `type` 以 R 结尾，用 `spread_pct(value, regressionValue)`，`spread_pct(l, r) = (r-l)/((l+r)/2)*100`）；`open_auto/close_auto = condition 非空且 |x|<100`（`Infinity`/缺失 = 不自动）。

## 允许修改
`backend/app/models/mobile.py`、`backend/app/services/mobile_card_control.py`（或新建 `services/mobile_card_params.py`，复用前者的锁/解锁/记录函数）、`backend/app/api/routes_mobile.py`、`backend/app/core/security.py`、`backend/app/services/mobile_summary.py`、`backend/app/core/config.py`（只加 `MOBILE_MAX_LEVERAGE`）；测试 `backend/tests/test_mobile_card_params.py`（新）、`backend/tests/test_mobile_api.py`（summary 新字段）。不改其他文件。

## 需求
### 1. summary 新字段（`MobileCard`，可空，老 App 忽略）
`max_trade_usdt: float | None`、`leverage: int | None`、`min_notional: float | None`、`max_notional: float | None`，从 Astro `maxTradeUSDT / leverage / minNotional / maxNotional` 解析（`number()`，非法为 None；leverage 非整数为 None）。
### 2. 接口 `POST /api/mobile/cards/params`
```json
{"card_id": "...",
 "changes":  {"open_condition_pct": 2.3, "close_condition_pct": 0.4, "max_trade_usdt": 20, "leverage": 3, "min_notional": 10, "max_notional": 60},
 "expected": {"open_condition_pct": 2.55, "close_condition_pct": 0.5, "max_trade_usdt": 10, "leverage": 1, "min_notional": 10, "max_notional": 10}}
```
- `changes` 至少 1 项、只允许这 6 个键；`expected` 必须包含 `changes` 里的每个键（值可为 null，表示 App 看到的是“不自动/缺失”）。模型 `CardParamsUpdate(_OpsInput)`，多余键 422。
- 返回 `{"ok": true, "card_id": ..., "changed": ["open_condition_pct", ...], "card": <MobileCard，同 summary 构造>}`；全部与现值相同时 `changed: []` 且不写 Astro。
- **永远需要解锁**（没有免解锁项）；未解锁 403、总开关关 503、限频与 `control` 共用计数 429。
- 加进 `core/security.py` 的改卡路由清单。
### 3. 核对 expected（锁内、最新卡片）
- 条件：用 `condition()` 算现值百分比（与 summary 的 `open_condition/close_condition` 同一函数；缺失/Infinity 为 None，|x|≥100 时照样返回数值）；与 expected 比较，都为 None 或 |差| ≤ 1e-6 视为一致。
- 金额/杠杆：同 §1 解析后比较，容差 1e-9。
- 任一不一致 → 409 `CONFLICT`，不写。
### 4. 校验（全部通过才写；失败 422，`detail` 中文写明哪一项）
- 数值必须有限（拒绝 NaN/Infinity/布尔）。
- 开、清仓条件：-50 ≤ x ≤ 50；**最终**开仓条件 > 清仓条件（未修改的一侧用现值；某侧现值为不自动时：只改另一侧可以，不比较）。允许负数。
- 不支持从手机把某一侧改成“不自动”（不接受 null 作为新值）。
- 单次下单金额 > 0；最小持仓 > 0；最小持仓 ≤ 最大持仓；单次下单金额 ≤ 最大持仓（都用最终值；缺失的现值不参与比较）。**不设金额上限。**
- 杠杆：整数，1 ≤ x ≤ `settings.mobile_max_leverage`（env `MOBILE_MAX_LEVERAGE`，默认 10）。有持仓时允许改。
### 5. 写回格式
- 条件 → Astro：普通卡 `pct / 100`；比值卡 `ref * (200 - pct) / (200 + pct)`（ref = `regressionValue`，缺失或 ≤0 时该卡条件修改 422“比值卡缺少基准，请在网页改”）。写回前用 `condition()` 正向换算核对，误差 > 1e-6 则 422。
- 数字统一用 `astro_planner._compact_number` 同款格式写成字符串（可把它提到公共处或复制一个私有函数；不要改 astro_planner 行为）；杠杆写整数字符串。
- `client.update_pair(dict(pair, **新字段))`，只改这几个键。
### 6. 记录与推送
- 审计日志一行：`astro_card_params pair=<name> changes=<字段:旧→新,…> auth=<method>`。
- 手机提醒 + `send_urgent(kind="card_control")`，标题 `<卡名> 参数已修改`，正文如 `开仓条件 2.55% → 2.30%；单次下单 10 → 20 U；杠杆 1 → 3 倍（手机）`。推送失败不影响结果；提醒记录失败沿用 `control` 的 503 处理。
- Astro 拒绝（`AstroClientError`）→ 原样转中文 `Astro 拒绝修改（HTTP n）`。

## 验收
- 新测试 `test_mobile_card_params.py` 覆盖：未解锁 403；总开关关 503；expected 不一致 409 且未调用 update_pair；开≤清 422；条件超范围 422；新值 null 422；金额关系（min>max、单次>max）422；杠杆非整数/0/11 422、10 通过；有持仓改杠杆通过；普通卡与比值卡换算往返（含负数条件）；只改一项时其他键原样保留；无变化不写；审计日志与推送正文；Astro 拒绝转写；限频与 control 共用。
- `test_mobile_api.py`：summary 四个新字段解析（含非法值为 None）。
- 跑：`cd backend && python -m pytest tests/test_mobile_card_params.py tests/test_mobile_card_control.py tests/test_mobile_api.py -q`，再跑全量 `python -m pytest -q`；输出原始汇总行。
- 只 commit 到当前分支：**禁止 git push、禁止部署、禁止 ssh、不跑 WSL**；commit `feat(mobile): card parameter edits (P22)`；输出 `git diff --name-only HEAD~1`。
