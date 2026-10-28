# M2-4b 身份注册表瘦身交接（2026-10-28）

## 目标、范围与基线

- 任务书：`docs/m2-4b-identity-registry-slim-spec-2026-10-28.md`，用户已确认。
- 工作树：`.worktrees/m2-4b-identity`；分支：`codex/m2-4b-identity`。
- 开始时 HEAD：`8bf1a70`（任务书提交）；实现基线：`4ad88b7`。开始时工作区干净。
- 验收目标：注册表仅保留标注需要的 13 个字段，保持输出和输入隔离行为，2 万条绑定的 `deep_size` 至少减少 70%。
- 按用户要求只修改/新建以下 3 个文件；本轮只做本地交付并提交，不推送、不部署，不使用 WSL。

## 已完成改动与关键入口

1. `backend/app/services/market_identity.py`
   - 新增 `_RegistryEntry`，使用 `@dataclass(frozen=True, slots=True)`，只保存读取所需的 13 个字段。
   - `from_binding()` 构造时计算并保存原 `MarketBinding.fingerprint`；`settlement_currency` 和 `spec_fingerprint` 仍通过原算法参与指纹，不保留在条目中。
   - 用 `sys.intern()` 复用资产名称、资产标识、报价币、跟踪定义、状态等字符串。
   - `replace()` 原子替换 `MappingProxyType`，值改为精简条目；不再深拷贝或持有完整绑定、市场模型及 evidence。
   - `get()` 返回类型改为 `_RegistryEntry | None`。
   - `__init__()`、`annotate()` 和 `annotate_all()` 的 AST 与旧版一致；标注读取的 `fingerprint` 现在是预计算字段。
2. `backend/tests/test_market_identity_registry_slim.py`
   - 完整复制旧类为 `LegacyMarketIdentityRegistry`，与实现基线的类结构进行 AST 核对。
   - 15 个测试用例（含参数化）：新旧逐字段类型及 `model_dump()` 比较，输入修改隔离及原对象释放，冻结/slots/只读映射，替换/空表/重复键，指纹只在构造时计算，重复字符串共享，以及体积门槛。
3. 本交接文档。

## 业务规则与等价性覆盖

- 保留完整 `MarketKey` 编码，包含交易所、市场类型、DEX、原始市场符号；不按规范化 ticker 合并。
- 覆盖 pending、verified、expired、conflict，有 evidence/有效期，单位核验与价格倍率，合约面值冲突，valuation 跟踪定义。
- 覆盖 settlement_currency/spec_fingerprint 不同导致的指纹差异；输出指纹仍与旧 SHA-256 算法相同。
- 行情覆盖命中、未命中、DEX 键冲突引发 `ValueError`、已有 alias 倍率、空价格/数量、不同报价时间来源。
- 两种模式（enforce/shadow）与有效期前、恰好到期、到期后、带偏移时区及无时区的 now 均比较输出；保留 datetime 类型和原有效期表示。
- 输入字段、嵌套 evidence/query 和原市场对象随后发生修改，已发布的标注结果保持不变；旧映射快照在 replace 后仍可读。

## 体积对比

同一进程构造 20,000 条 verified 绑定，每条 5 条 evidence，对比旧深拷贝实现和精简实现的 `registry.bindings`：

| 实现 | deep_size 估算字节 |
| --- | ---: |
| 旧实现 | 158,592,230 |
| 精简实现 | 7,375,242 |
| 减少 | 151,216,988（95.3496%） |

以上数字来自新测试与指定回归全部通过时的输出，满足至少减少 70% 的要求。测量使用 `app.runtime.cache_census.deep_size()`，`max_objects=2_000_000`，每次 deadline 为 `time.monotonic() + 60`；两次均未截断。该工具对超过 2,000 项的容器采样，具体估算值会随对象布局波动。这是合成数据的对象体积估算，不是进程 RSS，也不是线上 50MB 注册表的实际测量结果。

## TDD 与验证结果

环境：Windows PowerShell，Python 3.12.10。测试在 `backend` 目录运行，沿用项目离线网络阻断 fixture，未使用 WSL。

1. 先新增测试，生产实现尚未改动时运行新测试及指定回归：`4 failed, 211 passed, 3 deselected in 24.93s`。失败项为精简冻结结构、指纹预计算、字符串去重、体积门槛；等价性及输入隔离通过。旧/旧体积对比减少 0%，证明体积测试能拒绝旧实现。
2. 修改实现后，新测试及指定回归：`215 passed, 3 deselected in 17.59s`。
3. 修正测试辅助函数的两处 Ruff `C408` 提示后，后端全量：`2069 passed, 4 deselected, 12 warnings in 130.03s`。
4. `python -m ruff check backend/app/services/market_identity.py backend/tests/test_market_identity_registry_slim.py`：通过；`git diff --check`：通过。
5. AST 核对：测试中的旧类与开始时 HEAD 的旧类一致；生产 `__init__`、`annotate`、`annotate_all` 保持不变。

从仓库根目录先创建 `.pytest-tmp`，再进入 `backend`。新测试及回归命令：

```powershell
python -m pytest -q --basetemp=../.pytest-tmp/x tests/test_market_identity_registry_slim.py tests/test_market_identity_validation.py tests/test_market_validation_review.py tests/test_market_validation_astro.py tests/test_market_validation_prepare_copies.py tests/test_market_validation_cache_slim.py tests/test_cache_census.py -rP
```

后端全量命令：

```powershell
python -m pytest -q --basetemp=../.pytest-tmp/x
```

全量的 12 条警告来自 `test_api.py` 和 `test_index_components.py` 涉及的 `datetime.utcnow()` 弃用提示；本轮未扩大范围处理。network/benchmark 标记按项目默认配置排除，未运行外部服务测试。

## Git 交付、线上状态与未做事项

- 本次提交只包含上列 3 个文件。提交标题：`perf: slim market identity registry entries (M2-4b)`；正文末尾保留用户指定的 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。
- 初始无其他未提交改动。测试生成的 `.pytest-tmp/x/` 保留为未跟踪产物，不纳入提交；pytest/Python/Ruff 忽略缓存也不纳入提交。
- 未修改模型、数据库结构、API、刷新频率/锁、cache census 路径、前端或其他文档（包括 `docs/next-task.md`），遵守 3 文件边界。
- 按用户要求未推送；未访问生产环境，未备份线上数据库、部署、检查线上健康接口或核验线上版本。
- 线上 RSS/CPU 收益、上线前后 `/api/market-identities` 状态分布均未验收；不能用本地体积估算替代上线验收。
- 下一步另开上线任务：先按任务书确认锁修复观察期和生产基线，再获得部署批准；核对任务书指定的 `output/sqlitefix-isolated-deploy-20261028.sh` 副本及当前部署流程后执行备份、部署与线上对比。回滚方式为 revert 本次提交。
