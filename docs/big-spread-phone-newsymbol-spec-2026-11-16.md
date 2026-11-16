# 电话加急只对"新标的"生效

用户诉求:不要因为某个历史上一直有的价差就把人半夜吵醒;电话只给新出现的标的。

## 规则
在 backend/app/main.py `_maybe_send_big_spread_phone()`(现有:夜间时段/阈值/每晚上限/同币种一晚一次)增加一道判定:
- 该币种在过去 N 天(默认 7,可配置)内**没有**出现过大价差提醒记录,才允许打电话;否则不打(文字提醒照常)。
- "出现过"的数据来源:alert_events 表中 rule_id 为大价差(big_spread 通道,请读 main.py 里大价差投递写入 alert_events 的 rule_id/status)且 symbol 相同、created_at 在窗口内、**排除本次事件自身**(按 id 排除)的记录;status 为 sent 或 muted 都算"出现过"。
- 数据库查询失败时:不打电话(宁可漏打也不误吵),写一行 warning(只含异常类型名)。
- 判定放在上限计数之前,不通过的不占用每晚名额,也不写电话 delivery 记录。

## 配置(前端可改)
- RiskSettings 新增 `big_spread_phone_new_symbol_days: int = Field(default=7, ge=0, le=90)`,0 表示不做新标的限制(旧行为)。
- 后端 models/settings.py、frontend types.ts、SettingsPage.tsx "大价差快速提醒"分组的电话相关控件旁加一个"新标的窗口(天)"数字输入,文案"仅对 N 天内未出现过大价差的币种打电话,0 为不限制"。
- 面板 frontend/src/components/AlertSwitchboard.tsx 夜间电话加急的状态文案追加"仅新标的(N天)"(N=0 时不追加)。

## 测试
- backend:tests/test_big_spread_phone.py 增:窗口内有历史→不打且不占名额;窗口外/无历史→打;天数=0→旧行为;DB 查询异常→不打且有 warning;本次事件自身不算历史。
- frontend:SettingsPage.test.tsx 增新字段保存;AlertSwitchboard 文案测试。

## 约束与验收
- 不改其他告警逻辑、不改 .github。已有测试不得放宽。
- 跑:backend `python -m pytest tests/test_big_spread_phone.py tests/test_big_spread_alert.py tests/test_big_spread_throttle.py -q`;frontend `npx tsc --noEmit` 和 `npx vitest run --maxWorkers=1 --minWorkers=1 tests/AlertSwitchboard.test.tsx tests/SettingsPage.test.tsx`。
- 汇报改动文件 + 各汇总行。不要提交,不要跑 WSL。
