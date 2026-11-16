# 大价差提醒降噪（按币种节流）2026-11-16

背景：生产 24h 推送 607 条，大价差 485 条；INIT 356、SOPH 144 条。原因：大价差冷却键是 `big_spread:{opportunity.id}`（每条路线一个），同一币种多条路线各发一条。

## 目标（只改这些）
1. 币种级节流（backend/app/services/big_spread_alert.py，BigSpreadAlertEngine）：
   - 同一 normalize 后的币种（用 `normalize_mute_symbol(opportunity.symbol)`）任一路线 sent 后，该币种其他路线及本路线在 `big_spread_alert_symbol_cooldown_seconds`（默认 1800，0=关闭节流，上限 604800）内不再产生 match；
   - 例外：新机会 open_spread_pct ≥ 该币种上次 sent 价差 × 2（EPSILON 容差）时放行；放行后更新基线。
   - 状态有界（沿用 MAX_BIG_SPREAD_KEYS 的清理思路，币种基线随冷却过期清除）；重启恢复时无基线则等冷却结束（与现有 restore 行为一致）。
   - 原有路线级冷却与 1.5 倍升级规则保持不变，两者都要满足才发。
2. 设置项：backend/app/models/settings.py RiskSettings 增加 `big_spread_alert_symbol_cooldown_seconds: int = Field(default=1800, ge=0, le=604800)`；frontend/src/api/types.ts、SettingsPage.tsx 大价差分组加一个 InputNumber（标签“同币种提醒冷却秒数”），defaultRiskSettings 同步。
3. 建卡失败标红：backend/app/main.py 大价差消息中，卡片状态以“未创建”开头时，在消息首行前加“⚠️ ”（只对 big_spread 且已 sent 的消息；不改 status 语义）。

## 不改
- 普通规则、溢价告警、屏蔽列表、建卡逻辑、冷却持久化表结构。

## 验收
- 新增单测：同币种两条路线只发一条；价差翻倍放行；cooldown=0 不节流；不同币种互不影响；重启 restore 后不乱发。
- 命令：`cd backend && pytest tests -k "big_spread" -q`，再跑后端全量；前端 `npm test`、`npx tsc --noEmit`（本机，禁止在生产机跑）。
- 汇总行贴出，`git diff --name-only` 仅限上述文件及其测试。
