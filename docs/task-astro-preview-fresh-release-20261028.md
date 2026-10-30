# 发布任务书：astro-preview-fresh 上线（2026-10-28，用户已授权推 GitHub + 部署）

## 前提
- 修复提交：`06fba30`（分支 codex/astro-preview-fresh，基于 74c8f23）。
- 线上当前：`4ad88b7`（= origin/codex/frontend-localization-polish，sqlite busy snapshot 修复）。
- 服务器：`ssh taoli1-prod`，目录 `/home/ubuntu/wubin/taoli1`。部署方式参考 `output/m23b-deploy-20261027.sh` 与 `output/m23a-isolated-deploy-20261027.sh`（拉 CI 构建的 ghcr `sha-<commit>` 镜像，不在服务器构建）。
- 不要读、改、打印生产 `.env`；不用 dashboard 密码；不调用真实建卡（不 POST /api/astro/instrument/card），不改已有外部卡片。

## 步骤
0. 先补测试（只改 backend/tests/test_astro_preview_fresh.py）：shadow 模式下 HL 腿参考价、fresh 复核得到 book、身份未核验 → 预览 can_submit=True 且 warnings/blockers 无 REFERENCE_QUOTE_ONLY/LEG_TIME_SKEW；enforce 模式同条件 → can_submit=False 且身份/单位 blocker 各只出现一次；任一模式下 identity_relation=different/conflict → 拦截。跑通后 amend 进 06fba30 之上的新 commit `test(astro): cover shadow/enforce manual preview`。
1. 在本工作树把 06fba30 rebase 到 `4ad88b7`（只应有 sqlite 修复 db/database.py 的差异，若冲突停止并报告）。rebase 后跑：
   `cd backend && python -m pytest tests -q -k "astro or validation or instrument or database" --basetemp=.pytest_release_a -p no:cacheprovider`
2. 推送：`git push origin HEAD:codex/astro-preview-fresh`（备份分支）和 `git push origin HEAD:codex/frontend-localization-polish`（必须 fast-forward，禁止 force）。
3. 等 GitHub Actions `build-production-images.yml` 为新 commit 成功（`gh run list/watch`；若 gh 无权限，轮询 `docker manifest inspect` 或 ghcr 标签存在，间隔 ≥60 秒，最多 30 分钟）。CI 失败则停止并报告失败步骤。
4. 部署：把 m23b 脚本改写为 `output/astro-fresh-deploy-20261028.sh`（C=新 40 位 SHA，TAG=astro-fresh-20261028，去掉 2h/4h 探针收集循环，保留 baseline 探针、隔离部署脚本、重试与空备份清理、健康检查），scp 到服务器 `output/`，用 `nohup bash ... > output/astro-fresh-20261028-run.log 2>&1 &` 运行，轮询日志直到 `DEPLOY_EXIT=`。不要在 ssh 命令里 pkill 与命令本身同串。
5. 线上验证（服务器上 curl 127.0.0.1:8000，只读）：
   - `/api/health/live`、`/api/health/ready` 为 200；`docker inspect` 镜像 tag 为新 SHA；RestartCount 不增长。
   - `POST /api/astro/instrument/preview`（预览只读，不写 Astro），JIOUSDT：buy bitget future、sell hyperliquid future（raw_symbol/dex 从 `/api/instruments/lookup?symbol=JIO` 或前端同款接口取）。期望（线上 mode=shadow）：不再出现"仅有参考价格""双方行情时间差过大"，can_submit=true（身份未核验在 shadow 下只观察不拦截）；若 can_submit=false，原文记录 blockers。再用一对已核验市场（如 BTC binance future vs hyperliquid BTC main）预览，期望 can_submit 正常。
   - 部署后 10 分钟 `docker logs --since 10m` 中 Traceback 数、`database is locked` 数。
6. 把结果追加到 `docs/task-handoff-2026-10-28-astro-preview-fresh.md`（发布章节），commit `docs: astro preview fresh release record [skip ci]` 并推到 codex/astro-preview-fresh 和 codex/frontend-localization-polish（[skip ci] 不触发构建）。

## 输出
新 SHA、CI run 结果、DEPLOY_EXIT 行、健康检查原始输出、两次预览的 blockers/warnings 原文、日志计数。失败任一步立即停止，不回滚以外的修复尝试；需要回滚时报告，由 Claude 决定。
