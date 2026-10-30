# MarketKey 编码缓存交接（2026-10-28，第二版）

## 当前状态与范围

第二版仅保留 key 编码 LRU，已删除构造缓存，`from_snapshot` 已恢复基线实现。编码缓存专项测试、指定回归和后端全量验收全部通过；交付版本以包含本文件的本地提交为准。

全程使用本机 Windows Python；不运行 WSL、不联网、不安装依赖。用户明确禁止联网，因此本次不 push、不备份线上数据库、不部署、不进行线上检查。上线需另行授权。

- 任务书：`docs/marketkey-cache-spec-2026-10-28.md`，以 §6 第二版为准。
- worktree：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\marketkey-cache`。
- 分支：`codex/marketkey-cache`；本次开始时 HEAD：`55dac2999fa6d693e8998aa8627e8c7ca0d94a93`。
- 任务书记录的业务代码基线为 `8779d40`，线上状态本次未联网核实。
- 接手时已有第一版未提交的模型改动、新增测试及交接文档，以及用户修订后的第二版任务书；另有未跟踪的 `.pytest-tmp/`。
- 本次提交严格限于任务书、`backend/app/models/market_identity.py`、`backend/tests/test_market_key_cache.py` 和本交接文档四个文件。任务书保留用户已修订的第二版内容。
- 不修改其他已有测试；不修改 `docs/next-task.md` 或 `.gitignore`，以遵守任务范围。

## 第一版冲突与原始结果

第一版同时缓存 JSON 编码和 `from_snapshot` 构造结果，两个 LRU 的 `maxsize` 均为 32768；当时的内存上限为 6,000,000 字节。未修改既有测试或放宽失败断言，第一版未通过验收，未提交。

第一版全量命令为 `python -m pytest -q --basetemp=../.pytest-tmp/x`，原始结果：

```text
MarketKey caches retained memory (12400 identities): 12477386 bytes (12.477 MB); limit=6000000 bytes
MarketKey microbenchmark (12400 rows x 4 key reads): legacy=0.192254s, cached_cold=0.161999s, cached_warm=0.023981s
3 failed, 2168 passed, 4 deselected, 12 warnings in 183.73s (0:03:03)
```

失败项：

- `tests/test_market_identity_registry_slim.py::test_replace_isolates_input_mutations_and_releases_full_binding`
- `tests/test_market_identity_routes.py::test_identity_write_auth_revision_validation_and_invalidation`
- `tests/test_market_key_cache.py::test_retained_memory_for_12400_cached_identities`

第一版指定回归最终为 `6 failed, 112 passed in 18.59s`。除上述身份相关用例外，后续四个 Astro 入口用例也受同一进程内的缓存实例污染影响；全量运行中，新增缓存测试的清理 fixture 在后续 Astro 用例前清空了缓存，因此失败数不同。

冲突一：12,400 个身份的双缓存实测保留 12,477,386 字节，超过 6 MB。原测试在创建输入行之后开始 tracing，但仍须计入构造缓存新建并保留的 Pydantic 模型、规范化字符串以及编码缓存结构和结果。第一版独立探测记录：预建输入时，仅持有 12,400 个普通 MarketKey 就约需 7,151,096 字节（含列表），仅构造缓存约需 9,220,248 字节，不能靠移动统计起点规避原预算。

冲突二：Pydantic 的 frozen 阻止普通赋值，但既有注册表隔离/释放用例会用 `object.__setattr__` 修改 `source.market.raw_symbol`，并要求注册表查询仍然隔离、source 的 weakref 可释放。构造缓存共享并强引用该实例，导致后续查询复用被修改的对象、阻止回收，并污染后续用例。第一版将 `from_snapshot` 在单独测试进程中临时替换为旧构造实现后，该用例得到 `1 passed in 0.11s`。

第一版曾提出放宽到 14 MB 并增加篡改检测/失效重建的建议，未获批准。第二版 §6 已明确取消构造缓存，该建议不再作为后续实施方向。

## 第二版实现与结论

- 模型模块仅新增 `MARKET_KEY_CACHE_SIZE = 32768` 和 `@lru_cache(maxsize=MARKET_KEY_CACHE_SIZE)` 包装的 `_encode_key(exchange, market_type, dex, raw_symbol) -> str`。
- `_encode_key` 仍执行原有 `json.dumps([...], separators=(",", ":"))`；`MarketKey.key` 调用该函数，DB 编码与转义规则保持原样。
- 完全删除 `_cached_market_key`、相关 Enum 导入以及 `from_snapshot` 中的缓存分支。已用 AST 提取方法源码，确认 `from_snapshot` 与 `8779d40` 和本次 HEAD 逐字一致；`git diff` 中没有该方法的改动。
- 保留既有字段、校验、直接构造、`model_validate` 和模型生命周期；缓存只保存四元组参数与编码字符串。
- 必须保留 exchange、market_type、dex、raw_symbol 四个维度；Hyperliquid 的 main、不同 DEX、原始市场大小写及冒号均参与编码，不按规范化 ticker 合并。

第二版测试删除了构造缓存专属的对象复用、异常缓存、子类绕过与构造生命周期测试，仅保留编码缓存验收；本任务新增用例从第一版 51 项变为 33 项。既有注册表、路由和其他测试文件均未修改。

## 第二版测试方法与已完成验证

`backend/tests/test_market_key_cache.py` 共 33 项，保留基线 JSON 编码实现供对照，覆盖：

- 新旧输出逐 UTF-8 字节等价；HL main、不同 DEX、冒号、大小写、首尾空白、Unicode、转义、`dex=None`、字符串枚举；同时检查原始四元组和规范化后的模型属性，覆盖冷缓存与重复读取。
- 相同四元组在不同模型实例间返回同一 str 对象，核对命中、未命中与条目数；四个身份维度不会互相混用。
- LRU `maxsize=32768` 及超容量逐出。
- 预先创建 12,400 个 MarketKey，再启动 tracemalloc、读取 key 并 GC，统计编码缓存新增保留内存，包含缓存字典/四元组/节点及编码结果，排除预建模型及其既有字段；严格断言 `retained <= 4_000_000`，不放宽上限。
- 12,400 行 × 每行 4 次 key 读取的微基准；旧编码和新编码都经过同一未缓存的 `MarketKey.from_snapshot` 构造路径，打印旧实现、冷缓存和暖缓存耗时，校验相同的结果长度总和，不设置波动性的耗时断言。

4 MB 是任务书规定的 12,400 个不同 key 的验收上限；LRU 按条目数限制为 32768，不是按字节驱逐。

在 `backend` 下先运行 PowerShell `New-Item -ItemType Directory -Force -Path ../.pytest-tmp`，等价创建用户要求的目录，保留第一版的测试产物。

专项命令：`python -m pytest -q tests/test_market_key_cache.py --basetemp=../.pytest-tmp/cache-v2`。

```text
MarketKey encoding cache retained memory (12400 identities): 3257138 bytes (3.257 MB); limit=4000000 bytes
MarketKey microbenchmark (12400 rows x 4 key reads): legacy=0.079300s, cached_cold=0.043791s, cached_warm=0.024995s
33 passed in 0.84s
```

指定回归：用 PowerShell 枚举 §4 的 `tests/test_market_identity*.py`、`tests/test_market_validation_*.py`、`tests/test_pair_validation*.py`，共 9 个文件；最后一个模式当前没有匹配文件。运行 `python -m pytest -q @regressionFiles --basetemp=../.pytest-tmp/regression-v2`：

```text
118 passed in 16.88s
```

以下检查通过：

```text
python -m ruff check app/models/market_identity.py tests/test_market_key_cache.py
All checks passed!
python -m ruff format --check app/models/market_identity.py tests/test_market_key_cache.py
2 files already formatted
```

## 第二版全量验收

在 `backend` 下运行指定命令，退出码为 0：

```text
python -m pytest -q --basetemp=../.pytest-tmp/x2
```

该次全量运行的原始指标与汇总：

```text
MarketKey encoding cache retained memory (12400 identities): 3257138 bytes (3.257 MB); limit=4000000 bytes
MarketKey microbenchmark (12400 rows x 4 key reads): legacy=0.136390s, cached_cold=0.053104s, cached_warm=0.029315s
2153 passed, 4 deselected, 12 warnings in 131.21s (0:02:11)
```

4 deselected 来自项目既有 `not network and not benchmark` 配置；本任务新增的微基准未标记为 benchmark，实际执行。12 warnings 均为既有 `datetime.utcnow()` 弃用警告。第一版失败的注册表隔离/释放与身份路由用例，以及指定回归中的 Astro 用例全部恢复通过，无需修改既有测试。

## 交付与下一步

全部本地验收已通过，仅将四个任务文件加入以下本地提交：

```text
perf: cache MarketKey key encoding

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

`.pytest-tmp/` 中的 SQLite 数据库、第一版证据和第二版日志均保留、不提交。第二版日志为：

- `.pytest-tmp/marketkey-cache-v2-focused.log`
- `.pytest-tmp/marketkey-cache-v2-regression.log`
- `.pytest-tmp/marketkey-cache-v2-full.log`

本次仅作离线本地验收；微基准为本机单次测量，不代表生产收益。后续如需推送或上线，应另开经授权的交付任务，核对目标提交、备份线上数据库并按部署文档验证。其他模块另开任务，使用本交接文档继续。
