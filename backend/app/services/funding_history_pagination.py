"""Bounded pagination shared by pair-spread funding history adapters."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal

from app.models.pair_spread import PairSpreadFundingPoint


class PartialFundingHistoryError(RuntimeError):
    def __init__(self, message: str, points: list[PairSpreadFundingPoint]):
        super().__init__(message)
        self.points = points


async def collect_funding_pages(
    fetch_page: Callable[[int, int], Awaitable[list[dict[str, Any]]]],
    timestamp: Callable[[dict[str, Any]], datetime | None],
    parse: Callable[[dict[str, Any]], PairSpreadFundingPoint | None],
    *,
    start: datetime,
    end: datetime,
    limit: int,
    direction: Literal["forward", "backward"] = "backward",
    page_numbered: bool = False,
) -> list[PairSpreadFundingPoint]:
    """Cursor is inclusive milliseconds; page numbering starts at one.

    Raw timestamps drive pagination even if a rate is absent. Invalid rates do
    not become zero settlements. Preserve successfully parsed pages on failure.
    """
    cursor = int((start if direction == "forward" else end).timestamp() * 1000)
    points: dict[datetime, PairSpreadFundingPoint] = {}
    invalid = 0
    try:
        for page in range(1, 101):
            rows = await asyncio.wait_for(fetch_page(cursor, page), timeout=20)
            if not isinstance(rows, list):
                raise TypeError("invalid funding history response")
            times = []
            for row in rows:
                if not isinstance(row, dict):
                    invalid += 1
                    continue
                at = timestamp(row)
                if at is not None:
                    times.append(int(at.timestamp() * 1000))
                point = parse(row)
                if point is None:
                    invalid += 1
                elif start <= point.funding_time <= end:
                    points[point.funding_time] = point
            if len(rows) < limit:
                break
            if not times:
                raise RuntimeError("funding pagination has no valid timestamps")
            next_cursor = max(times) + 1 if direction == "forward" else min(times) - 1
            if not (page_numbered and page == 1) and (
                (direction == "forward" and next_cursor <= cursor)
                or (direction == "backward" and next_cursor >= cursor)
            ):
                raise RuntimeError("funding pagination did not advance")
            cursor = next_cursor
            if (direction == "backward" and cursor < int(start.timestamp() * 1000)) or (
                direction == "forward" and cursor > int(end.timestamp() * 1000)
            ):
                break
        else:
            raise RuntimeError("funding pagination exceeded 100 pages")
        if invalid:
            raise RuntimeError(f"funding history omitted {invalid} invalid/unsettled records")
    except Exception as exc:
        raise PartialFundingHistoryError(
            str(exc) or type(exc).__name__, [points[t] for t in sorted(points)]
        ) from exc
    return [points[t] for t in sorted(points)]
