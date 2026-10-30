# 通知交付交接（2026-09-25）

## 目标与范围

本次按用户指定顺序完成三个通知相关需求：合约指数成分变化通知开关、价差告警消息精简、交易所公告首发提速。分支为 `codex/frontend-localization-polish`，开发基线为 `4614af4`。其他模块和现有未跟踪产物不属于本次改动。

## 已实现

1. 指数成分页“监控标的”区域新增“成分变化通知”开关，独立于“自动联动”。状态保存在 `app_settings.index_component_notifications`，未配置时默认开启。关闭后继续记录成分变化，首发与到期走势补发均不发送；已到期的补发会标记为 `muted`。入口：`frontend/src/pages/IndexComponentChangesPage.tsx`、`backend/app/api/routes_index_components.py`、`backend/app/services/index_components.py`。
2. 价差告警默认采用精简格式，包含精确买卖市场身份、盘口开平仓价差、费后开仓估算、两腿当前及预测资金费率和各自结算周期、双边 24 小时成交额、风险标签（存在时）、Astro 卡片结果和必要的交易状态。设置页可切换“精简/详细”，原详细模板选项仍可用。新告警历史与飞书使用同一消息；旧的未评级历史重建仍按详细格式呈现。入口：`backend/app/services/alert_messages.py`、`backend/app/main.py`、`frontend/src/pages/SettingsPage.tsx`。
3. 公告快讯默认轮询由 300 秒改为 30 秒；六家交易所并行拉取，先完成的交易所立即入库并发通知。快讯只取公告列表，跳过文章详情；详情每 10 分钟在独立后台任务补齐。线上公告监控不再执行外部标的资料搜索。首发优先处理较新的公告，日志记录公告发布到发送及发送本身的耗时。入口：`backend/app/services/announcements.py`、`backend/app/main.py`、`backend/app/models/announcement.py`。

## 业务边界

- 指数开关不改变手动/自动监控列表，也不删除已有变化记录。关闭期间检测到的新变化状态为 `muted`；重新开启不会补发这些变化。
- 精简告警的开平仓价差是盘口估算，不代表指定下单量可成交；费后值仍是估算。异周期资金费率按每腿各自周期展示，不将原值直接解释为同持仓时长收益。Hyperliquid DEX、原始市场与非默认倍率保留在买卖腿标识中。
- 公告首发可能缺少仅在文章正文出现的开盘时间；详情后台补齐后用于页面和后续事件提醒，不重发首条。公告源发布时刻、源接口更新、网络与飞书 webhook 仍会贡献实际延迟，不能仅由 30 秒轮询保证绝对延迟。
- 生产库若已有 `app_settings.announcements.poll_interval_seconds` 覆盖值，仍以其值为准；本轮只读核对生产库当时没有该记录，因此升级后会采用新的 30 秒默认值。

## 本地验证

- 指数成分：后端 `test_index_components.py` 与 `test_floating_watch.py` 共 34 项；前端 `IndexComponentChangesPage.test.tsx` 共 10 项，均通过。
- 价差告警：后端消息、主循环、飞书和仓储相关 56 项；前端 `SettingsPage.test.tsx` 15 项，均通过。
- 公告：`test_announcements.py` 与 `test_announcement_research.py` 共 59 项通过，覆盖“慢交易所不阻塞快交易所”和“慢资料研究不阻塞首发”。
- `test_api.py` 告警、历史及模板定向回归 7 项通过，全文件复跑 81 项通过；前端全套 192 项通过，`npm run build` 通过。后端全套测试未在最终提交上完整复跑。
- 本地 API 实测 `/api/index-components/notifications` 返回开启、告警模板为 `compact`、公告轮询默认 30 秒。

## 线上状态与交付

实施前线上运行提交为 `c946aa4`。功能提交 `7a02ce21df06763342c9500726099813bc9a0254` 已推送到 `origin/codex/frontend-localization-polish`；GitHub Actions 后端与前端镜像任务均成功，服务器验证两个对应 SHA 的镜像可用。`deploy/linux-update.sh` 退出码为 0，生产仓库 HEAD 为该功能提交，前后端容器均运行对应镜像且为 `healthy`。

部署前备份为 `backups/radar-before-7a02ce21df06-20260925T135231Z.db`，`integrity_check=ok`，SHA-256 为 `44bdec1d1b1e7543ec88bb8c9f1ebf55c9fda76031f00487ab0a79312bb4c1a3`。脚本按既定保留策略清理一份旧自动备份，保留四份。线上 `/api/health` 返回 `ok`；公告设置接口返回 30 秒轮询；告警模板接口返回 `compact`；受密码保护的成分变化通知接口返回 `enabled: true`；前端指数成分页 HTTP 返回 200。服务器访问和低内存部署方法见 `docs/linux-deployment.md`。本交接文档单独提交，不改变已部署功能版本。

## 已知问题与后续

- 本次上线后尚无新公告的真实发布样本，无法宣称端到端真实延迟已稳定达到某个秒数；待下一条新公告可从 `published_to_send_seconds` 观察。原始生产日志读取被自动审批拒绝，当前验收未导出日志。
- 详细资料自动搜索已从线上链路移除，新公告的“公开资料”字段可能为空；原有历史资料仍保留。
- 开发开始前已有 `.worktrees/`、`output/`、`backend/.pytest_module_prune_20260925/`、其他未跟踪交接文档及 `script/dexe_bybit_bitget_chain.py`，均未纳入本次交付或清理。
