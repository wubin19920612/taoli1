# P14 方程式新闻频道订阅交接

本次交付为手机 App 提供订阅接口，并将所选频道的新消息送往飞书和手机。分支为 `codex/mobile-p1`，开发基线为 P13 提交 `03edb3e`。交付范围限本地后端提交，线上版本和真机收件情况未核验。

## App 接入

- 已登录的手机只读会话可直接使用 `GET /api/mobile/eqn-news/settings` 获取 `channels` 和 `available`。App 可按后者的 `key` 和 `name_zh` 渲染选项。
- 向同一路径 POST 完整选择，例如 `{"channels":["main","hl"]}`。响应返回已去重的频道列表。空数组关闭订阅，非法频道返回 422。订阅对所有设备生效，每台设备仍可通过 `PushKinds.eqn_news` 单独关闭通知。旧设备缺少该字段时默认接收。

## 后端入口与投递行为

- `backend/app/api/routes_mobile.py` 提供设置接口。`SettingsRepository` 在 `app_settings` 的 `eqn_news` 记录中保存频道及进度。新选频道先保存为 null，首次成功抓取后建立基线。
- `backend/app/main.py` 在后台 worker 生命周期内运行 `services/eqn_news.py`。每轮从聚合流抓取最多 50 条，跳过交易所公告频道。符合条件的消息超过 20 条时保留最新 20 条，并按编号升序发送。多频道消息只发送一次，标题采用订阅顺序中首个符合投递条件的频道。
- 每条发送前先持久化进度，防止重启重复发送。若进程恰在写入进度和投递之间退出，该条可能漏发。
- `services/feishu.py` 负责飞书格式、提醒记录与手机入队。实时发送关闭、webhook 缺失或飞书发送失败时仍走手机路径。手机副本沿用已有脱敏规则。手机实际收件依赖有效设备注册、推送配置与设备偏好。
- `services/mobile_push.py` 只保留当前设备的已处理哈希，单条提醒达到 500 个后丢弃剩余投递并记录警告。

## 本地验收

在 Windows 的 `backend` 目录使用指定解释器运行 `python -m pytest tests -k 'mobile or feishu or eqn'`，并使用任务指定的 basetemp。结果为 `287 passed, 4275 deselected, 1 warning in 51.47s`。告警来自 Starlette TestClient 的 httpx 弃用提示。新闻源与飞书投递用例使用假 HTTP。

任务开始前已有三份未跟踪输入文档，均保留在工作树：`docs/mobile-p12-feishu-push-2026-11-25.md`、`docs/mobile-p13-pair-spread-2026-11-25.md`、`docs/mobile-p14-eqn-news-2026-11-25.md`。后续 App 界面任务可直接使用上述接口。
