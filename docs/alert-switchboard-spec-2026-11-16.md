# 告警总开关面板(前端为主)

目标:设置页(frontend/src/pages/SettingsPage.tsx,Tabs 在约 670 行)的页签上方新增"告警总开关"卡片,一屏集中显示/切换所有告警开关。**不新建后端存储、不改后端**,读写现有接口。

## 行与数据来源(每行:名称 + Switch + 一句话状态 + "去设置"按钮,按钮切到对应页签/页面)
1. 大价差提醒 → RiskSettings.big_spread_alert_enabled;状态显示阈值/冷却字段(读 types.ts 里 big_spread_* 字段)
2. 大价差自动建卡 → big_spread_alert_auto_create
3. 夜间电话加急 → big_spread_phone_enabled;状态:"{start}:00-{end}:00 / ≥{min}% / 每晚≤{max}"
4. 多交易所溢价告警 → multi_exchange_premium_alert_enabled
5. 普通告警规则 → 规则列表(现有 alert rules API/页签"告警规则"):汇总"已启用 N / 共 M 条";Switch 不做批量,只显示汇总+去设置
6. 交易所公告 → getAnnouncementSettings/updateAnnouncementSettings(client.ts 472-477):enabled、listing_delisting_alerts_enabled、launchpool_alerts_enabled、other_alerts_enabled、bootstrap_alerts_enabled、event_reminders_enabled 各一行(可折叠为一组)
7. 手机价格提醒 → 现有 PhonePriceAlertsPanel 所用接口:汇总"已启用 N / 共 M 条",仅显示+去设置

## 要求
- 风险参数类开关复用页面现有的 settings 加载/保存方法与状态(同一份 state,改了面板与下方风险参数页签同步);保存失败要提示并回滚 Switch,不得吞错。
- 公告设置独立加载/保存;加载失败该组显示"加载失败"而不是整个面板消失。
- 保存时只改被点击的那个字段,其余字段取最新值原样回写(避免覆盖别处的修改)。
- 文案中文;手机宽度下不横向溢出。
- 新建组件文件 frontend/src/components/AlertSwitchboard.tsx(单文件<300 行),SettingsPage 只做挂载。
- 不要改 backend、不要改 .github、不要改 big_spread 相关逻辑。

## 验收
- frontend 新增测试 tests/AlertSwitchboard.test.tsx:渲染各行、点击大价差开关调用保存且只改该字段、保存失败回滚、公告加载失败降级。
- 跑:cd frontend && npx vitest run --maxWorkers=2 tests/AlertSwitchboard.test.tsx tests/SettingsPage.test.tsx;再 npx tsc --noEmit。
- 汇报:改动文件列表 + 上述命令汇总行。
