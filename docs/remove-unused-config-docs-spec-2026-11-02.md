# 删除不用功能：C 包（部署配置、说明与交接）任务书（2026-11-02）

依据：`docs/remove-unused-inventory-2026-11-02.md` 的“C. 部署配置、当前说明与交接”一节，以及“文档引用盘点与处理”一节。本分支 `codex/ui-refresh` 已经合入前端改版、前端删除（A 包）和后端删除（B 包）。

## 允许修改的文件
- `.env.example`、`docker-compose.yml`
- `README.md`、`docs/next-task.md`、`docs/linux-deployment.md`
- 新建 `docs/task-handoff-2026-11-02-ui-refresh-and-retirement.md`

不改 `frontend/**`、`backend/**`、`deploy/**`、`.github/**`，也不改 prod/local 两个 compose override。不要 git push。

## 要做的事
1. `.env.example`：删除资金研究、挤仓、原油新闻专属的变量，以及 `FUNDING_POLL_INTERVAL_SECONDS`。先 grep `backend/app` 确认这些变量已没有读取方，有读取方的保留并在报告里说明。
2. `docker-compose.yml`：删除显式的 `FUNDING_POLL_INTERVAL_SECONDS`，其余不动。
3. `README.md`：删除资金研究模块说明和相关运行命令，其余保留。
4. `docs/linux-deployment.md`：把挤仓 worker 的描述改成“已退役”，旧库的备份、恢复说明保留。
5. `docs/next-task.md`：在最前面更新阶段状态，说明本轮改版和退役内容，链接交接文档。
6. 新建交接文档，内容包括：
   - 本轮前端改版涉及的页面和要点；
   - 退役功能清单；
   - 保留的共享项，重点写 symbol-query 例外、公式分类迁移、`fs_identity`；
   - 旧表和旧 `app_settings` 行全部保留、未删除；
   - 退役开关的兼容做法：旧 `DISABLED_FEATURES` 里的 8 个退役 key 会被忽略，`gate_twap` 仍然有效；
   - 已知遗留问题：同一个 app 对象在同一进程内第二次进入 lifespan 时，Astro、飞书、Gate 客户端已被关闭。这是原有问题，生产按进程重启所以不受影响，待以后修；
   - 上线后的检查项：退役端点返回 404，symbol-query 返回 200，设置页能正常加载，标的查询走势能出图。

## 验收
1. `git diff --check` 没有输出。
2. 如果本机有 docker，运行 `docker compose --env-file .env.example -f docker-compose.yml config --quiet` 能通过；没有 docker 就在报告里说明。
3. 在当前分支提交，提交信息用 `docs: retire unused features config and handoff`。
