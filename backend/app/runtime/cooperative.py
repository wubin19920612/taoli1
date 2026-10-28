"""Time-budgeted checkpoints for CPU work owned by the event loop."""

import asyncio
import time


class YieldBudget:
    def __init__(self, budget_ms: float = 20) -> None:
        self.budget_seconds = budget_ms / 1000
        self.last_yield = time.perf_counter()

    async def maybe_yield(self) -> None:
        if time.perf_counter() - self.last_yield >= self.budget_seconds:
            await asyncio.sleep(0)
            # Time spent waiting for other tasks is not part of our CPU budget.
            self.last_yield = time.perf_counter()
