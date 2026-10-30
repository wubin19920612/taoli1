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
保留已有未跟踪 .worktrees、output、pytest 目录、其他交接文档和脚本，仅提交本任务三个文件。功能提交 cb2dc58c6507a231850fd39c35c98ce9e621df28 已推送并部署。CI https://github.com/wubin19920612/taoli1/actions/runs/36559690516 前后端镜像均成功；使用 linux-update.sh 备份后 git pull --ff-only，拉取预构建镜像并启动，未在服务器构建。

### 备份与验收
- backups/radar-before-cb2dc58c6507-20260929T110855Z.db，626737152 字节，integrity_check=ok，宿主与容器 SHA256 一致：e2c2794911325544aa75de81d08a85090ce5bbb68a224126ada4e91f7df531d2。
- backups/squeeze-route-before-cb2dc58c6507-20260929T110855Z.db，217088 字节，integrity_check=ok，SHA256：1859169666f43f4a9308775d8f8424c691937ed1105e2145b757f726a21e67c9。
- 前后端均运行 cb2dc58 对应镜像且 healthy；/api/health 与 OKX 公告接口均 200。
- 部署后的正式镜像再次只读实测：网页各 15 条，合并 48 条且 ID 唯一。
- 线上库部署前后均 216 条 OKX 公告，117 sent、99 muted；重复 announcement_id 为 0，未见重复入库或历史误通知。
- 本次没有新自然公告可供端到端通知验收；未发送人工测试消息。文档后续提交不改变线上功能版本。

## 后续
观察下一条自然新公告的采集与发送；sent 仅代表应用发送器成功，不等于用户终端独立收件证明。后续模块在新任务中继续。
