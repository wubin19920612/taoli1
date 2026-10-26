# M2-3 内存治理清单（Claude，2026-10-26，依据 M2-2 线上普查，待用户确认）

## 1 普查结果（生产 `cfd79d6`，17:34–18:35，预算 8 秒 / 3 秒）

- 11 轮，0 跳过，0 错误，0 OOM，0 重启。每轮阻塞 3.5–5.4 秒，普查自身让 RSS 临时增加 12–30MB。
- 第 1 轮（17:39）**完整，没有截断**：去重后 56.9 万个对象，可归属体积合计 **257MB**；当时 rss_anon 638MB，启动时 258MB。
- 第 2 轮起达到 60 万的对象上限，排在后面的 35 个目标（从 `fs_monitor.providers.1.cache` 开始）被截断，所以排名以第 1 轮为准，增长趋势看后续各轮。以后再跑普查，建议设 `MEMORY_CENSUS_MAX_OBJECTS=1000000`（多占约 30MB）。
- 1 小时内 rss_anon 从 638MB 涨到 718MB（+80MB），能看到的增长主要在 `market_validation` 的几个容器（见 #2）。

| 排名 | 容器（代码位置） | 第 1 轮 | 1 小时后 | 条目数 | 性质 |
|---|---|---|---|---|---|
| 1 | `fs_monitor.providers.1.cache`（`fs_public.py:354` `binance_specs`） | **71MB** | 被截断 | 6 | Binance 现货和合约 `exchangeInfo` 的**完整原始 dict**，一直常驻；查询时超过 20 秒就重新下载、重新解析 |
| 2 | `market_validation.registry.bindings`（`market_identity.py:18`） | 50MB | 50MB | 1.9 万 | 身份绑定，每条约 2.6KB 的 pydantic 对象，`replace()` 时做 deep copy |
| 3 | `snapshot_store._markets` | 44MB | 44MB | 1.2 万 | 当前快照，必需 |
| 4 | `market_collector._exchange_snapshots` | 42MB | 42MB | 10 | 采集器按交易所保留的**另一套**快照对象（与 store 不共享） |
| 5 | `snapshot_store._opportunities` | 32MB | 20MB | 1.6–2.6 千 | 机会快照，必需，随行情波动 |
| 6 | `market_validation.books`（`market_validation_review.py:34`） | 9.8MB | **27MB** | 362→1000（上限） | 订单簿，每条约 27KB，上限 1000 |
| 7 | `market_validation.reviews` | 0.8MB | 7.6MB | 100→1000（上限） | 审核结果 |
| 8 | `market_validation.review_attempted` | 0.1MB | 2.2MB | 396→4218（上限 5000） | 时间戳 |

**未归属**：按第 1 轮估算约 120MB（638 − 258 − 257）。这一部分包括分配器碎片、已释放但没还给系统的内存、原生库，以及没登记的对象。

## 2 治理清单（按“收益 ÷ 风险”排序）

| 编号 | 内容 | 预期降幅 | 风险 | 说明 |
|---|---|---|---|---|
| **M2-3a** | `fs_public` 的 `binance_specs` 只保留实际用到的字段（`current_instruments` 和 `meta` 用到的 status、filters 里的 stepSize/minQty/minNotional 等），按 symbol 存精简后的 dict；TTL 从 20 秒延长到 10 分钟（规格很少变）；解析完立刻丢掉原始 payload | **−55~65MB 常驻**，同时消除每 20 秒一次的大 JSON 解析尖峰 | 低–中 | FS 链路比较敏感：只改数据缓存的形状，**不改 fs_astro attempt 路径和任何判断逻辑**；用新旧实现对照测试保证 `current_instruments` 的输出完全一致 |
| **M2-3b** | `market_validation.books`：只保留审核真正用到的档位深度（例如前 N 档），上限从 1000 降到 300，并加 TTL；`reviews`/`_last_incident` 同样加 TTL | −15~25MB，平台期更低 | 低 | 先查清审核用到多少档、过期语义是什么，写进任务书 |
| M2-4a | 采集器与 store 共享快照对象，或者发布后释放采集器那一份（两份快照同时常驻） | −30~40MB | 中–高 | 原本就是 M2-4 的范围，先出设计并在本机测量 |
| M2-4b | 身份绑定瘦身：`replace()` 不再 deep copy（输入本来就是新从库里读出来的），或者改成更轻的不可变结构 | −10~30MB | 中 | 先确认 deep copy 是为了防止什么被改动 |

**预计**：M2-3a 加 M2-3b 做完后平台期约 630–650MB，M2-4 做完后约 590–610MB，可以达到“稳定在 600MB 以下”的目标。

## 3 建议

1. 先做 M2-3a：单项最大，风险可控，还能顺带改善尖峰。任务书写好后给用户确认。
2. M2-3b 和 M2-3a 改的文件不重叠，可以并行。
3. 上线后用探针（不开普查）采 1 小时，与本次对比：底部和尖峰都应该下降。
