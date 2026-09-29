# OKX 公告解析与容错优化

## 范围与基线
仅公告模块，分支 codex/frontend-localization-polish，基线 7167870；修改前线上版本 3e34ce6。主 API 原本正常，2026-09-29 Lisk 充提公告发布后约 42 秒入库，状态 sent。网页虽然 200，但旧解析器未读取 appState 中无 URL 的文章，解析为零。

## 实现及业务规则
入口 backend/app/services/announcements.py 的 OKXAnnouncementProvider。
- 从 appState.appContext.initialProps.sectionData.articleList.list 提取 slug、title、publishTime、sectionSlug，构造官网文章链接；只接受合法 slug 和有效发布时间，不用抓取时间替代发布时间。
- 三个 API 与两个网页入口独立并发请求，单源 HTTP/解析/业务错误不丢弃其他来源；零解析明确警告，所有来源无有效结果时抛错。
- 按文章 URL 的稳定 slug 合并中英文和跨源重复记录，优先保留 API 的更精细发布时间和详情。来源名和公告标识保持一致，沿用数据库去重与 30 分钟通知时效，不人工补发。
- 不新增依赖、数据库迁移或修改其他交易所。网页仍为首屏 15 条，不能保证长时间中断后补齐全部历史；英文入口实测可能重定向中文。

## 验证
- 后端 test_okx_announcement_sources.py、test_announcements.py、test_announcement_public_sources.py 共 80 passed（25.82 秒）。覆盖实际 SSR 数据结构、无日期/非法 slug、各 API 故障、全 API 故障后网页恢复、全源异常、跨语言去重与 API 时间优先。
- 新测试 Ruff 与 git diff --check 通过；本次无前端修改。
- 修改代码仅加载到生产临时进程只读探测：中文/英文网页各解析 15 条，全部来源合并 48 条且 ID 唯一；未写库或发送通知。

## 工作区与交付
保留已有未跟踪 .worktrees、output、pytest 目录、其他交接文档和脚本，仅提交本任务三个文件。部署版本、备份校验与线上验收在部署后补充。

## 后续
观察下一条自然新公告的采集与发送；sent 仅代表应用发送器成功，不等于用户终端独立收件证明。后续模块在新任务中继续。
