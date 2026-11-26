# P19 手机币价监控与到价提醒（后端，2026-11-26）

设计稿：`D:/吴彬工作/claude/套利/research/mobile-app/p19-price-watch-design.md`（用户已拍板，先读）。参考：`routes_mobile.py`（pins / eqn-news settings 的个人设置写法）、`app/core/security.py` `_mobile_route` 白名单、`app/services/mobile_push.py`（MobilePushService、PushKinds、send_urgent/queue_*、mobile_alert_events 记录）、`mobile_query_utils.py`（限频）。

## 范围
- 新建：`backend/app/services/mobile_price_watch.py`（配置存取、价格环形缓冲、提醒判断后台任务）、`backend/app/api/routes_mobile_price_watch.py`（在 routes_mobile include）、测试 `backend/tests/test_mobile_price_watch.py`。
- 可改：`app/models/mobile.py`（新模型；PushKinds 新增 `price_watch: bool = True`）、`app/core/security.py`（白名单只加 `("POST", "/api/mobile/price-watch")`）、`app/main.py`（启动/停止后台任务，挂 app.state）、`mobile_push.py`（新增发送入口，不改已有类别行为）、`db/repositories.py`（如用 app_settings 存配置）。
- 不可改：行情采集、机会/价差计算、网页接口与前端、已有推送类别的判断逻辑。

## 配置（存 app_settings，key `mobile_price_watch`，单用户）
- `symbols: list[str]`：基础币名（如 BTC、ETH），大写去重，≤20，校验 `^[A-Z0-9]{1,20}$`。
- `alerts: list[Alert]`，≤50。Alert：`id`（客户端 uuid 或服务器生成）、`symbol`（须在 symbols 中）、`kind: above|below|change`、`price`（above/below 必填 >0）、`change_pct`（change 必填 0.1–50）、`window_minutes`（change 必填 1–60）、`direction: up|down|both`（change 用，默认 both）、`rearm: bool`、`enabled: bool`；服务器维护的只读字段：`state: armed|triggered|cooldown|rearm_wait`、`last_triggered_at`、`last_trigger_price`、`cooldown_until`。
- POST 整体替换：按 id 匹配的提醒若条件字段（kind/price/change_pct/window/direction/symbol）未变则保留服务器状态；变了或新增则 state=armed；客户端传来的只读字段一律忽略。`enabled=true` 且原状态为 triggered 时视为“重新启用”→armed。

## 价格
- 来源顺序：binance 现货 `<SYM>USDT` → okx / bybit / bitget / gate 现货；取快照中 (bid+ask)/2；行情超过 RiskSettings.stale_after_seconds 视为过期（`stale=true`）。
- 环形缓冲：后台任务每 10 秒给关注币各记一点，保留 60 分钟；5m/15m/1h 涨跌用“≥该时长前最近的点”，不够返回 null。

## 判断（后台任务每 5 秒，异常不影响主流程，记日志后继续）
- above：price ≥ 目标；below：price ≤ 目标；change：与 window 前的点比，涨/跌幅绝对值 ≥ change_pct 且方向符合。
- 仅 state=armed 且 enabled 且行情未过期时判断。触发：推送 + 记录 mobile_alert_events（kind `price_watch`）。
- 触发后：rearm=false → state=triggered、enabled=false；rearm=true 且 above/below → rearm_wait，直到价格回到目标另一侧 0.1% 以外 → armed；rearm=true 且 change → cooldown，`cooldown_until = now + window` 后 → armed。
- 状态变化持久化（并发安全，与 POST 互斥；POST 期间不丢触发状态）。
- 推送：kind `price_watch`、紧急通道；按设备 PushKinds.price_watch 开关；标题/正文如 `BTC 上穿 100,000` / `现价 100,012 · 币安现货`，`ETH 5 分钟涨 2.3%` / `现价 3,512 · 币安现货`。价格按量级格式化（≥1000 千分位 0 位小数、≥1 四位有效小数、否则 6 位有效数字）。

## 接口
1. `GET /api/mobile/price-watch` → `{symbols, alerts}`（含只读状态字段）。
2. `POST /api/mobile/price-watch`（body `{symbols, alerts}`）→ 保存后同 GET 返回；参数非法 422 中文文案；每会话 ≥1 秒一次，否则 429。
3. `GET /api/mobile/price-watch/quotes?symbols=BTC,ETH`（不传则用关注列表；≤20）→ `{observed_at, quotes:[{symbol, exchange, market_type, price, bid, ask, change_5m_pct, change_15m_pct, change_1h_pct, age_seconds, stale, error}]}`；找不到行情时 price=null、error="暂无行情"。限频：每会话 1 在途、≥2 秒（与其他名额独立）。
- 内部失败 503 固定中文文案“币价数据暂不可用”，不泄露异常。mobile_readonly 可访问以上三个；其他网页接口仍 403。

## 验收
- 测试覆盖：来源回退与过期、环形缓冲涨跌计算、above/below/change(up/down/both) 触发、rearm 与 cooldown 状态机、过期不触发、POST 保留/重置状态与忽略只读字段、上限与校验 422、并发（判断与 POST 交错不丢状态）、推送调用与 PushKinds 开关、mobile_alert_events 记录、限频 429、权限（白名单仅新增这一个 POST）、503 不泄露。后台任务用可注入时钟测试，不 sleep 真实时间。
- pytest 用锁定 venv，`--basetemp` 到 scratchpad：先跑 `-k "mobile or price_watch"`，再跑全量，输出汇总行。
- **只 commit：禁止 git push、禁止 ssh/scp、禁止部署或操作服务器，忽略 AGENTS.md 里的推送与部署步骤。** 不跑 WSL。commit `feat(mobile): price watch quotes and alerts (P19)`；输出 `git diff --name-only HEAD~1`、pytest 汇总行、新模型字段清单。
