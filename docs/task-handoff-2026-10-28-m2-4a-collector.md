# M2-4a：采集器原始快照紧凑存储交接

## 目标、范围与基线

- 按 `docs/m2-4a-collector-compact-raw-spec-2026-10-28.md` §2–§4 实现，仅涉及该节授权的 5 个文件。
- 工作树：`.worktrees/m2-4a-collector`；分支：`codex/m2-4a-collector`。
- 实际开始基线：`ed8eb292dc288b40200c545789d43d773d6c80b2`。任务书标注的 `4ad88b7` 不是本次开始时的 HEAD；未切换或回退基线。
- 开始时工作区干净。按用户要求全程使用 Windows Python 3.12.10，不使用 WSL、不联网，只做本地提交。

## 实现与代码入口

- `backend/app/services/compact_markets.py`：模块加载时从 `MarketSnapshot.model_fields` 确定字段顺序；`pack` 将所有值和 `model_fields_set` 整数位图放进每行 tuple；`CompactMarkets.unpack` 使用 `model_construct(_fields_set=..., **values)` 重建模型。
- list/dict/set 等容器在 pack 时深拷贝；tuple/frozenset 也处理其可变后代。每次 unpack 为每行重新复制容器和字段集合，源对象、同批重复行、历次 unpack 不共享可变容器。不可变叶子值直接保留。
- `CompactMarkets.rows` 和 `mutable_fields` 为普通属性，已有 census 可以沿 `__dict__` 遍历；没有修改 census 注册路径。
- `backend/app/services/collector.py`：仅增加 import、变更 `_exchange_snapshots` 类型、两处结果写入 pack、按交易所播种 pack、按 adapter 顺序 unpack 拼接。
- aliases、prepare、filter、store、slots、调度保持原实现。未到期、冷却和失败继续使用原始快照，每轮重新应用当前转换和核验规则；空成功覆盖旧快照。

## 测试证据

先创建两个测试文件，运行得到预期红灯：`2 errors in 1.01s`，均为尚未实现 `app.services.compact_markets` 的 ModuleNotFoundError，然后实现。

- `tests/test_compact_markets.py`：覆盖全部 49 个模型字段、现货/期货、HL DEX、datetime/None、alias 倍率、显式默认值与未设置字段、空批次、源对象/各轮 unpack/同批重复行的 list 和 fields_set 隔离；1.2 万行保留内存和 unpack 耗时测试默认参与全量。
- `tests/test_collector_compact_equivalence.py`：冻结实际基线的 3 个旧方法，使用独立 fake adapters/repository，真实执行 `apply_symbol_aliases` 和 `MarketValidationService.prepare`。9 轮覆盖播种、到期、未到期、部分成功、market_errors 降级、带部分结果的超时、冷却、恢复、空成功；另有 binding 删除/恢复、FX 过期、盘口缓存失效、10 倍 alias 不累乘。每轮逐项比较 all_markets、markets、opportunities、exchange_errors；同时比较轮询状态和调用序列。机会集有非空断言，过滤前后也有差异断言。
- 静态 AST 复核：collector 仅 3 个授权方法及 `__init__` 标注变化；测试中的 3 个旧方法与实际基线逐 AST 一致。

在 `backend` 下先执行 `New-Item -ItemType Directory -Force ../.pytest-tmp`（PowerShell 对应 `mkdir -p ../.pytest-tmp`）。PowerShell 不会为 pytest 自动展开文件通配符，因此用 `Get-ChildItem` 展开以下集合，再调用 `python -m pytest -q ... --basetemp=../.pytest-tmp/x`：

`tests/test_collector*.py`、`tests/test_snapshot_*.py`、`tests/test_market_validation_*.py`、`tests/test_cache_census.py`，并加入新建的 `tests/test_compact_markets.py`。

指定回归结果：

```text
285 passed, 3 deselected in 19.59s
M2-4a retained: original=37929454 bytes, compact=10389758 bytes, reduction=72.61%
M2-4a unpack 12000 rows: 186.997 ms (budget 150 ms)
```

后端全量命令：`python -m pytest -q --basetemp=../.pytest-tmp/x`。原样结果：

```text
M2-4a retained: original=37929454 bytes, compact=10389758 bytes, reduction=72.61%
M2-4a unpack 12000 rows: 187.974 ms (budget 150 ms)
2059 passed, 4 deselected, 12 warnings in 138.86s (0:02:18)
```

项目默认排除 `network` 和 `benchmark` 标记；未放开外网测试。12 条 warning 来自现有 API/index component 测试的 `datetime.utcnow()` 弃用提示。全量完成后仅整理新文件空行和测试字典字面量，未改变实现语义。

整理后的定向复验：`4 passed, 1 deselected in 0.49s`（只排除已经测量过的 1.2 万行性能用例）。三个新 Python 文件的 `ruff check` 通过；`git diff --check` 通过。

## 性能边界与后续方案

此前指定回归 unpack 为 186.997 ms、全量为 187.974 ms，超过 150 ms 上限。2026-10-28 跟进修复以 `53bbe8ad5d3843cd2e36e2734e7a7e5e93d319e2` 为基线，只修改 `compact_markets.py`、其单元测试和本性能章节。

- 本机 Windows Python 3.12.10、Pydantic 2.12.5；已检查本地 `model_construct` 源码。模块加载时仅当模型无 private attributes、`__pydantic_post_init__ is None`、extra 不为 `allow` 时启用 `_FAST`；否则保留 `model_construct` 回退。
- 快速路径为每行创建 `MarketSnapshot.__new__(MarketSnapshot)`，按顺序以 `object.__setattr__` 写入新 values dict、新 fields set、值为 None 的 extra 和 private 槽位。所有字段值来自已验证快照，不重复执行 Pydantic 的字段扫描。
- pack/unpack 中，普通 list 且元素全部为精确的 str/int/float/bool/None 类型时用 `list(value)`；其他值及容器/元素子类继续 deepcopy，保留其状态和嵌套可变值隔离语义。
- 新增同一组行的快速路径/回退路径对比，核对 `model_dump()`、`model_fields_set`、模型相等比较、模型及字段类型；强制 `_FAST=False` 执行全部字段往返，并增加嵌套可变值的源对象/重复行/多次 unpack 隔离断言。

在 backend 下创建 `../.pytest-tmp`，指定回归包括 `tests/test_compact_markets.py`、`tests/test_collector_compact_equivalence.py` 和展开去重后的全部 `tests/test_collector*.py`。原样结果：

```text
M2-4a retained: original=37929454 bytes, compact=10341942 bytes, reduction=72.73%
M2-4a unpack 12000 rows: 41.872 ms (budget 150 ms)
71 passed in 1.89s
```

随后执行全量命令 `python -m pytest -q --basetemp=../.pytest-tmp/x`。原样性能输出和汇总行：

```text
M2-4a retained: original=37929454 bytes, compact=10341758 bytes, reduction=72.73%
M2-4a unpack 12000 rows: 66.981 ms (budget 150 ms)
2062 passed, 4 deselected, 12 warnings in 137.26s (0:02:17)
```

指定回归和全量 unpack 均低于 150 ms，保留内存缩减 72.73%，满足至少 60% 的要求。12 条 warning 仍为既有 `datetime.utcnow()` 弃用提示。两个修改的 Python 文件通过 `ruff check`，`git diff --check` 通过。

保留内存按 tracemalloc 同一批数据在释放原模型前后测量，包含紧凑记录继续持有的叶子值。unpack 计时关闭 tracemalloc，包含正常 GC 的墙钟耗时，没有禁用 GC 或取多次最小值。以上为本地测试，线上 RSS 和 prepare stage 仍需另行验收。

本次全程离线、未使用 WSL；只做本地提交。原 `.pytest-tmp/x` 已移至同目录下的 `m2-4a-before-fast-x-20261028-135446` 留存，原全量日志未覆盖；新回归日志为 `.pytest-tmp/m2-4a-fast-targeted-pytest.log`，本次全量日志为 `.pytest-tmp/m2-4a-fast-full-pytest.log`。这些未跟踪产物均保留且不纳入提交。

## 交付、线上状态与下一步

- 本次只进行本地 Git 提交，提交信息按用户指定并包含 Co-Authored-By 行。
- 未推送、未连接服务器、未创建线上备份、未部署；线上版本和 RSS/prepare 实际变化均未验证。用户明确要求不联网，任务书 §5 也要求部署另行批准。
- `.pytest-tmp/` 是本次按要求生成的未跟踪测试目录，保留测试产物与 `m2-4a-full-pytest.log`，不纳入提交。没有处理其他工作树或用户产物。
- 后续审阅重点：可变值隔离、字段顺序、fields_set、播种、空成功，以及 unpack 超时记录。独立只读代码审阅仍由后续验收任务执行。
- 后续模块请新建任务；上线按任务书排在 M2-4b 和 Gate 降噪之后，单独测量并审批，完成备份及指定部署验收。回滚采用 revert 本提交。
