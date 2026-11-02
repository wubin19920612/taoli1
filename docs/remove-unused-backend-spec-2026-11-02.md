# 删除不用功能：第二阶段 B 包（后端）任务书（2026-11-02）

依据：`docs/remove-unused-inventory-2026-11-02.md`。清单已经 Claude 审核，按其中“B. 后端卸载与共享契约保护”一节实施。行号以基线为准，实施前重新定位。

## 范围
- **只改 `backend/**`。** 不改 `frontend/**`、`docs/**`（本任务书除外）、部署和 compose 文件，这些由 A、C 包负责。
- 删除 11 项功能的后端部分：乌龙回测、资金研究、负基差埋伏、原油新闻、机会雷达、1s 采样、挤仓结构、跨所价差的 feature guard、价差事件榜、TradFi 价差、FS 观察，以及 live pilot 后端。
- 保留清单“必须优先保护的共享依赖”一节列出的全部内容，Gate 定时减仓（`gate_twap`）保持不变。

## 实施顺序（按清单 B 节）
1. 把 `FORMULA_FAMILIES` / `exchange_formula_family` 迁到 `services/funding_formula_families.py`，并修改 `funding_arbitrage.py` 的导入。行为和 unknown 回退保持不变。
2. 解开 schema 对挤仓包的反向导入：把旧建表、加列定义迁到纯 db 兼容模块，然后删除对 service 包的导入。不执行旧 route 数据复制。
3. 删除专属模块，以及 `main.py` 里对应的实例、任务、close、state 和路由。
4. 裁剪 live pilot：
   - 只删实验分支；
   - 普通告警循环的规则、校验、隔离并发、投递去重和建卡链路保持原样。
5. `/api/pair-spread/symbol-query` 去掉 `require_feature('symbol_spread')`，接口保留。
6. Settings 和功能开关：`OPTIONAL_FEATURES` 只保留 `gate_twap`。8 个退役 key 作为“已知退役值”被接受并忽略，不报错；未知拼写仍然报错。
7. 同步更新 `cache_census`、测试、fake settings 和 patch 目标。

## 硬约束
- **数据库**：不写 DROP，不删表、不删行，不改 CHECK 约束，不新增数据迁移。旧表的建表和加列定义可以保留。
- **共享逻辑**：不删除共享的 repositories 或 routes_settings 文件，只删其中的专属方法；不改动普通 alert-loop、FeishuNotifier、电话告警、preadd、溢价告警、账户服务和 history recorder 的行为。
- **测试**：
  - 不放宽已有共享测试的断言。删除专属测试文件可以；混合测试只删退役场景。
  - 按清单 B 节“必须加入／调整的回归场景”补齐测试，包括：新库和已有库的 lifespan 启停、退役开关兼容、退役端点 404、symbol-query 正常返回、已删除的任务不再注册、普通告警隔离不变。
- **Git**：不要 git push。

## 测试环境
必须使用 CI 锁定依赖的 Python。在 `backend` 目录下执行：

```
C:/Users/wubin/AppData/Local/Temp/claude/D-------claude---/7f276a81-4df2-4527-bc65-b410f9d557b6/scratchpad/venv-lock/Scripts/python.exe -m pytest -q --basetemp=<工作树内的临时目录>
```

该解释器已确认从当前工作树导入 `app`。不要 pip install，也不要修改这个环境。不要运行 WSL。

## 验收
1. 清单 B 节列出的定向测试全部通过。
2. 全量 `pytest -q` 全部通过，贴出汇总行。
3. `git diff --check` 无输出。

最后在当前分支提交，提交信息用 `refactor(backend): remove retired features ...`。报告需包括：
- 删除的模块和端点；
- 迁移的共享符号；
- 退役开关的兼容做法；
- 新增的回归测试；
- live pilot 删除后普通告警链路的对照说明（列出改动点的文件:行号）。
