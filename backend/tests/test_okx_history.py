import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from itertools import pairwise

import httpx
import pytest

from app.services import okx_history
from app.services.pair_spread_query import PairSpreadQueryService, PartialKlineHistoryError


class Clock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    async def sleep(self, delay):
        self.value += delay
        await asyncio.sleep(0)


@pytest.fixture
def paced(monkeypatch):
    clock = Clock()
    limiter = okx_history.OkxHistoryLimiter(clock=clock.now, sleep=clock.sleep)
    monkeypatch.setattr(okx_history, "get_okx_history_limiter", lambda: limiter)
    return clock, limiter


@pytest.mark.asyncio
async def test_all_service_instances_share_history_request_budget(paced):
    clock, _ = paced
    starts = []

    def handler(request):
        starts.append(clock.now())
        return httpx.Response(200, json={"code": "0", "data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        services = [PairSpreadQueryService(client), PairSpreadQueryService(client)]
        await asyncio.gather(*(
            services[i % 2]._get_json(
                f"https://www.okx.com/api/v5/market/history-candles?instId={i}&limit=100"
            ) for i in range(12)
        ))
    assert len(starts) == 12
    assert all(b - a >= 0.25 - 1e-9 for a, b in pairwise(starts))


@pytest.mark.asyncio
async def test_limiter_is_shared_within_loop_and_cancellation_does_not_reserve_slots():
    assert okx_history.get_okx_history_limiter() is okx_history.get_okx_history_limiter()
    limiter = okx_history.OkxHistoryLimiter(interval_seconds=0.01)
    await limiter.acquire()
    waiting = asyncio.create_task(limiter.acquire())
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await asyncio.wait_for(limiter.acquire(), timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(429, "50011"), (200, "50011")])
async def test_rate_limit_retries_respect_retry_after_and_business_code(paced, status, code):
    clock, _ = paced
    starts = []

    def handler(request):
        starts.append(clock.now())
        if len(starts) == 1:
            return httpx.Response(status, headers={"Retry-After": "5"},
                                  json={"code": code, "msg": "Rate limit reached", "data": []})
        return httpx.Response(200, json={"code": "0", "data": [["ok"]]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await PairSpreadQueryService(client)._get_json(
            "https://www.okx.com/api/v5/market/history-candles"
        )
    assert result["data"] == [["ok"]]
    assert starts[1] - starts[0] >= 5


@pytest.mark.asyncio
async def test_exhausted_retries_leave_shared_cooldown_for_other_clients(paced):
    clock, limiter = paced
    starts = []

    def handler(request):
        starts.append(clock.now())
        return httpx.Response(429, text="Too Many Requests")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="限流"):
            await PairSpreadQueryService(client)._get_json(
                "https://www.okx.com/api/v5/market/history-candles"
            )
    assert len(starts) == 3
    assert starts[1] - starts[0] >= 2
    assert starts[2] - starts[1] >= 4
    await limiter.acquire()
    assert clock.now() - starts[-1] >= 8


def test_retry_after_accepts_http_date_and_rejects_nonfinite_values():
    now = datetime.now(UTC)
    assert 7 <= okx_history.retry_after_seconds(format_datetime(now + timedelta(seconds=8))) <= 8
    for value in (None, "invalid", "NaN", "inf", "-2"):
        assert okx_history.retry_after_seconds(value) == 0


@pytest.mark.asyncio
async def test_business_errors_are_not_silently_treated_as_empty_history(paced):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"code": "51001", "msg": "Instrument missing"})
    )) as client:
        with pytest.raises(RuntimeError, match="51001"):
            await PairSpreadQueryService(client)._get_json(
                "https://www.okx.com/api/v5/market/history-candles"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["backward", "forward"])
async def test_partial_pages_carry_prices_and_exact_covered_range(direction):
    start = datetime(2026, 10, 19, 0, tzinfo=UTC)
    end = start + timedelta(hours=24) - timedelta(milliseconds=1)
    calls = 0
    service = PairSpreadQueryService()

    async def fetch(url):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("OKX 历史行情接口限流")
        return {"data": [
            [str(int((start + timedelta(minutes=minute)).timestamp() * 1000)),
             "100", "100", "100", "100", "1", "1", "100", "1"]
            for minute in (range(1340, 1440) if direction == "backward" else range(100))
        ]}

    service._get_json = fetch
    try:
        with pytest.raises(PartialKlineHistoryError) as caught:
            await getattr(service, f"_fetch_okx_klines_{direction}")("CT-USDT-SWAP", start, end, 1)
        assert len(caught.value.points) == 100
        assert caught.value.covered_start == (start + timedelta(minutes=1340)
                                             if direction == "backward" else start)
        assert caught.value.covered_end == (end + timedelta(milliseconds=1)
                                           if direction == "backward" else start + timedelta(minutes=100))
    finally:
        await service.aclose()
