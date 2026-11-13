# 大价差快速提醒与告警拒绝原因日志交接

## 范围与基线

- 模块：价差与资金费率告警。本次只实现大价差快速提醒、普通规则拒绝原因日志及设置/历史入口。
- 任务书：`docs/big-spread-alert-spec-2026-11-13.md`，已完整阅读。
- 分支：`codex/big-spread-alert`。
- 基线：`765fb0bbeb9d2748016e5bbd7bc68068ffb6529c`。
- 用户明确限定：只做本地提交，不 push、不部署、不运行 WSL。本次未访问生产服务器、未发真实通知、未提交真实 Astro 卡片。

## 已完成与代码入口

- `backend/app/models/settings.py`：5 个带默认值的 RiskSettings 字段，旧配置自动补默认值；正数、整数及非有限数校验。
- `backend/app/services/big_spread_alert.py`：独立资格判断、按完整 opportunity.id 连续计数、冷却与相对上次成功发送价差的 50% 加发、失败退避、容量和过期清理。
- `backend/app/services/alert_reject.py`、`alert_engine.py`：独立 `alert_reject` JSON logger；记录普通规则的次数、峰值/最新值及衰减结果、排除标签、费后门槛、成交额、时效、身份及冷却等原因。
- `backend/app/main.py`：从普通规则预过滤之前接入快速候选；保持身份核验；同一轮普通规则实际发送成功后才去重快速提醒；复用诊断、事件、持久化发送状态和时序日志。最新资格变更时记录 muted 事件。
- `backend/app/services/alert_messages.py`：固定大价差前缀、双边 bid/ask、开平价差、双边成交额、原始市场/DEX/倍率、资金周期、费用/行情预估标识、全部风险标签及中文名称；HUGE_SPREAD_VERIFY 提示人工核实。
- `backend/app/services/astro_alerts.py`：独立自动建卡入口，使用局部 settings 副本强制 `open_enabled=False`。保留原有参数、订单簿、身份、黑名单、同名卡冲突、建卡锁和尝试账本流程。
- `backend/app/api/routes_alerts.py`：历史接口保留大价差原始消息。
- `frontend/src/api/types.ts`、`frontend/src/pages/SettingsPage.tsx`：参数与告警页面下的风险参数中提供 5 个中文配置项；旧接口响应兼容默认值。
- `frontend/src/pages/AlertHistoryPage.tsx`：按 `rule_id=big_spread` 展示“大价差提醒”来源标记。
- `backend/tests/test_big_spread_alert.py`、`frontend/tests/SettingsPage.test.tsx`、`frontend/tests/AlertHistoryPage.test.tsx`：覆盖上述业务边界和构造回放。

## 关键业务口径

1. 默认开启、开仓价差 3%、连续 2 轮、冷却 600 秒、快速自动建卡开关默认开启；仍需全局 `alert_auto_create` 同时开启才能写卡。
2. 类型取启用规则并集；排除交易所取启用规则并集；成交额取启用规则最低门槛。任务书未规定多规则时效合并方式，本次取最严格的最小 `max_data_age_seconds`。无启用规则时使用 FF、100000 USDT、600 秒时效。
3. 快速提醒不受普通规则的衰减、风险标签、费后门槛、白名单和 SF 负资金费率过滤影响；身份能力禁用/过期、黑名单、被忽略交易所、成交额和时效仍拦截。
4. 提醒与建卡分别判断：建卡校验失败或已有卡片不会静默大价差提醒；全局飞书发送开关仍生效。不能通过快速通道绕过现有建卡校验。
5. Astro 尝试账本继续使用既有 `source=alert`（数据库约束允许的值），提醒事件使用 `rule_id=big_spread`。无需数据库迁移。
6. 快速状态最多 5000 个 key；连续命中不满足或机会消失即清零。无活跃记录超过 max(冷却秒数, 3600) 后清理；容量溢出按最近活跃时间淘汰。
7. 发送成功的冷却和失败退避沿用发送账本，pending 恢复为结果未知。50% 加发价格基准只保留内存：重启后有冷却记录但无基准时，保守等待冷却结束；连续次数从零开始。
8. 拒绝日志独立输出，`propagate=False`、StreamHandler，每机会最多 30 秒一行。限频表最多 5000 个 key，满额时本窗口新 key 暂不输出，等已有记录到期；不会为腾空间而允许同一 key 刷屏。

## 验证

所有命令使用 Windows 原生命令行，前端复用现有 node_modules 目录链接。

后端目录 `backend/`：

```text
pytest tests/test_big_spread_alert.py tests/test_alert_engine.py tests/test_alert_loop.py tests/test_alert_loop_isolation.py tests/test_alert_delivery_state.py tests/test_alert_messages.py tests/test_astro_alerts.py -q -p no:cacheprovider --basetemp ../.tmp-bigspread-related-v3
329 passed in 19.74s

pytest tests -q -p no:cacheprovider --basetemp ../.tmp-bigspread
1 failed, 3435 passed, 11 deselected, 12 warnings in 501.10s (0:08:21)

pytest tests -q -p no:cacheprovider --basetemp ../.tmp-bigspread-final
2 failed, 3438 passed, 11 deselected, 12 warnings in 318.54s (0:05:18)
```

前端目录 `frontend/`：

```text
npx.cmd tsc --noEmit
exit 0，无类型错误。

npx.cmd vitest run --no-cache
Test Files  44 passed (44)
Tests       678 passed (678)
Duration    185.62s
```

补充检查：`git diff --check` 通过；新增后端文件的 Ruff E9/F63/F7/F82/F401 检查通过。

构造 IMX 回放：2026-11-13 UTC+8 11:09:22 起每 10 秒一轮，最后补 11:10:23 端点；价差序列 5%、6%、16%、12%、8%、5%、7%、6%。第 2 轮快速提醒触发，第 3 轮因相对上次 6% 扩大超过 50% 再发；guize1 始终因排除标签不触发，拒绝日志保留原因。另有独立测试验证 6 轮命中但从峰值 16% 衰减至 5% 的普通规则拒绝详情。

验证过程发现并处理：订单簿 fixture 补齐模型必填值；数值控件默认显示 3.0 的断言改为数值比较；尝试账本 source 改为既有 alert 枚举；新增设置测试在全量并行运行时超过 15 秒，减少交互开销并使用局部 30 秒超时后，全量通过。重复使用旧 pytest basetemp 在 Windows 遇到权限错误，因此相关测试使用新的独立目录复验；未修改系统权限。

已确认的基线失败：`tests/test_market_identity_repository.py::test_catalog_reimport_and_numeric_serialization_do_not_create_false_conflicts`。测试 fixture 固定 NOW=2026-10-15 02:00 UTC、有效期 30 天（11-14 02:00 UTC）。当前运行日期已进入有效期结束前 24 小时，`import_catalog` 按现有逻辑刷新记录并返回 1，测试仍断言 0。使用 `git archive 765fb0bbeb9d2748016e5bbd7bc68068ffb6529c backend` 导出干净基线到 `output/big-spread-baseline-765fb0b/`，在其 backend 目录执行：

```text
pytest tests/test_market_identity_repository.py::test_catalog_reimport_and_numeric_serialization_do_not_create_false_conflicts -q -p no:cacheprovider --basetemp ../.tmp-baseline-check
1 failed in 0.86s
```

基线和当前分支均复现同一断言失败；本次未修改该测试或身份资料刷新策略，没有跳过失败项。后续可在身份核验模块任务中修复固定日期 fixture，使时间口径可控。

最终全量复验另外遇到 `tests/test_shadow_shutdown_stats_freeze.py::test_terminate_failure_escalates_to_kill_with_threaded_joins[1]`：实际 join 超时参数 0.515 秒，测试断言必须 ≤0.5 秒，导致关闭清理断言失败。同一失败已在干净基线复现。该文件在当前分支单独重跑全部通过，说明有时间相关波动；未修改 Shadow 模块或放宽其断言。实际命令与结果：

```text
# 当前工作树 backend/
pytest tests/test_shadow_shutdown_stats_freeze.py -q -p no:cacheprovider --basetemp ../.tmp-bigspread-shadow-check
40 passed in 42.65s

# 干净基线副本 backend/
pytest tests/test_shadow_shutdown_stats_freeze.py -q -p no:cacheprovider --basetemp ../.tmp-baseline-shadow-check
1 failed, 39 passed in 44.62s
```

因此本次相关测试和前端全量通过，但后端全量并非全绿；两个失败均有基线复现证据，应在对应模块任务中继续处理。

## 线上状态、未做项与后续

- 本次未 push、未部署、未做线上备份或线上验证；不声称生产已生效。
- 后端全量保留上述两个可在基线复现的失败，未修复或跳过范围外测试。
- IMX 回放是按任务书构造的机会序列，不是真实逐笔/历史订单簿重放。
- 未测试真实飞书和 Astro 网络写入；集成测试使用假客户端、真实校验链路与 SQLite 尝试账本。
- 多规则合并口径、重启后的 50% 加发基准及日志容量行为见上文，后续如改变需增加对应测试。
- 任务开始时已存在的未跟踪任务书保持原样，不纳入本次提交。`.tmp-bigspread*` 是本次本地验证产物；日志不提交，临时目录保留，部分目录存在 Windows 访问权限限制。未修改 node_modules 链接及其目标。
- 如需发布，请另开服务器运维任务，按仓库交付规则审阅本地提交、获准推送后，再做数据库备份、部署和实际通知/页面验收。其他功能模块应另开任务并引用本交接。
