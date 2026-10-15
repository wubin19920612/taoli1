# 交接：跨市场差价交易所补全搜索

日期：2026-09-23（Asia/Shanghai）

## 目标与范围

在标的查询的「跨市场差价」表格增加按交易所名称的快捷搜索与补全，便于从大量
交易对中查找买入或卖出市场。只改前端展示和测试；不改变行情采集、交易判断、
交易执行、账户连接、告警阈值或 Astro 卡片状态。

## 分支与基线

- 分支：`codex/frontend-localization-polish`。
- 开始基线：`f5da589f4f3ffe75df499c7df2dc17dd24e8222a`。
- 功能提交：`6f0e343e41dcdb85a678cee32563671705db4686`，已推送 GitHub。
- 服务器已 `git pull --ff-only` 快进到功能提交；随后交接文档的提交仅更新文档，
  不要求重新构建镜像。

## 已实现与关键入口

- `frontend/src/pages/InstrumentLookupPage.tsx`：在「跨市场差价」标题右侧新增
  AutoComplete 搜索框；选项仅来自当前标的的差价数据，并按交易所展示名排序去重。
  输入时实时匹配买入/卖出两侧交易所的展示名或 ID，不区分大小写；输入完整名称
  或选中补全项时精确筛选该交易所。清空后还原列表和计数。
- 原有 FF/SF/SS/反向 SF 类型屏蔽继续与交易所搜索叠加；只过滤表格行，
  不改原始价差、路由、盘口、资金费率或建卡入口。
- `frontend/src/styles.css`：搜索框宽度；原有标题栏和移动端筛选布局负责换行。
- `frontend/tests/InstrumentLookupPage.test.tsx`：覆盖 `Lighter` 与 `RH Lighter`
  补全区分、从买入/卖出任一侧匹配、大小写、精确选择、清空以及类型筛选叠加。
  Hyperliquid 原始市场/DEX 等其他表格数据保持原样，不按 ticker 替换路由身份。

## 测试与线上状态

- 前端专项测试：`npm test -- tests/InstrumentLookupPage.test.tsx --silent`，
  `22 passed`；`npm run build`（含 `tsc -b`）通过。
- 部署前在线备份：
  `/home/ubuntu/wubin/taoli1/backups/radar-20260923T064040Z-pre-instrument-spread-search.db`。
  大小 `601989120` 字节；SHA-256
  `6eb80ca1b3d2a7f9df3217255353444917667a61acc88e51eb5d321049eb379a`。
  源 `/data/radar.db` 与在线备份的 `PRAGMA quick_check` 均为 `ok`；复制到主机后
  SHA-256 与容器内校验一致。容器内临时副本已删除，没有替换或清理生产数据库卷。
- 服务器只构建修改过的 frontend（`sudo docker compose --progress plain build --pull frontend`），
  随后 `sudo docker compose up -d --remove-orphans`。前端镜像 ID：
  `sha256:d366f5f3790c1949fc002f45f62e5fd440bb727ce711a66d41adc77cbf75acbf`；
  backend 镜像仍为 `sha256:b206e657b4d954390ab9ed47a27a050d9d4db40acdca027a96a88871f73afa93`。
  两容器均 `running/healthy`，服务器本机 `/api/health` 和前端 `/` 均返回 200。
  构建时添加的临时 2 GiB swap 已卸载并删除。
- 经 SSH 只读隧道访问线上 `ANTHROPICUSDT`：原表格 `36 / 36 组`，输入 `li`
  后 `21 / 36 组`，从补全选择 `RH Lighter` 后 `8 / 36 组`，八行均含 RH；
  清空恢复 `36 / 36 组`。390px 移动端输入 `bin` 可补全 `Binance` 并筛出
  `8 / 36 组`。无浏览器 JS 错误，没有提交卡片请求。

## 已知问题与下一步

- 移动端原有浮窗可能覆盖屏幕下半部；搜索框和下拉仍可见可用，本任务未改浮窗。
- 此搜索仅作用于当前标的「跨市场差价」表格，不保存筛选状态；后续如需买/卖腿
  分栏筛选或跨标的全局搜索，应另开任务。
- 原有 `output/**`、pytest 临时目录和其他未跟踪产物均保留。新增的线上验收截图
  `output/instrument-exchange-search-production-desktop.png`、
  `output/instrument-exchange-search-production-mobile.png` 保留为未跟踪产物，不提交。
