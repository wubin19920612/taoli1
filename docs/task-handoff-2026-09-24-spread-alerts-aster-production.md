# 价差告警：Aster 生产部署中断与主机排查交接

日期：2026-09-24。模块：价差与资金费率告警。

## 目标、范围与代码基线

- 目标：确认中断的 Docker 构建和生产服务状态，恢复服务后验证 Gate 现货 / Aster 永续 TAKEUSDT 数据采集、告警事件及实际通知状态。
- 分支：`codex/frontend-localization-polish`。本次修复提交为 `e4fc83f`，其基线为 `9385e21`。本地分支目前指向 `2aa4477`，其中 `643cb25`、`2aa4477` 属于其他模块；不得为了本次告警排查顺带部署它们。
- 功能实现、业务规则和本地测试见 `docs/task-handoff-2026-09-23-spread-alerts-aster.md`。关键入口是 `backend/app/exchanges/aster.py`、`backend/app/services/alert_engine.py`、`backend/app/api/routes_alerts.py` 和 `backend/app/api/routes_opportunities.py`。

## 已知生产操作与备份

以下操作来自本次生产排查交接，因 SSH 当前不可用，尚未在服务器上再次核实：

- 操作前仓库为 `9385e21`，前后端容器均显示 healthy，磁盘剩余约 9.6 GB。
- 已通过 SQLite backup API 备份 `/data/radar.db` 至 `backups/radar-before-aster-alert-20260924T000714Z.db`。文件大小 613,199,872 字节，`integrity_check=ok`，容器内外 SHA-256 均为 `f951cf3f87d3ebcaf2a94b00a31cabb38219b76a7604e330c174b3cfd0bb85a3`。保留此备份；恢复连接后再核对文件与校验和。
- 服务器仓库使用 `git pull --ff-only` 仅快进至 `e4fc83f`。之后运行 `sudo docker compose build --pull`：后端镜像构建完成；前端 `npm ci` 完成，`vite build` 在 `transforming...` 后长时间无响应。已中止本地卡住的 SSH 连接，远端构建进程是否仍在运行未知。
- 未运行 `docker compose up`，未运行 `docker compose down -v`。服务器上的 `.env` 备份和 `CACHED` 必须保留。

## 2026-09-24 本次只读复查

- 本机保存有该服务器的 ED25519、RSA、ECDSA 主机公钥记录；两次使用 `StrictHostKeyChecking=yes` 的 SSH 连接均在 banner 交换阶段超时，未取得远端命令输出。
- 对外 `:3000/api/health` 在 12 秒内没有返回 HTTP；对外 `:8000/api/health` 建连超时。`BACKEND_PORT` 可能仅绑定回环地址，因此后者不能单独证明后端失效。
- 本机未发现可用的云 CLI 或对应环境变量；当前无法通过云控制台或 SSH 查看主机负载、OOM 记录、Docker 进程、容器状态、远端 Git 提交及备份现状。
- 因此不能把镜像构建进度、代码已快进或本地测试视为生产恢复，更不能宣称告警通知已恢复。

## 恢复连接后的顺序

1. 通过云控制台或严格主机密钥校验的 SSH 检查主机负载、可用内存、磁盘、内核 OOM 记录及 Docker 服务；定位 `docker compose build`、BuildKit、`npm`、`vite` 等进程是否仍在运行。先确认原因和进程归属，再决定是否终止卡住的构建进程或重启主机。
2. 在项目目录核对 `git rev-parse --short HEAD`、工作区状态、`sudo docker compose ps`、容器日志和 `sudo docker system df`。核对数据库备份的大小、SHA-256 与 `integrity_check`。不要清理备份、`.env` 备份、`CACHED`、未确认归属的文件或 Docker 数据卷。
3. 仅在资源与现有容器状态确认后决定是否继续构建和执行 `sudo docker compose up -d`。保持生产代码停在 `e4fc83f`；避免无意拉入其他模块提交。不得使用 `docker compose down -v`。
4. 验证前后端容器健康、服务器本机 `/api/health` 和前端代理 `/api/health`；同时检查返回的 `exchange_errors`、`exchange_states`，不要只看 `status: ok`。
5. 只读核对 `/api/markets?symbol=TAKEUSDT`、`/api/opportunities?symbol=TAKEUSDT&include_risky=true`、`/api/alerts/rules` 和 `/api/alerts/events`。确认 Gate 现货与 Aster 永续的原始市场身份、报价、双方成交额、Aster 资金费率及周期、数据时间、实际盘口价差和风险标签；结合线上规则的连续命中与冷却，记录事件 `sent`、`muted` 或 `failed` 的真实原因。`sent` 事件仍需结合飞书投递状态确认通知，不制造虚构交易信号。

云控制台可登录时，优先执行以下只读检查；内核日志时间按服务器时区调整：

```bash
cd ~/wubin/taoli1
uptime
free -h
df -h / /var/lib/docker
sudo journalctl -k --since '2026-09-24 08:00:00' --no-pager | grep -Ei 'oom|out of memory|killed process'
ps -eo pid,ppid,stat,%cpu,%mem,rss,etime,comm --sort=-rss | head -n 20
pgrep -fl 'docker|buildkit|npm|vite'
sudo docker compose ps
sudo docker system df
git rev-parse --short HEAD
sha256sum backups/radar-before-aster-alert-20260924T000714Z.db
```

## 当前交付状态与遗留事项

- 代码修复 `e4fc83f` 已在远端分支；据交接，服务器工作树已快进到该提交，但没有完成容器重建启动与线上验收。生产部署状态未知。
- 本次仅新增排查交接文档，没有改动业务代码或生产配置。此前 Aster 采集、告警引擎相关本地测试结果见 9 月 23 日交接；当前因无法连接生产主机，线上行为未验证。
- 本地工作区已有大量其他任务的 `output/` 未跟踪产物和 `script/dexe_bybit_bitget_chain.py`，均应保留，不纳入本模块交付。
