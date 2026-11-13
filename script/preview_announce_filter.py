"""Regenerate the title-only preview: python script/preview_announce_filter.py."""

import json
import sys
from itertools import zip_longest
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "backend"))
    from app.services.announcement_relevance import is_arbitrage_relevant_announcement

    source = root / "backend/tests/fixtures/announce-titles-7d.json"
    rows = json.loads(source.read_text(encoding="utf-8"))
    kept: list[str] = []
    excluded: list[str] = []
    for index, (exchange, kind, title) in enumerate(rows, 1):
        cell = f"#{index} [{exchange}/{kind}] {title}"
        cell = cell.replace("|", "&#124;").replace("\n", " ")
        target = kept if is_arbitrage_relevant_announcement(title) else excluded
        target.append(cell)

    lines = [
        "# 其他公告过滤预览（2026-11-13）",
        "",
        "数据：[近 7 天标题样本](../backend/tests/fixtures/announce-titles-7d.json)。",
        f"共 {len(rows)} 条，保留 **{len(kept)}** 条，排除 **{len(excluded)}** 条。",
        "按原始行号标记，保留重复标题；未提供中文标题或分类，使用纯函数的标题规则。",
        "排除词优先；未命中保留规则的标题也归入排除。此表表示相关性，不包含交易所白名单、开关、历史窗口等最终推送条件。",
        "",
        "复现：在仓库根目录执行 `python script/preview_announce_filter.py`。",
        "",
        f"| 保留（{len(kept)}） | 排除（{len(excluded)}） |",
        "| --- | --- |",
    ]
    lines.extend(f"| {keep} | {exclude} |" for keep, exclude in zip_longest(kept, excluded, fillvalue=""))
    destination = root / "docs/announce-filter-preview.md"
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"total={len(rows)} retained={len(kept)} excluded={len(excluded)}")


if __name__ == "__main__":
    main()
