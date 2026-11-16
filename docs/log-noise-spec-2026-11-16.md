# 日志降噪:预期的单源失败不再打完整 Traceback(只改后端日志,行为不变)

范围文件:backend/app/services/announcements.py、announcement_polling.py、trade_availability.py(仅限下列位置)。

## 改动
1. announcements.py 约 2101 行 Gate `fetch` 的分类抓取失败(日志 "failed to fetch gate announcement category: <cat>"):改单行 warning,带异常类型名(type(exc).__name__)和分类,不带 exc_info;降级行为(跳过该分类)不变。2109/2185 附近同类 Gate 日志一并检查。
2. announcements.py 约 1236-1241 `_get_additional_json`:去掉 exc_info=True,改单行 warning + 异常类型,仍返回 {}。
3. announcements.py 约 2479/2492 `logger.exception("announcement provider failed")`:改 logger.warning + provider 名 + 异常类型,仍返回 []。
4. announcement_polling.py `log_failure`(64-74):首次失败也不带完整堆栈,只打单行 warning(provider、异常类型、consecutive_failures);异常类型变化时打一行即可;保留现有状态记录(status=error、history)不变。
5. trade_availability.py 约 1626 `check_once` 失败日志:RuntimeError("聚合行情中未找到指定原始市场")这类行情未就绪的预期错误降为单行 warning(不带堆栈);其他未知异常保持原样带堆栈。

## 约束
- 只改日志级别/格式,不改控制流、返回值、重试、状态记录。
- 不改 frontend、不改告警逻辑、不改 .github。
- 为改动的每处补/改测试(caplog 断言:无 exc_info/Traceback、含异常类型名、返回值不变);已有相关测试不得放宽。

## 验收
- cd backend && python -m pytest tests/test_announcements.py tests/test_announcement_polling*.py tests/test_trade_availability*.py -q 2>&1 | tail -5(文件名以实际为准,用 glob 找相关测试)。
- 汇报改动文件列表 + 测试汇总行。不要提交。不要跑 WSL。
