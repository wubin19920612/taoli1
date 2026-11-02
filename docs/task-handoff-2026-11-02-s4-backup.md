# S4 备份流程正式化交接

- 目标/范围：部署脚本隔离备份双库、可回退旧函数、备份目录容量门槛与非自动清理文件盘点、临时恢复演练、运维文档及每日 timer 方案。
- 分支：`codex/s4-backup-formalize`；基线：`0935bd7e5ceaf8b886a4e8774a7ef0ff90202454`。
- 依据：调查笔记 §1、§2、§3 S4；架构审查 P1-8/S4 位于主仓库 `docs/architecture-review-2026-10-23-claude.md`（本工作树没有该文件，只读参考）。
- 入口：`deploy/linux-update.sh` 默认使用 backend 镜像 ID 启动独立 128 MiB 备份容器；`BACKUP_MODE=legacy` 显式回退旧函数，不自动回退。
- 双库各自校验 integrity_check 与宿主机 SHA-256；route 库不存在时输出 skip；不是跨库原子快照。
- 源库以 SQLite `mode=ro` 打开，卷可写仅为 WAL/SHM 锁元数据；不对在线库使用 immutable。备份失败的 `.partial` 留待人工调查。
- `deploy/backup_tools.py`：`check-dir` 递归统计逻辑大小，复用现有自动命名正则，列出未管理文件；阈值默认 15 GiB，部署备份前后都检查，过限不删除文件。
- `bash deploy/linux-update.sh --check-backups DIRECTORY`：本地只读检查入口，不调用 Git 远端或 Docker，不读取 `.env`。
- `deploy/restore_drill.sh`：仅复制输入快照到临时目录，再恢复到临时库并核对 integrity_check/关键表计数；成功和失败均清理临时文件。返回 0/1/2 表示成功/校验失败/用法错误。
- 恢复默认表及完整命令见 `docs/linux-deployment.md`；旧命名用 `--kind`，自定义表用重复的 `--table`。行数只与快照对比，不是线上计数或业务语义验收。
- `docs/s4-daily-backup-proposal.md` 仅评估 timer；尚未提供只备份入口、共享锁或启用任何任务。
- 禁止修改范围保持：`backend/`、`frontend/`、任何 `.env`、保留器规则均未修改。
- 验证命令：`python -m pytest deploy/test_backup_retention.py deploy/test_backup_tools.py -q` → `20 passed, 1 skipped`；单独运行原保留器测试 → `4 passed`；新增测试 → `16 passed, 1 skipped`（Windows 不允许创建符号链接，跳过该项）。
- 脚本语法：对本任务两份脚本及根目录参考脚本逐一 `bash -n` → `BASH_SYNTAX PASS scripts=3`；ShellCheck 不在 PATH，未运行。定时方案 `20` 行（≤40）。
- 本地实跑临时合成双库：`bash deploy/linux-update.sh --check-backups TEMP` → `BACKUP_DIR_CHECK PASS`；分别 `bash deploy/restore_drill.sh TEMP/<backup>.db` → 两次 `RESTORE_DRILL PASS integrity_check=ok tables=3`；`BACKUP_DIR_MAX_GB=0.000001` 再检查 → 退出 1，仍输出未管理文件清单，未删除文件。`LOCAL_ACCEPTANCE PASS checks=4`。
- 差异检查：`git diff --check` 通过；旧 `backup_sqlite` 函数与基线文本一致；受保护路径无改动。全部运行证据来自本地合成数据或 shell mock，没有连接生产。
- 线上状态：遵照任务禁止，未执行 SSH、Docker 生产操作、本机部署；未取得新线上备份或部署版本。
- 测试边界：本地用实际 SQLite 测试 WAL 活跃/关闭、恢复与容量逻辑；Docker 参数与旧路径用 shell mock 验证，不等于 cgroup 或测试机 Compose 验收。
- 初始未跟踪文件：`sqlitefix-isolated-deploy-20261028.sh`，仅参考，保持原样且不提交。其他测试产物不提交。
- 下一步：用户在测试机独立 Compose 项目演练双库备份，记录业务容器内存及备份容器限额、两份 SHA 与恢复 PASS，再决定上线。后续 S6/定时备份另开任务。

## 审查修复

- 范围：修复只读报告末尾的 P2-1、P2-2、P2-3；以本地 `36ef0b1` 为修复基线，在当前 `codex/s4-backup-formalize` 分支新增 `fix: 处理 S4 审查问题` 提交，不 amend。
- P2-1：通过 `docker inspect` 精确提取 backend `/data` 的 volume 名或 bind source，以 `--mount` 只挂 `/data` 和 `backups/`；拒绝缺失、多重或不支持的挂载信息。Shell mock 逐参数断言恰好两个挂载，没有 `--volumes-from` 或 Docker socket。
- P2-2：隔离容器只写 `*.db.partial`；容器 integrity 与宿主机 SHA-256 全部通过后，同目录原子 rename 为正式 `.db`。失败保留为 `*.db.failed` 并中止部署；`.failed` 不匹配自动保留器正则。新增 mock 覆盖 SHA 不符、哈希命令失败、容器 integrity 失败和发布碰撞，并核对已有文件不被覆盖、失败后不继续部署。异常中断仍可能留下 `.partial`，由人工调查。
- P2-3：不再调用 `gettempdir()`；按 `TMPDIR` / `TEMP` / `TMP` 或固定目录选取临时父目录，先检查解析后的路径，拒绝 `/data`、`/var/lib/docker`、输入备份目录及仓库 `backups/` 内部路径，再显式传给 `TemporaryDirectory(dir=...)`。新增受保护 TMPDIR 测试，在创建临时目录、读取输入内容、哈希或 SQLite 连接前拒绝；安全目录测试断言不调用探测函数并清理临时文件。
- 验证：`python -m pytest deploy/test_backup_retention.py deploy/test_backup_tools.py -q` → **`23 passed, 1 skipped in 11.94s`**。跳过项为 Windows 符号链接权限限制。全部 3 份现有 `.sh`（含仅检查的根目录参考脚本）执行 `bash -n` → `BASH_SYNTAX PASS scripts=3`；`git diff --check` 通过，legacy 函数与修复基线一致。
- 文档：同步更新 `docs/linux-deployment.md` 的挂载、验证发布顺序、失败文件和临时目录说明。
- 编码：本次 5 份修改文件均通过严格 UTF-8 解码、无 BOM；Shell 保持 LF，Python/Markdown 保持当前 `core.autocrlf=true` 的 CRLF 检出格式，Git 存储为 LF。提交说明保留中文。根目录未跟踪参考脚本保持原样，不提交。
- 交付边界：只进行本地 SQLite 与 shell mock 验证，未执行 SSH、Docker 或生产部署，无新增线上备份和线上验证证据。开始时本地分支相对已缓存远端为 ahead 4 / behind 1；保留该历史，不合并、重写或强推。
