# Astro Hyperliquid GC 市场核验交接（2026-10-14）

## 目标与范围

- 模块：Astro 建卡路线。告警、实盘实验、人工建卡、预建和预览按 Gate CrossEx 实时交易对规则判断 HL 是否可走 GC。
- 用户最终选择：原始合约与具体 DEX 完全匹配、且状态为 live，才使用 GC；没有匹配或清单查询失败时回退原始 HL。
- 分支：`codex/frontend-localization-polish`；本地基线 `cf7eecdc3fd0469a3a223ce00088102999a091db`，部署前生产源码及双镜像 `f0371ebd95be0b3c7d2e05d93e0c61c360ba89f3`。
- 不修改交易金额、开卡/禁开状态、资金费周期、行情、手续费、市场倍率、现有卡片或通知开关。

## 官方证据与业务规则

- 2026-10-14 16:06:30（Asia/Shanghai）实查 Gate 公共 `GET /api/v4/crossex/rule/symbols`：HL 共 311 个 live 合约，默认市场 178、xyz 111、para 22；没有 io。
- 当时 `HYPERLIQUID_FUTURE_NET_USDC` 的 `tag=xyz`。`para:NET` 不匹配，不能用同名 xyz NET 替代。支持部分 para 不代表支持整个 para DEX。
- [Gate 官方 API 文档](https://www.gate.com/docs/developers/apiv4/en/crossex/)：`symbols` 可查询指定交易对；`tag` 返回 HL DEX 信息，`state` 表示运行状态，`support_cross` 表示是否支持全仓。
- [Gate 2026-08-13 公告](https://www.gate.com/zh/announcements/article/101144)：已支持 xyz 仅逐仓交易对。`support_cross=false` 不表示不支持交易，此次核验不以它拒绝 GC。
- 使用原始 HL coin 生成 Gate 标识 `HYPERLIQUID_FUTURE_<coin>_USDC`，再核对返回的完整 symbol、exchange_type、business_type、state 和 tag；不会用规范化 ticker 或别名代替原市场。
- 已确认市场仍按原 `both/non_gc/gc` 偏好生成路线。未确认市场整个配对回退普通路线，即使全局为仅 GC 也创建原始 HL；保留 `aHlDex/bHlDex`，不改成默认 DEX。

## 关键入口与实现

- `backend/app/services/gate_crossex_hyperliquid.py`：公共只读接口查询，按 Gate symbol 缓存 60 秒；失败缓存及全局重试冷却 15 秒；请求超时 5 秒；过期旧支持结果不会在故障时继续用于 GC。并发请求共用锁，重复市场只查询一次。
- `backend/app/services/market_labels.py`：`astro_pair_route_variants` 接收已核验的 HL 支持结果，统一选择普通/GC 路线及回退。
- `backend/app/services/astro_alerts.py`：四种实际创建入口在提交前共用市场核验；结果消息显示回退，保留查重、DEX 冲突保护、黑名单、dry-run 及已有 RH-Lighter 自动写入限制。
- `backend/app/api/routes_astro.py`：预览共用核验和路线选择；回退时有效默认类型为 non_gc，避免页面仍选择不可用 GC。不会更改持久化全局偏好。
- `backend/app/main.py`：预览与实际创建共用一个 catalog；HTTP 客户端每次查询自行关闭，无新增后台轮询或长驻客户端。
- 新测试：`backend/tests/test_gate_crossex_hyperliquid.py`；扩展 `test_market_labels.py`、`test_astro_alerts.py`、`test_astro_instrument_routes.py`。

## 验证

- 定向回归 187 项通过，覆盖原始市场/DEX 同名冲突、支持和未支持合约、仅逐仓、四种创建入口、双向 HL、三种卡片偏好以及预览。
- 最终后端回归：catalog、market_labels、planner、alerts、instrument_routes、preadd、instrument_spreads 共 400 项通过（412.86 秒）；加强无效原始市场不得发请求的断言后，catalog 21 项单独通过。
- 新增服务和修改的路线/测试文件 Ruff 检查通过；main.py 只做 catalog 接线，运行错误/语法检查通过，保留原有其他规则的 lint 问题。
- 人工复核固定公共 URL、无认证/秘密、新增缓存的时效/故障回退、SDK payload 不混入内部核验元数据，以及原 DEX 保留。

## 线上状态与交付

- 部署前生产工作区已跟踪文件干净，双容器 healthy，`/api/health` 为 ok，`exchange_errors={}`，剩余磁盘约 13.5 GiB。
- 生产公共 Gate 查询再次确认 BTC 为默认 DEX，CIFR 为 para，NET 为 xyz。旧生产 `/api/astro/preview/b5d3ceb6d1d75534` 对 `NETUSDT -> para:NET` 仍提供 `bitget -> gc-hl`，复现了需要修复的实际行为；只读预览没有创建卡片。
- 尚未部署本次功能；等待最终测试、精确 Git 提交、推送、双镜像构建成功，再通过标准更新脚本备份并快进部署。
- 最终提交、镜像构建、SQLite 备份/校验和线上接口验收将在完成后补充。连接信息与凭据仅使用受控运行环境，不写入文档。

## 已知限制、工作区与下一步

- 正常支持规则最多缓存 60 秒；清单故障时使用原始 HL，首次故障最多等待请求超时。公开清单不能证明具体账户权限或实际 SDK 下单成交。
- 已有卡片不自动修改/删除；已有同名或 DEX 冲突仍遵守原安全保护，不能把新路线核验当成旧卡片修复。
- 保留开始时已有的 `frontend/src/pages/PairMonitorPage.tsx` 和 `frontend/tests/PairMonitorPage.test.tsx` 未提交改动，未纳入本次交付。
- 保留原有 `.worktrees/`、pytest 临时目录、output/script 等未跟踪产物及其他模块交接文档。本次新增未跟踪 `output/gate-crossex-hl-support-20261014.json` 为公共证据快照，`output/astro-hl-dex-ssh-proxy-20261014.py` 为临时本机连接辅助；不提交。
- 下一模块请新建任务并引用本文件；本模块后续可观察正常告警的实际建卡结果，不人为重放历史告警或开启交易。
