import asyncio
import hashlib
import hmac
import json
import math
import os
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, ROUND_DOWN
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from app.models.gate_twap import (
    GateTickerBook,
    GateTwapContractRules,
    GateTwapJobEvent,
    GateTwapJobStatus,
    GateTwapMarketSnapshot,
    GateTwapPlan,
    GateTwapPlanSlice,
    GateTwapRequest,
    GateTwapRunRequest,
)


GATE_API_BASE = "https://api.gateio.ws/api/v4"


class GateTwapError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_contract(raw: str) -> str:
    value = raw.strip().upper().replace("-", "_")
    if "_" not in value and value.endswith("USDT"):
        return f"{value[:-4]}_USDT"
    return value


def spot_pair_for_contract(contract: str) -> str:
    return normalize_contract(contract)


def decimal_from(value: Any, default: Decimal | None = None) -> Decimal | None:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def float_from(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def pct_diff(left: float | None, right: float | None) -> float | None:
    if left is None or right is None or right == 0:
        return None
    return ((left - right) / right) * 100


def signed_size(side: str, abs_size: Decimal) -> Decimal:
    return -abs_size if side == "sell" else abs_size


def decimal_plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


@dataclass(frozen=True)
class GateCredentials:
    api_key: str = ""
    api_secret: str = ""

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @classmethod
    def from_env(cls) -> "GateCredentials":
        return cls(
            api_key=os.getenv("GATE_API_KEY", "").strip(),
            api_secret=os.getenv("GATE_API_SECRET", "").strip(),
        )


class GateTwapClient:
    def __init__(
        self,
        *,
        credentials: GateCredentials | None = None,
        base_url: str = GATE_API_BASE,
        client: httpx.AsyncClient | None = None,
    ):
        self.credentials = credentials or GateCredentials.from_env()
        self.base_url = base_url.rstrip("/")
        self._own_client = client is None
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=True)

    @property
    def has_credentials(self) -> bool:
        return self.credentials.available

    async def aclose(self) -> None:
        if self._own_client:
            await self.client.aclose()

    @property
    def sign_prefix(self) -> str:
        return urlsplit(self.base_url).path.rstrip("/")

    def _auth_headers(self, method: str, path: str, query: str, body: bytes) -> dict[str, str]:
        if not self.credentials.available:
            raise GateTwapError("GATE_API_KEY/GATE_API_SECRET are not configured")
        timestamp = str(int(time.time()))
        body_hash = hashlib.sha512(body).hexdigest()
        sign_path = f"{self.sign_prefix}{path}"
        payload = "\n".join([method.upper(), sign_path, query, body_hash, timestamp])
        signature = hmac.new(
            self.credentials.api_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()
        return {
            "KEY": self.credentials.api_key,
            "Timestamp": timestamp,
            "SIGN": signature,
        }

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        auth: bool = False,
    ) -> Any:
        query = urlencode(params or {}, doseq=True)
        body_bytes = b""
        if body is not None:
            body_bytes = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "User-Agent": "taoli1-gate-twap/0.1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if auth:
            headers.update(self._auth_headers(method, path, query, body_bytes))
        url = f"{self.base_url}{path}"
        response = await self.client.request(
            method,
            url,
            params=params,
            content=body_bytes if body is not None else None,
            headers=headers,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GateTwapError(f"Gate API {method} {path} failed: {response.text}") from exc
        if not response.content:
            return None
        return response.json()

    async def get_contract(self, settle: str, contract: str) -> dict[str, Any]:
        return await self.request("GET", f"/futures/{settle}/contracts/{contract}")

    async def get_futures_ticker(self, settle: str, contract: str) -> dict[str, Any] | None:
        rows = await self.request(
            "GET",
            f"/futures/{settle}/tickers",
            params={"contract": contract},
        )
        return rows[0] if isinstance(rows, list) and rows else None

    async def get_spot_ticker(self, pair: str) -> dict[str, Any] | None:
        try:
            rows = await self.request("GET", "/spot/tickers", params={"currency_pair": pair})
        except GateTwapError:
            return None
        return rows[0] if isinstance(rows, list) and rows else None

    async def get_position(self, settle: str, contract: str) -> dict[str, Any]:
        return await self.request("GET", f"/futures/{settle}/positions/{contract}", auth=True)

    async def create_futures_order(self, settle: str, order: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", f"/futures/{settle}/orders", body=order, auth=True)


def rules_from_contract(payload: dict[str, Any] | None) -> GateTwapContractRules:
    payload = payload or {}
    enable_decimal = bool(payload.get("enable_decimal", False))
    min_size = float_from(payload.get("order_size_min")) or 1
    if enable_decimal:
        min_decimal = decimal_from(payload.get("order_size_min"), Decimal("1")) or Decimal("1")
        exponent = min_decimal.as_tuple().exponent
        step = float(Decimal(1).scaleb(exponent)) if exponent < 0 else 1
    else:
        step = 1
    return GateTwapContractRules(
        order_size_min=min_size,
        order_size_step=step,
        enable_decimal=enable_decimal,
        market_order_slip_ratio=float_from(payload.get("market_order_slip_ratio")),
        market_order_size_max=float_from(payload.get("market_order_size_max")),
        status=payload.get("status"),
    )


def floor_order_size(raw_abs_size: Decimal, rules: GateTwapContractRules) -> Decimal:
    if raw_abs_size <= 0:
        return Decimal("0")
    step = decimal_from(rules.order_size_step, Decimal("1")) or Decimal("1")
    min_size = decimal_from(rules.order_size_min, Decimal("1")) or Decimal("1")
    floored = (raw_abs_size / step).to_integral_value(rounding=ROUND_DOWN) * step
    return floored.normalize() if floored >= min_size else Decimal("0")


def position_size(payload: dict[str, Any]) -> Decimal:
    parsed = decimal_from(payload.get("size"))
    if parsed is None:
        raise GateTwapError(f"Gate position response missing size: {payload}")
    return parsed


def reducible_size(side: str, signed_position: Decimal) -> Decimal:
    if side == "sell":
        return signed_position if signed_position > 0 else Decimal("0")
    return -signed_position if signed_position < 0 else Decimal("0")


def build_plan(
    request: GateTwapRequest,
    *,
    rules: GateTwapContractRules,
    has_credentials: bool,
    signed_position_size: Decimal | None = None,
) -> GateTwapPlan:
    contract = normalize_contract(request.contract)
    settle = request.settle.lower()
    order_count = int(math.floor(request.duration_seconds / request.interval_seconds))
    initial_abs = (
        Decimal(str(request.initial_size))
        if request.initial_size is not None
        else reducible_size(request.side, signed_position_size)
        if signed_position_size is not None
        else None
    )
    start_at = request.start_at or utc_now()
    warnings: list[str] = []
    if order_count <= 0:
        warnings.append("duration / interval produced zero orders")
    if initial_abs is None:
        warnings.append("initial size is unknown; provide initial_size or configure Gate API keys")
    elif initial_abs <= 0:
        warnings.append("no reducible position for the selected side")
    if request.slice_mode == "remaining" and request.last_order_all:
        warnings.append("last_order_all will clear the remainder on the final slice")

    slices: list[GateTwapPlanSlice] = []
    remaining = initial_abs if initial_abs is not None else Decimal("0")
    percent = Decimal(str(request.percent)) / Decimal("100")
    total = Decimal("0")
    for index in range(order_count):
        scheduled_at = start_at.timestamp() + index * request.interval_seconds
        if initial_abs is None or remaining <= 0:
            slices.append(
                GateTwapPlanSlice(
                    index=index + 1,
                    scheduled_at=datetime.fromtimestamp(scheduled_at, tz=UTC),
                    raw_size=0,
                    order_size=0,
                    signed_order_size=0,
                    remaining_after=float(max(remaining, Decimal("0"))),
                    skipped_reason="no position size available" if initial_abs is None else "no remaining position",
                )
            )
            continue
        raw_size = remaining * percent if request.slice_mode == "remaining" else initial_abs * percent
        if request.last_order_all and index == order_count - 1:
            raw_size = remaining
        order_abs = floor_order_size(min(raw_size, remaining), rules)
        skipped = None
        if order_abs <= 0:
            skipped = "below Gate minimum order size"
        else:
            remaining -= order_abs
            total += order_abs
        signed_order = signed_size(request.side, order_abs)
        slices.append(
            GateTwapPlanSlice(
                index=index + 1,
                scheduled_at=datetime.fromtimestamp(scheduled_at, tz=UTC),
                raw_size=float(raw_size),
                order_size=float(order_abs),
                signed_order_size=float(signed_order),
                remaining_after=float(max(remaining, Decimal("0"))),
                skipped_reason=skipped,
            )
        )

    return GateTwapPlan(
        request=request.model_copy(update={"contract": contract, "settle": settle}),
        contract=contract,
        settle=settle,
        side=request.side,
        order_count=order_count,
        initial_size=float(initial_abs) if initial_abs is not None else None,
        signed_position_size=float(signed_position_size) if signed_position_size is not None else None,
        has_credentials=has_credentials,
        rules=rules,
        total_planned_size=float(total),
        slices=slices,
        warnings=warnings,
    )


def ticker_book_from_spot(payload: dict[str, Any] | None) -> GateTickerBook | None:
    if not payload:
        return None
    bid = float_from(payload.get("highest_bid"))
    ask = float_from(payload.get("lowest_ask"))
    return GateTickerBook(
        bid=bid,
        ask=ask,
        bid_size=float_from(payload.get("highest_size")),
        ask_size=float_from(payload.get("lowest_size")),
        mid=(bid + ask) / 2 if bid is not None and ask is not None else None,
        last=float_from(payload.get("last")),
        volume_24h_usdt=float_from(payload.get("quote_volume")),
    )


def ticker_book_from_future(payload: dict[str, Any] | None) -> GateTickerBook | None:
    if not payload:
        return None
    bid = float_from(payload.get("highest_bid"))
    ask = float_from(payload.get("lowest_ask"))
    return GateTickerBook(
        bid=bid,
        ask=ask,
        bid_size=float_from(payload.get("highest_size")),
        ask_size=float_from(payload.get("lowest_size")),
        mid=(bid + ask) / 2 if bid is not None and ask is not None else None,
        last=float_from(payload.get("last")),
        volume_24h_usdt=float_from(payload.get("volume_24h_quote")),
    )


async def fetch_market_snapshot(
    client: GateTwapClient,
    *,
    contract: str,
    settle: str = "usdt",
) -> GateTwapMarketSnapshot:
    normalized = normalize_contract(contract)
    pair = spot_pair_for_contract(normalized)
    contract_payload, future_payload, spot_payload = await asyncio.gather(
        client.get_contract(settle, normalized),
        client.get_futures_ticker(settle, normalized),
        client.get_spot_ticker(pair),
    )
    rules = rules_from_contract(contract_payload)
    future = ticker_book_from_future(future_payload)
    spot = ticker_book_from_spot(spot_payload)
    mark = float_from((future_payload or {}).get("mark_price"))
    index = float_from((future_payload or {}).get("index_price"))
    future_mid = future.mid if future else None
    spot_mid = spot.mid if spot else None
    next_apply = float_from(contract_payload.get("funding_next_apply"))
    return GateTwapMarketSnapshot(
        contract=normalized,
        spot_pair=pair,
        observed_at=utc_now(),
        spot_available=spot is not None,
        spot=spot,
        future=future,
        mark_price=mark,
        index_price=index,
        mark_index_premium_pct=pct_diff(mark, index),
        future_index_premium_pct=pct_diff(future_mid, index),
        future_spot_premium_pct=pct_diff(future_mid, spot_mid),
        funding_rate_pct=(float_from((future_payload or {}).get("funding_rate")) or 0) * 100
        if future_payload and float_from(future_payload.get("funding_rate")) is not None
        else None,
        funding_next_rate_pct=(float_from((future_payload or {}).get("funding_rate_indicative")) or 0) * 100
        if future_payload and float_from(future_payload.get("funding_rate_indicative")) is not None
        else None,
        funding_interval_hours=(float_from(contract_payload.get("funding_interval")) or 0) / 3600
        if contract_payload.get("funding_interval") is not None
        else None,
        funding_next_time=datetime.fromtimestamp(next_apply, tz=UTC) if next_apply else None,
        contract_status=contract_payload.get("status"),
        order_size_min=rules.order_size_min,
        order_size_step=rules.order_size_step,
        market_order_slip_ratio=rules.market_order_slip_ratio,
    )


def build_order_text(prefix: str, run_id: str, index: int) -> str:
    clean = prefix.strip() or "t-twap"
    if not clean.startswith("t-"):
        clean = f"t-{clean}"
    text = f"{clean}-{run_id}-{index:03d}"
    return text if len(text) <= 28 else f"{clean[:12]}-{run_id[-8:]}-{index:03d}"


class GateTwapJobManager:
    def __init__(self, client: GateTwapClient):
        self.client = client
        self.jobs: dict[str, GateTwapJobStatus] = {}
        self.tasks: dict[str, asyncio.Task] = {}

    def list_jobs(self) -> list[GateTwapJobStatus]:
        return sorted(self.jobs.values(), key=lambda item: item.created_at, reverse=True)

    def get_job(self, job_id: str) -> GateTwapJobStatus | None:
        return self.jobs.get(job_id)

    async def aclose(self) -> None:
        for job_id, task in list(self.tasks.items()):
            if not task.done():
                task.cancel()
                job = self.jobs.get(job_id)
                if job is not None and job.state in {"queued", "running"}:
                    job.state = "cancelled"
                    job.finished_at = utc_now()
        for task in list(self.tasks.values()):
            with suppress(asyncio.CancelledError):
                await task
        await self.client.aclose()

    async def preview(self, request: GateTwapRequest) -> GateTwapPlan:
        contract = normalize_contract(request.contract)
        rules = rules_from_contract(await self.client.get_contract(request.settle.lower(), contract))
        signed_position: Decimal | None = None
        if request.initial_size is None and self.client.has_credentials:
            signed_position = position_size(await self.client.get_position(request.settle.lower(), contract))
        return build_plan(
            request.model_copy(update={"contract": contract, "settle": request.settle.lower()}),
            rules=rules,
            has_credentials=self.client.has_credentials,
            signed_position_size=signed_position,
        )

    async def start(self, request: GateTwapRunRequest) -> GateTwapJobStatus:
        if request.live and not request.confirm_live:
            raise GateTwapError("Live run requires confirm_live=true")
        if request.live and not self.client.has_credentials:
            raise GateTwapError("Live run requires GATE_API_KEY/GATE_API_SECRET")
        plan = await self.preview(request)
        job_id = uuid.uuid4().hex[:12]
        status = GateTwapJobStatus(
            job_id=job_id,
            state="queued",
            live=request.live,
            request=request.model_copy(update={"contract": plan.contract, "settle": plan.settle}),
            plan=plan,
            created_at=utc_now(),
        )
        self.jobs[job_id] = status
        self.tasks[job_id] = asyncio.create_task(self._run(job_id))
        return status

    async def cancel(self, job_id: str) -> GateTwapJobStatus:
        job = self.jobs.get(job_id)
        if job is None:
            raise GateTwapError("TWAP job not found")
        task = self.tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        job.state = "cancelled"
        job.finished_at = utc_now()
        job.events.append(GateTwapJobEvent(at=utc_now(), level="warning", message="Job cancelled"))
        return job

    async def _run(self, job_id: str) -> None:
        job = self.jobs[job_id]
        job.state = "running"
        job.started_at = utc_now()
        run_id = datetime.now().strftime("%m%d%H%M")
        try:
            if job.plan is None:
                raise GateTwapError("Job missing plan")
            for item in job.plan.slices:
                wait_seconds = item.scheduled_at.timestamp() - time.time()
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)
                if item.skipped_reason:
                    job.skipped_orders += 1
                    job.events.append(
                        GateTwapJobEvent(
                            at=utc_now(),
                            level="warning",
                            message=f"Slice {item.index} skipped: {item.skipped_reason}",
                        )
                    )
                    continue
                order = {
                    "contract": job.plan.contract,
                    "size": decimal_plain(Decimal(str(item.signed_order_size))),
                    "price": "0",
                    "tif": "ioc",
                    "reduce_only": True,
                    "text": build_order_text(job.request.client_prefix, run_id, item.index),
                    "action_mode": job.request.action_mode,
                }
                if job.request.slip_ratio is not None:
                    order["market_order_slip_ratio"] = str(job.request.slip_ratio)
                if job.live:
                    response = await self.client.create_futures_order(job.plan.settle, order)
                    job.events.append(
                        GateTwapJobEvent(
                            at=utc_now(),
                            level="info",
                            message=f"Live slice {item.index} submitted",
                            order=order,
                            response=response,
                        )
                    )
                else:
                    job.events.append(
                        GateTwapJobEvent(
                            at=utc_now(),
                            level="info",
                            message=f"Dry slice {item.index} generated",
                            order=order,
                        )
                    )
                job.completed_orders += 1
                job.total_order_size += item.order_size
            job.state = "completed"
            job.finished_at = utc_now()
        except asyncio.CancelledError:
            job.state = "cancelled"
            job.finished_at = utc_now()
            raise
        except Exception as exc:
            job.state = "failed"
            job.last_error = str(exc)
            job.finished_at = utc_now()
            job.events.append(GateTwapJobEvent(at=utc_now(), level="error", message=str(exc)))
