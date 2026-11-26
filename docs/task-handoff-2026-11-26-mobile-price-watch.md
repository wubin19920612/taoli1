# P19 手机币价监控与到价提醒后端交接

分支为 `codex/mobile-p1`。P19 首版为 `fb9907e`，本轮在该提交上修复手机币价提醒的并发保存、写锁、推送入队与断流处理。见 [P19 后端任务书](mobile-p19-price-watch-2026-11-26.md) 和 [审查修复单](mobile-p19-fix1-2026-11-26.md)。

## App 接口

| 方法和路径 | App 接入约定 |
|---|---|
| `GET /api/mobile/price-watch` | 返回有序关注列表、提醒和整数 `revision`。首次使用返回两个空列表与版本 0。已有配置缺少版本字段时也从 0 开始。 |
| `POST /api/mobile/price-watch` | 整体替换配置，最多 20 个币和 50 条提醒。带上打开表单时的 `revision`，用响应中的新版本继续编辑，按 UUID 匹配提醒。每会话间隔至少 1 秒。 |
| `GET /api/mobile/price-watch/quotes` | 默认按关注顺序返回，可用 `?symbols=BTC,ETH` 指定。每会话最多一个在途请求，启动间隔至少 2 秒。 |

手机只读会话可以访问这三个接口。POST 只新增上述精确路径的授权。路由在 `backend/app/api/routes_mobile_price_watch.py`，请求与响应模型在 `backend/app/models/mobile.py`。参数错误返回中文 422，限频返回 429，内部失败返回 503 和“币价数据暂不可用”。

每次成功保存、每轮实际提交提醒状态变化都会让整份配置的 `revision` 加 1。POST 缺少该字段返回 422，版本冲突返回 409 和“提醒已在别处更新，请刷新后再保存”。App 应重新 GET 后让用户确认编辑。单次提醒触发后的重新启用需要在最新配置上明确把 `enabled` 从 `false` 改为 `true`。

## 行情与提醒

- `backend/app/services/mobile_price_watch.py` 按币安、OKX、Bybit、Bitget、Gate 顺序查找 USDT 现货，取买一卖一中间价。优先来源存在但已过期时，响应带 `stale=true`，跳过提醒和采样。App 可直接读 `exchange` 展示实际来源。
- 后台每 5 秒判断，约每 10 秒采集一个有效价格点。提醒和报价都取窗口起点前 30 秒至起点（含两端）的最近样本。样本缺失时涨跌为 `null`，跳过该窗口的涨跌幅提醒。内存历史重启后重新积累。行情过期或缺失期间，到期的冷却仍会解除。
- `backend/app/services/snapshot_store.py` 在每个快照版本首次查询关注币时扫描一次市场，以币种建立现货索引。该版本内的判断和报价只读取所查币种的现货快照。
- 已满足的条件在下一次后台检查触发。单次提醒随后变为 `triggered` 且 `enabled=false`。客户端明确提交 `enabled=true` 可重新启用。条件变更会重置触发记录。自动重新生效的价格提醒须跨回目标价另一侧 0.1%，涨跌幅提醒冷却一个完整窗口。
- `backend/app/main.py` 在文件库和内存库都为提醒创建独立 SQLite 连接。`backend/app/db/repositories.py` 使用旧配置值检查并发写入，后台遇到竞争会重读配置再判断。状态和 `mobile_alert_events` 同事务提交，异常会回滚并释放写锁。手机提醒页可读取 `kind=price_watch` 的记录。
- `backend/app/services/mobile_push.py` 用专用内存队列收取整轮紧急推送，最多等待 16 批。队列已满时本轮事务回滚，下轮重新判断。发送任务等待事务提交成功才投递，按设备的 `price_watch` 开关筛选，旧设备默认开启。数据库提交后至实际发送前进程退出，仍可能丢失待发推送，事件历史已持久化。

## 验收与下一任务

- 在 `backend` 目录使用任务指定的 Python，定向运行 `-k "mobile or price_watch"`，新临时目录为 `pt-p19/fix3`。结果为 `266 passed, 4404 deselected, 2 warnings in 61.68s (0:01:01)`。回归覆盖了双连接写锁释放、陈旧表单与并发触发、窗口采样边界、行情过期时冷却到期、整批入队回滚和重试。
- 在新的 `pt-p19/fix5` 临时目录运行全量后端回归，结果为 `4659 passed, 11 deselected, 14 warnings in 307.22s (0:05:07)`。项目默认排除 network 和 benchmark 标记的用例。警告来自既有弃用提示及 pytest 写入本地缓存时的权限提示。
- 前一轮 `fix4` 中，Shadow 关停测试一次收到约 0.515 秒的 join 参数，超过断言上限 0.5 秒。相同代码与断言在 `fix5` 全量复跑通过。
- 验收使用 Windows 本地锁定环境、进程内 API、测试 SQLite 库和模拟 FCM。交付止于本地提交。真实 FCM 送达、锁屏展示、币价浮窗与 Android 0.17.0 的操作流程留待 App 集成验收。
- 工作区中原有 P12、P13、P14、P15、P16 五份未跟踪任务书均保留原位。下一任务可从三个接口接入工具页、独立币价浮窗及“到价提醒”推送开关。
