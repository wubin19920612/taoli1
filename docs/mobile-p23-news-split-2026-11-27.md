# 手机 P23：公告/新闻与提醒分开（后端，2026-11-27）

用户要求：「提醒」页不要混入交易所公告和方程式新闻（太多太乱），这两类在 App「工具 → 公告与新闻」单独看。**在 P22 提交之后再做**（同改 `routes_mobile.py`）。

## 允许修改
`backend/app/api/routes_mobile.py`、`backend/app/db/repositories.py`（只改 `MobileAlertRepository.list`）、`backend/app/services/feishu.py`（只改 `_event_text` / `_record`）、`backend/app/services/announcements.py`（只改 `build_announcement_alert_message` 标题行）、`backend/app/services/announcement_relevance.py`；对应测试（`test_mobile_api.py`、公告与 feishu 现有测试文件）。不改其他文件。

## 需求
1. `GET /api/mobile/alerts` 新增查询参数 `group: Literal["alerts", "news", "all"] = "alerts"`：
   - `alerts`（默认）：排除 `kind IN ('announcement', 'eqn_news')`；
   - `news`：只要这两类；
   - `all`：不过滤（保留给调试）。
   默认值就是 `alerts`，这样**老版本 App 不升级也立刻不再在提醒页看到公告和新闻**（这是想要的效果）。
2. 过滤在 SQL 里做（`WHERE created_at > ? AND kind [NOT] IN (...)`），`limit` 作用于过滤后的结果，避免公告刷屏把交易提醒挤出 100 条窗口。kinds 常量放一处（如 `NEWS_ALERT_KINDS = ("announcement", "eqn_news")`）。
3. 推送不变（仍按 PushKinds 发）。
4. **公告标题**（11-27 实测：手机记录标题是 `[GATE] 公告公告`，看不出内容）：
   - `build_announcement_alert_message` 首行 `{kind_label}公告` 在 OTHER 时变成“公告公告”：OTHER 改为只显示“公告”，其他为“上币公告/下币公告/Launchpool公告”。
   - 手机记录（`feishu._record`）对 `announcement` 类：标题改为 `[交易所] <公告标题>`（取正文 `标题:` 行，优先中文标题，截 160 字），推送标题同。飞书消息文本不变。
5. **保留公告/新闻链接**：`_event_text` 现在把所有 http(s) 链接替换成 `[链接]`，手机上无法打开原文。改为：只对 `announcement`、`eqn_news` 两类保留链接，但仍要遮蔽 query 中的签名/凭据参数（`sign|signature|token|key|secret|auth` 等参数值替换为 `[已隐藏]`），且飞书 webhook 等 `secrets` 依旧整串替换；其他 kind 维持替换为 `[链接]`。
6. **促销公告漏过滤**：11-27 06:45 Gate 公告 “Perp Comeback Rewards: Return to Trading and Earn Up to 86 USDT”（kind=other）在 `other_alerts_filter=arbitrage_relevant` 下被发送了，但它含 rewards/earn，应被排除。查明原因（例如命中了某个“相关”正则后不再看排除词，或大小写/复数问题）并修正，用这条标题加测试；跑公告过滤现有测试确认不误杀。

## 验收
- 测试：标题行 OTHER 不重复“公告”；手机记录标题取公告标题；公告/新闻保留链接且签名参数被遮蔽、其他 kind 仍替换；上述促销标题被排除。
- 测试：三种 group 的结果；默认等于 alerts；limit 在过滤后生效（造 150 条公告 + 5 条交易提醒，默认取 100 仍含那 5 条）；非法 group 422。
- `cd backend && python -m pytest tests/test_mobile_api.py -q` 和全量 `python -m pytest -q`，输出原始汇总行。
- 只 commit：禁止 git push、部署、ssh、docker、WSL；commit `feat(mobile): split news from alerts, readable announcement titles (P23)`；输出 `git diff --name-only HEAD~1`。
