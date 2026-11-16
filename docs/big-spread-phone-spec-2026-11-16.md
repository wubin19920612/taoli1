# 大价差夜间电话加急 2026-11-16

背景：飞书电话加急已存在（backend/app/services/feishu.py `send_phone_urgent_text`，main.py ~956 手机价格提醒在用）。把大价差最极端的一档接上，夜间叫醒用户。

## 目标（只改这些）
1. RiskSettings（backend/app/models/settings.py）新增，均可在设置页修改：
   - `big_spread_phone_enabled: bool = False`（总开关，默认关）
   - `big_spread_phone_start_hour: int = 0`（0-23，北京时间 Asia/Shanghai）
   - `big_spread_phone_end_hour: int = 7`（0-23；start==end 视为全天；start>end 视为跨零点）
   - `big_spread_phone_min_spread_pct: float = 5.0`（gt 0，le 100，开仓价差）
   - `big_spread_phone_max_per_night: int = 3`（0-20，0=不打）
2. 触发（backend/app/main.py 大价差发送成功之后，仅 is_big_spread 且 status 最终为 "sent"）：
   开关开、当前北京时间在时段内、该机会 open_spread_pct ≥ 阈值、本夜电话数 < 上限、同一币种（normalize_mute_symbol）本夜未打过 → 调用 `send_phone_urgent_text(短文本)`。
   短文本 ≤3 行：币种、买卖所路线、开/平价差、卡片状态；复用已生成消息的数据，不重新拉行情。
   “夜”= 时段起点所在的北京日期（跨零点的时段归起点那天）。
3. 计数必须重启后仍有效：复用现有投递状态存储（alert_delivery_state，key 例如 `big_spread:phone:{夜日期}:{币种}` 与 `big_spread:phone:{夜日期}:count`），不新建表。注意并发：同一轮多条匹配要串行判定，避免超过上限。
4. 失败隔离：电话异常只记 warning 单行（不带 Traceback）+ 在 alert_events 消息后追加“；电话加急失败”不可行则只记日志；绝不影响飞书文字消息、建卡、主循环。失败不占用名额。
5. 前端：frontend/src/api/types.ts、SettingsPage.tsx 的“大价差快速提醒”分组加：开关、起止小时(0-23)、价差阈值、每晚上限；defaultRiskSettings 同步；说明文案写明“北京时间；电话走飞书加急；同一币种每晚最多一次”。

## 不改
普通规则、溢价告警、节流逻辑（big_spread_alert.py 的币种节流）、手机价格提醒、建卡逻辑。

## 验收
- 新增单测：时段内/外（含跨零点 22→6）、阈值边界、每晚上限、同币种一晚一次、开关关、电话失败不影响文字消息且不占名额、重启后计数仍在。
- 命令：`cd backend && pytest tests -k "big_spread or phone" -q`，后端全量；前端 `npm test`（限制 2 workers）、`npx tsc --noEmit`。本机跑，禁止碰生产机/WSL，不 git commit。
- 汇总行贴出，`git diff --name-only` 仅限上述文件及其测试。
