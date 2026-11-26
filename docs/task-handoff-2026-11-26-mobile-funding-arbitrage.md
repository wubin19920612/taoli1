# P18 手机资金费率套利接口交接

分支为 `codex/mobile-p1`，基线为 `50f00de716cd6dc4307268835a82d2466a789c4f`。本阶段交付资金费率套利只读预览后端，验收范围为 Windows 本地测试和 Git 提交。任务书为 `docs/mobile-p18-funding-arbitrage-2026-11-26.md`。

## 接口与代码入口

- `GET /api/mobile/funding-arbitrage/preview` 由 `backend/app/api/routes_mobile_funding.py` 提供，直接调用网页 `routes_funding_arbitrage.get_funding_arbitrage_preview`。网页风控设置和 `max_candidates` 先应用。手机接着按 `decision`、`type` 和大小写不敏感的 `symbol` 子串过滤，保留网页顺序。计数使用网页结果，`filtered_total` 为过滤后、手机截断前的数量。手机的 `limit` 默认 100，范围为 1–200。
- `backend/app/models/mobile.py` 中的三个 `MobileFunding*` 模型限定返回字段。两腿原始标的、资金费率来源、费率周期、估算成本和风险标签沿用网页值。`observed_at` 为手机预览生成时的 UTC 时间。
- 手机接口要求有效会话。手机只读会话可读该预览，访问网页资金费率接口返回 403。手机资金费率路由仅注册预览 GET。
- `MobileQueryLimits` 按会话限制 1 个在途请求和至少 3 秒的启动间隔，独立于其他手机工具。非法查询参数返回 422，限频返回 429。设置库未就绪及内部失败统一返回 503 和“资金费率数据暂不可用”。

## 验收与接续

- `backend/tests/test_mobile_funding.py` 用同一市场快照和设置对比两个预览接口，校验完整返回字段、计数、顺序、筛选、上限、会话权限、限频及错误处理。
- 在 `backend` 用指定虚拟环境执行 `python -m pytest tests -k 'mobile or funding' --basetemp=<scratchpad>/pt-p18/run2 -q`，汇总为 `380 passed, 4261 deselected, 1 warning in 71.67s (0:01:11)`。警告来自 Starlette TestClient 的 httpx 弃用提示。
- 使用同一环境执行全量 `python -m pytest tests --basetemp=<scratchpad>/pt-p18/run3 -q`，汇总为 `4630 passed, 11 deselected, 13 warnings in 313.84s (0:05:13)`。全量额外警告来自指数成分相关测试的 `datetime.utcnow()` 弃用提示。
- 线上未发布。App 接入和真机验证留待后续任务。App 可以用 `filtered_total` 显示筛选后的条数，用 `displayed_candidates` 显示网页候选全集的条数。
- 工作树已有的 P12、P13、P14、P15、P16 任务书仍为未跟踪文件。P18 任务书随本次提交归档。
