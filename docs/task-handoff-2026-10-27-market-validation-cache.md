# M2-3b 市场审核缓存交接（2026-10-27）

## 范围与基线

- 模块：市场审核缓存瘦身；依据 `docs/m2-3b-market-validation-cache-spec-2026-10-27.md`。
- 工作区：`.worktrees/m2-3b-books`；分支：`codex/m2-3b-books`。
- 开始时 HEAD：`dafc8b412b7ddf8e0687b900e70087540cc34630`，工作区干净。任务书指定的代码基线为其父提交 `f7f56a9`，两者之间为任务书文档提交。
- 只修改 `backend/app/services/market_validation_review.py`，只新建本交接文档及 `backend/tests/test_market_validation_cache_slim.py`。没有修改任务书、其他源码、配置或测试。
- 本交付只创建一个本地提交，信息为 `perf: slim market validation book and review caches (M2-3b)`；交付提交号可由包含本文的提交取得。
- 按用户本次明确限制，不联网、不调用 WSL、不推送、不部署。

## 实现与代码入口

- `_book()`：归一化、排序、multiplier/fx 换算与完整 20 档返回值保持原样；缓存另存一份 `OrderBookSnapshot`，只含 `bids[:1]` / `asks[:1]`，其他字段不变。
- `prepare()` 开头：删除 `reviews` 中 `expires_at <= now` 的载荷。未增加 books TTL，仍使用每次调用的 risk 判断报价是否过期。
- `_background_review()`：成功记录 incident 后，按原有规则写入去重时间并清理距采样 now 至少 60 秒的时间载荷；60 秒内仍去重。
- `_ExpiryCache`：为 reviews 和 incident 保存有界淘汰顺序，过期后只保留键和 `None`，不保留审核对象或过期时间对象。reviews 继续按最后写入顺序淘汰，incident 继续按首次插入顺序淘汰；两者逻辑上限仍是 1000。
- `_bounded_put()`：仅对上述专用缓存转交 `put()`；books、failures、review_attempted 仍走原有实现。
- `refresh()` / `invalidate()` 的代码完全未改。专用缓存的 `clear()` 同时清除载荷和顺序元数据，继续由原有清空调用触发。
- `_review()`、`review()`、`annotate()`、`validate()`、`_cached_book()`、`_book_row()` 的方法体完全未改；锁、信号量、`_mutation_epoch`、`review_inflight` 和调度规则未改。没有修改 validator、identity、adapter 或 cache census 路径。

## 对任务书设计的补充

没有放宽 §3 或扩大 §4 文件范围，但对 §2 的“直接删除过期条目”补充了轻量淘汰顺序元数据。原因是直接删除会改变容量到达 1000 时的业务行为，已先用测试复现：

1. 最早条目 A 仍有效，中间条目 B 已过期，缓存合计 1000 条。
2. 清理 B 后再插入 C，旧实现会淘汰 A，简单清理实现会保留 A。
3. 同一 `validate()` 因而由旧版的 `pending_review` 变成 `passed`；incident 同类序列还会少记录一次。

新增两个容量边界测试先得到 RED，再引入 `_ExpiryCache` 得到 GREEN，保持旧版容量淘汰结果。顺序元数据各最多保留 1000 个键，不持有过期载荷；弱引用测试确认过期审核对象实际释放。

注意：`cache_census` 未修改；其映射遍历不包含专用缓存实例属性中的 `_order`。如需测量 reviews/incident 的总可达体积，应对 `(cache, cache._order)` 一起调用 `deep_size`，避免漏计顺序元数据或重复归因。下列 books 数字不涉及此项，因为 books 仍是原有 `OrderedDict`。

## 离线体积对比

fake adapter 为 1000 个不同原始市场各提供乱序 20 档订单簿。新旧实现分别写满 books，然后各自调用现有 `app.runtime.cache_census.deep_size`，对象预算 1,000,000、独立截止时间，均未截断。

| 指标 | 旧实现 | 新实现 |
| --- | ---: | ---: |
| books 条目 | 1000 | 1000 |
| 可达体积（字节） | 22,654,441 | 2,526,441 |
| 不重复对象数 | 208,028 | 18,028 |
| 每侧缓存深度 | 20 档 | 1 档 |
| `_book()` 返回深度 | 20 档 | 20 档 |

减少 **20,128,000 字节（88.8479%）**，达到至少减少 80% 的门槛；新缓存约 2.526 MB，低于 3 MB 的预期目标。这是固定离线样本的 Python 可达体积，不是线上普查、RSS 或容器内存下降实测。

## 测试与审核证据

测试都从 `backend` 目录运行，所有 pytest 命令均显式指定 `--basetemp=../.pytest-tmp/<子目录>`。沿用已有离线网络拦截 fixture，默认排除 network / benchmark 标记，不访问外部服务。

- 旧实现参照：测试内冻结复制了 `dafc8b4` 的 8 个方法：构造函数、`_bounded_put`、`_book`、`_cached_book`、`_book_row`、`prepare`、`validate`、`_background_review`；AST 对照确认与基线相同。未改的深度审核流程共用原业务实现，通过独立旧 `_book` 返回值做新旧对比。
- 初始 RED：指定五个回归文件与新测试一起跑，**12 failed, 86 passed**（7.13 秒）；失败覆盖完整缓存未瘦身、reviews/incident 未清理和体积降幅为 0。
- 补充 RED：任务移除断言先复现循环变量覆盖后台任务 key 的中间实现问题，**3 failed, 23 deselected**；最终通过专用缓存避免此问题。
- 容量边界 RED：**2 failed, 26 deselected**，复现上述审核命中及 incident 次数差异。
- 最终 GREEN：指定回归与新测试 **102 passed**（8.51 秒）；其中新增测试 30 项，覆盖 multiplier/fx、时间来源、revision/fingerprint/fx 不匹配、stale 边界和更长 risk 窗口、两次审核的深度不足/充足/第二轮不足、过期等号边界、调度命中、incident 参数/次数/任务移除、容量淘汰、对象释放及清空。
- 最终后端全量：**1956 passed, 4 deselected, 12 warnings**（123.41 秒）。排除项来自默认 network / benchmark 过滤；警告来自未修改文件中的 `datetime.utcnow()` 弃用调用。
- `ruff check --no-cache backend/app/services/market_validation_review.py backend/tests/test_market_validation_cache_slim.py` 与 `git diff --check` 通过。
- 复查未改方法体和冻结参照的 AST；只对新增 helper、两个缓存实例类型、专用缓存写入、清理入口和 books 副本做变更。

最终 GREEN 命令：

```powershell
python -m pytest tests/test_market_validation_review.py tests/test_market_validation_prepare_copies.py tests/test_market_validation_astro.py tests/test_market_identity_validation.py tests/test_orderbook_validator.py tests/test_market_validation_cache_slim.py --basetemp=../.pytest-tmp/m2-3b-final-regression -q -s --tb=short
python -m pytest --basetemp=../.pytest-tmp/m2-3b-release-full -q --tb=short
```

## 本地与线上状态、下一步

- 开始时无既有修改或未跟踪文件。本次保留 `.pytest-tmp/` 下的 RED、GREEN、容量边界及全量测试产物，不加入提交；目录内可能含测试数据库与 pytest 生成文件。既有忽略规则下的 pytest/Python/ruff 缓存也不加入提交。
- 不更改 `.gitignore`、`docs/next-task.md` 或任何其他交接文件，以遵守本次三文件范围。
- 未联网核对远端或生产版本，没有推送、线上数据库备份、部署或线上验收；不据本地测试推断线上 RSS 或审核事件比例。
- 后续上线请在新任务中按任务书 §5 确认 feature-flags / M2-3a 前置验收、至少一小时间隔、备份及部署批准，再对比 books/reviews 普查与 `SPREAD_REVIEW` open/resolved 比例。
