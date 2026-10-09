# 交接：标的查询隐藏 Astro 路由证据区

日期：2026-09-23（Asia/Shanghai）

## 目标与范围

用户不需要在标的查询中专门看到「Astro 路由证据」面板。本任务仅删除该面板及
对应样式；保留真实市场列表、价差、交易可用性和 Pair 精确跳转。后台已有的
路由发现与匹配数据不变；不修改行情采集、账户连接、交易执行、告警阈值或卡片状态。

## 分支与基线

- 分支：`codex/frontend-localization-polish`。
- 开始基线与上一模块交接提交：`65ca7eee53f2d91f610dd5b927aa4d509e363968`。
- 功能提交：`e732c1803acf79250949fb13a17fc73f98776c9e`，已推送并部署。
- 文档后续单独提交，只同步工作树，不重新构建已经部署的功能镜像。

## 已完成与代码入口

- `frontend/src/pages/InstrumentLookupPage.tsx`：删除「Astro 路由证据」卡片和
  该区块的错误展示；删除该页面不再使用的 `astroRoutes`/`routeErrors` 局部变量。
- `frontend/src/styles.css`：移除只服务于被删除区块的桌面和移动端 CSS。
- `frontend/tests/InstrumentLookupPage.test.tsx`：测试确认即使 API 返回 Astro
  路由证据，页面也不显示面板，同时精确市场仍区分普通 Lighter、RH Lighter 和
  Hyperliquid 的 `io` DEX，Pair 跳转仍携带原始市场与倍率。
- `backend/app/api/routes_instruments.py` 未修改：仍可从 Astro 卡片识别路由和
  真实市场，但卡片不作为价格源；删除前端面板不代表停止后台路由发现。

## 测试与线上状态

- `npm test -- tests/InstrumentLookupPage.test.tsx --silent`：`22 passed`。
- `npm run build`：`tsc -b`、Vite 构建成功。`git diff --check` 通过。
- 部署前源 `/data/radar.db` 和 SQLite 在线备份均 `PRAGMA quick_check=ok`。
  主机备份：`/home/ubuntu/wubin/taoli1/backups/radar-20260923T073854Z-pre-hide-astro-route-panel.db`，
  大小 `602824704` 字节，SHA-256
  `67333410b267966301b46b47199d1c5fc52def23fde46ff051d63fda17e47c15`；
  复制后与容器内副本哈希相同。只清理本次容器临时副本，保留数据库与数据卷。
- 服务器 `git pull --ff-only`，只构建修改过的 frontend：
  `sudo docker compose --progress plain build --pull frontend`，然后
  `sudo docker compose up -d --remove-orphans`。前端镜像 ID 为
  `sha256:2b535de9d17fd76c6fded18f820d347e1f19245a82437a23d4f26a55f2ad1a66`，
  backend 镜像仍为 `sha256:b206e657b4d954390ab9ed47a27a050d9d4db40acdca027a96a88871f73afa93`。
  两容器均健康，`/api/health`、前端首页返回 HTTP 200。构建用临时 swap 已移除。
- 线上无登录态浏览器实测桌面和 390px 移动端：专用标题数量为 0；
  精确市场仍分别显示普通/RH Lighter，8 个 Pair 跳转按钮存在；
  交易所搜索仍可把 `ANTHROPICUSDT` 的差价从 `36 / 36` 筛为 `8 / 36`。
  没有页面 JS 异常或建卡请求。

## 已知问题与下一步

- 标的查询 API 仍会请求 Astro 路由数据并返回 `astro_routes`/`route_errors`，
  但前端不再展示。若希望一并省去该后台请求及其响应字段，应作为独立后端兼容性
  任务处理，不在本次展示调整中扩大范围。
- 保留所有已有未跟踪 `output/**`、pytest 临时文件及其他任务产物，没有提交。
