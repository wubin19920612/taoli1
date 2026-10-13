# 交易所公告中文源交接

## 目标与范围

公告来源与展示模块：为交易所公告接入官方中文标题和中文原文链接，页面和飞书提醒优先使用中文；没有中文版本时保留原英文。公告身份、原始标题、原始链接、币种和事件时间解析不改变。

## 分支与基线

- 分支：`codex/frontend-localization-polish`
- 基线：`a453ec835bb2847b8ea88f0c4e66d5f35fa6e7c7`
- 本任务变更尚未提交、推送或部署（交付前需按仓库清单执行）。

## 已完成功能

- `ExchangeAnnouncement` 增加 `title_zh` / `url_zh`，SQLite 自动迁移并保存。
- 新增 `AnnouncementLocalizationService`：按交易所公告 ID/slug 精确匹配中文源，不按 ticker 模糊匹配。
- Binance 使用 `lang: zh-CN`，OKX 使用中文 API/帮助中心，Bitget 使用 `language=zh_CN`，Bybit 使用官方支持的 `locale=zh-TW`；Gate 保留现有中文阅读器回退。
- 公告页面显示“官方中文”标签、链接中文原文，展开详情保留英文原始标题；飞书新公告和到点提醒同样使用中文标题/链接。

## 关键入口与业务规则

- `backend/app/services/announcement_localization.py`
- `backend/app/services/announcements.py` 的 `HttpAnnouncementProvider.localize` 与 `MultiAnnouncementProvider`
- `backend/app/models/announcement.py`
- `backend/app/db/schema.py`、`backend/app/db/repositories.py`
- `frontend/src/pages/AnnouncementsPage.tsx`、`frontend/src/api/types.ts`
- Bybit 当前官方中文接口是繁体 `zh-TW`，`zh-CN` 会返回参数错误；不能把它标记为简体。
- 中文源失败或没有翻译时，保留原公告，不阻塞公告入库。

## 验证结果

- `backend/tests/test_announcement_localization.py`：通过。
- 公告相关后端测试：通过（80 tests）。
- `frontend/tests/AnnouncementsPage.test.tsx`：5 tests 通过。
- 前端全量：24 files / 231 tests 通过。
- `npm run build`：通过。
- 实时源探测：Binance、OKX、Bitget、Bybit 均返回中文标题和中文链接；Gate 中文 reader 页面可解析。

## 线上状态与下一步

- 线上状态：待提交、推送、备份数据库并部署。
- 部署时只暂存本任务文件，避开现有 `PairMonitorPage` 修改和大量 `output/`、`.worktrees/` 等其他任务产物。
- 按 `docs/git-delivery-checklist.md` 提交并推送；按 `docs/linux-deployment.md` 备份 `/data/radar.db`，使用预构建镜像更新并检查 `/api/health`、容器和公告接口。

## 已知问题

- Bybit 只有繁体中文官方源。
- Gate 官方页面可能被挑战页拦截，中文内容依赖现有 `r.jina.ai` 阅读器回退，可能延迟或暂时缺失。
- 中文回填只覆盖轮询抓到的近期公告；旧记录会在其 ID 再次出现在源列表时补齐。
