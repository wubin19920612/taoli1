# 删除不用的功能：第一阶段（只读盘点）任务书（2026-11-02）

用户已决定删除下列功能。本阶段**只做盘点，不修改任何代码**，产出一份删除清单，交给 Claude 审核后再进入第二阶段实施。

## 要删除的功能
| 功能 | 前端页面 | 功能开关 |
|---|---|---|
| 乌龙回测 | fat-finger | `fat_finger` |
| 资金研究 | funding-research | `funding_research` |
| 负基差埋伏 | negative-basis | `negative_basis` |
| 原油新闻 | oil-news | `oil_news` |
| 机会雷达 | opportunity-radar | `opportunity_radar` |
| 1s 采样 | second-sampling | `second_level_sampling` |
| 挤仓结构观察 | squeeze-arbitrage | `squeeze` |
| 跨所价差 | symbol-spread | `symbol_spread` |
| 价差事件榜 | spread-events | 无 |
| TradFi 价差 | tradfi-perp | 无 |
| 负费率 FS 观察 | fs-monitor | 无 |
| 正差价正费率实盘实验 | 设置页里的一个面板（前端由另一分支删除） | 查后端 |

**保留**：Gate 定时减仓（`gate_twap`），以及上表以外的所有功能。

## 要盘点的内容
对每个功能列出：
1. **后端**：路由文件和端点；service/runtime 模块；后台循环和定时任务（在 `main.py` 的启动/关闭里注册的）；settings 字段和环境变量；仓储类；数据库表（`schema.py`）；告警或其他功能对它的调用。
2. **前端**：页面、组件、api 调用、类型、AppShell 导航和 `pageFeatures`、样式前缀、测试文件。
3. **共享依赖（最重要）**：哪些模块、表、函数同时被要保留的功能使用，删除时必须保留。逐项写清“谁在用”和文件:行号。特别核对：
   - FS 观察和资金费率套利、Astro 建卡、告警引擎（`astro_alerts`、`alert_engine`）之间的依赖；
   - 机会雷达和实时机会（`/api/opportunities`、`spread_engine`）之间的依赖；
   - 价差事件榜和价差历史统计、`pair_spread_query` 之间的依赖；
   - 1s 采样和采集器（collector）之间的依赖；
   - 实盘实验和 arb-mvp 建卡目标（arb_paper/arb_live）、Astro 之间的依赖。
4. **数据库表**：只列出，不删。第二阶段也不写 DROP，旧表留在库里，以后单独处理。
5. **部署与配置**：`docker-compose`、`.env.example`、`DISABLED_FEATURES` 默认值、CI 配置、文档中的引用。
6. **风险点**：删除后可能影响启动、迁移、已有数据读取、或者告警推送的地方。

## 输出
写入 `docs/remove-unused-inventory-2026-11-02.md`。这是本阶段唯一允许新建的文件，写完后 git commit。格式要求：
- 每个功能一节，用表格列出：文件/符号 | 处理（删除 / 保留并说明谁在用 / 修改）| 文件:行号。
- 最后一节“建议的实施拆分”：按互不重叠的文件集合，拆成 2–4 个可以分别实施和测试的工作包，并给出每包的验收命令。

不要 git push。不要修改其他文件。
