# Git 交付与自动推送说明

本项目完成代码任务后，默认需要把本次改动提交并推送到当前 GitHub 分支，除非用户明确要求只保留本地修改。

## 给其他 Codex 对话的固定指令

可以把下面这段直接发给负责开发的对话：

> 完成任务后请自行做完验证、Git 提交和 GitHub 推送，不要只修改本地文件。先检查工作区，保留并避开不属于本次任务的改动；只暂存本次任务涉及的文件。Git 不要使用 sudo，不要 force push。提交后推送当前分支，并用 `git status --short --branch`、`git log -1 --oneline` 确认本地与远端同步。如果推送失败，必须明确报告原因和尚未推送的提交，不要声称已经完成。

## 标准流程

### 1. 开始前检查

```bash
git status --short --branch
git branch --show-current
```

确认当前分支正确，并记录已有的未提交文件。已有修改默认属于用户或其他任务，不要执行 `git reset --hard`、`git checkout -- <file>` 或其他回退操作。

### 2. 完成代码并验证

根据改动范围运行相关测试。常用命令：

```bash
cd backend
pytest
```

```bash
cd frontend
npm test -- 相关测试文件
npm run build
```

回到项目根目录检查差异：

```bash
git diff --check
git status --short
```

### 3. 只暂存本次任务文件

不要直接暂存工作区里的所有文件。明确列出本次任务修改的路径：

```bash
git add path/to/file1 path/to/file2
```

提交前复核：

```bash
git diff --cached --stat
git diff --cached
```

如果暂存区包含其他任务的文件，先停止提交并调整暂存范围，不要删除或还原那些文件。

### 4. 提交并推送当前分支

```bash
git commit -m "简洁、准确的提交说明"
git push origin HEAD
```

禁止使用：

```bash
sudo git ...
git push --force
git reset --hard
```

### 5. 确认交付结果

```bash
git status --short --branch
git log -1 --oneline
```

必须确认以下结果：

- `git commit` 成功并产生提交号。
- `git push` 明确显示推送成功。
- 当前分支与 `origin` 同步。
- 工作区如果仍有文件，说明它们是否属于其他任务。

不能因为本地提交成功就说“已推送”。网络、权限或认证导致推送失败时，应保留本地提交并把失败信息告知用户。

## 当前项目约定

- 本地项目：`C:\Users\wubin\Desktop\code\codex\taoli1`
- GitHub：`https://github.com/wubin19920612/taoli1.git`
- 常用开发分支：`codex/frontend-localization-polish`
- Git 命令不要使用 `sudo`。
- 服务器运行 Docker 命令需要使用 `sudo`。
- 不要提交 `.env`、Webhook、密钥、API Key 或其他敏感信息。

## 服务器部署

代码推送成功后，在服务器执行：

```bash
cd ~/wubin/taoli1
git pull --ff-only
git log -1 --oneline
sudo docker compose up -d --build
sudo docker compose ps
```

服务器上的 `git pull` 不使用 `sudo`；只有 Docker Compose 使用 `sudo`。
