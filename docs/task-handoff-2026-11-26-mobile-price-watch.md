# P19 手机币价监控与到价提醒后端交接

分支为 `codex/mobile-p1`，基线为 `f52b30d`。本次交付包含手机关注列表、现货报价、服务端提醒和紧急推送。任务书见 [P19 后端任务书](mobile-p19-price-watch-2026-11-26.md)。

## App 接口

| 方法和路径 | App 接入约定 |
|---|---|
| `GET /api/mobile/price-watch` | 返回有序关注列表和提醒。首次使用返回两个空列表。 |
| `POST /api/mobile/price-watch` | 整体替换配置，最多 20 个币和 50 条提醒。请保存响应中的提醒 UUID，后续编辑按 UUID 匹配。每会话间隔至少 1 秒。 |
| `GET /api/mobile/price-watch/quotes` | 默认按关注顺序返回，可用 `?symbols=BTC,ETH` 指定。每会话最多一个在途请求，启动间隔至少 2 秒。 |

手机只读会话可以访问这三个接口。POST 只新增上述精确路径的授权。路由在 `backend/app/api/routes_mobile_price_watch.py`，请求与响应模型在 `backend/app/models/mobile.py`。参数错误返回中文 422，限频返回 429，内部失败返回 503 和“币价数据暂不可用”。

## 行情与提醒

- `backend/app/services/mobile_price_watch.py` 按币安、OKX、Bybit、Bitget、Gate 顺序查找 USDT 现货，取买一卖一中间价。优先来源存在但已过期时，响应带 `stale=true`，跳过提醒和采样。App 可直接读 `exchange` 展示实际来源。
- 后台每 5 秒判断，约每 10 秒采集一个有效价格点。5 分钟、15 分钟和 1 小时涨跌均为百分数。内存历史重启后重新积累，数据不足返回 `null`。小时边界保留一个相邻采样点，以容纳调度偏差。
- 已满足的条件在下一次后台检查触发。单次提醒随后变为 `triggered` 且 `enabled=false`。客户端明确提交 `enabled=true` 可重新启用。条件变更会重置触发记录。自动重新生效的价格提醒须跨回目标价另一侧 0.1%，涨跌幅提醒冷却一个完整窗口。
- `backend/app/db/repositories.py` 将整个列表保存在 `app_settings` 的 `mobile_price_watch` 键下，以旧值匹配重试并发更新。SQLite 在一条写语句中提交触发状态和 `mobile_alert_events` 记录。手机提醒页可读取 `kind=price_watch` 的记录。
- `backend/app/services/mobile_push.py` 将已提交的事件加入现有 FCM 紧急发送队列，并检查设备的 `price_watch` 开关。旧设备缺少该字段时默认开启。推送队列在内存中，提交后进程中断可能影响该次设备投递，已入库的历史仍可查看。

## 验收与下一任务

- 在 `backend` 目录使用任务指定的 Python，定向运行 `-k "mobile or price_watch"`，新临时目录为 `pt-p19/run5`。结果为 `255 passed, 4404 deselected, 1 warning in 66.27s (0:01:06)`。
- 在新的 `pt-p19/run6` 临时目录运行全量后端回归，结果为 `4648 passed, 11 deselected, 13 warnings in 309.87s (0:05:09)`。项目默认排除 network 和 benchmark 标记的用例，警告来自 Starlette TestClient 和 `datetime.utcnow()` 弃用提示。
- 验收使用 Windows 本地锁定环境、进程内 API、测试 SQLite 库和模拟 FCM。交付止于本地提交。真实 FCM 送达、锁屏展示、币价浮窗与 Android 0.17.0 的操作流程留待 App 集成验收。
- 工作区中原有 P12、P13、P14、P15、P16 五份未跟踪任务书均保留原位。下一任务可从三个接口接入工具页、独立币价浮窗及“到价提醒”推送开关。
