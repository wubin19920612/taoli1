#!/usr/bin/env python3
"""
Gate futures TWAP reducer.

Default mode is safe: it never places live orders unless both --live and --yes
are provided. Use --preview-only first to inspect the schedule.

Examples:
  python script/gate_twap_sell.py --contract SKHYNIX_USDT --initial-size 1000 --preview-only

  python script/gate_twap_sell.py --contract SKHYNIX_USDT --env-file .env --preview-only

  python script/gate_twap_sell.py --contract SKHYNIX_USDT --env-file .env --start "2026-06-05 15:50:00"

  python script/gate_twap_sell.py --contract SKHYNIX_USDT --env-file .env --start "2026-06-05 15:50:00" --live --yes
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from pathlib import Path
from typing import Any


GATE_API_BASE = "https://api.gateio.ws/api/v4"


class GateApiError(RuntimeError):
    pass


def load_env_file(path: str | None) -> None:
    if not path:
        return
    env_path = Path(path)
    if not env_path.exists():
        raise SystemExit(f"env file not found: {env_path}")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def decimal_from(value: Any, default: Decimal | None = None) -> Decimal | None:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default


def local_timezone():
    return datetime.now().astimezone().tzinfo


def parse_start_ts(value: str | None) -> float:
    if not value or value.lower() == "now":
        return time.time()

    stripped = value.strip()
    if stripped.isdigit():
        raw = int(stripped)
        if raw > 10_000_000_000:
            return raw / 1000.0
        return float(raw)

    try:
        dt = datetime.fromisoformat(stripped)
    except ValueError as exc:
        raise SystemExit(
            "Invalid --start. Use 'now', unix timestamp, or ISO format like "
            "'2026-06-05 15:50:00'."
        ) from exc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_timezone())
    return dt.timestamp()


def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def sleep_until(target_ts: float) -> None:
    while True:
        remaining = target_ts - time.time()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.5))


def signed_order_size(side: str, abs_size: Decimal) -> str:
    if side == "sell":
        return f"-{abs_size:f}"
    if side == "buy":
        return f"{abs_size:f}"
    raise ValueError(f"unsupported side: {side}")


def normalize_contract(raw: str) -> str:
    value = raw.strip().upper().replace("-", "_")
    if "_" not in value and value.endswith("USDT"):
        return f"{value[:-4]}_USDT"
    return value


class GateClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        api_secret: str | None,
        base_url: str = GATE_API_BASE,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def sign_prefix(self) -> str:
        return urllib.parse.urlsplit(self.base_url).path.rstrip("/")

    def _sign_headers(self, method: str, path: str, query: str, body: bytes) -> dict[str, str]:
        if not self.api_key or not self.api_secret:
            raise GateApiError("Missing GATE_API_KEY/GATE_API_SECRET")
        timestamp = str(int(time.time()))
        body_hash = hashlib.sha512(body).hexdigest()
        sign_path = f"{self.sign_prefix}{path}"
        sign_payload = "\n".join([method.upper(), sign_path, query, body_hash, timestamp])
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            sign_payload.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()
        return {"KEY": self.api_key, "Timestamp": timestamp, "SIGN": signature}

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        auth: bool = False,
    ) -> Any:
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"

        body_bytes = b""
        if body is not None:
            body_bytes = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

        headers = {
            "Accept": "application/json",
            "User-Agent": "gate-twap-sell/0.1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if auth:
            headers.update(self._sign_headers(method, path, query, body_bytes))

        request = urllib.request.Request(
            url,
            data=body_bytes if body is not None else None,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise GateApiError(f"{method} {path} HTTP {exc.code}: {error_body}") from exc
        except urllib.error.URLError as exc:
            raise GateApiError(f"{method} {path} failed: {exc}") from exc

        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise GateApiError(f"{method} {path} returned non-JSON: {payload[:300]}") from exc

    def get_contract(self, settle: str, contract: str) -> dict[str, Any]:
        return self.request("GET", f"/futures/{settle}/contracts/{contract}", auth=False)

    def get_position(self, settle: str, contract: str) -> dict[str, Any]:
        return self.request("GET", f"/futures/{settle}/positions/{contract}", auth=True)

    def create_futures_order(self, settle: str, order: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"/futures/{settle}/orders", body=order, auth=True)


@dataclass
class ContractRules:
    order_size_min: Decimal
    enable_decimal: bool
    market_order_slip_ratio: Decimal | None

    @property
    def step(self) -> Decimal:
        if not self.enable_decimal:
            return Decimal("1")
        exponent = self.order_size_min.as_tuple().exponent
        if exponent < 0:
            return Decimal(1).scaleb(exponent)
        return Decimal("1")

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "ContractRules":
        payload = payload or {}
        return cls(
            order_size_min=decimal_from(payload.get("order_size_min"), Decimal("1")) or Decimal("1"),
            enable_decimal=bool(payload.get("enable_decimal", False)),
            market_order_slip_ratio=decimal_from(payload.get("market_order_slip_ratio")),
        )

    def floor_order_size(self, raw_abs_size: Decimal) -> Decimal:
        if raw_abs_size <= 0:
            return Decimal("0")
        step = self.step
        floored = (raw_abs_size / step).to_integral_value(rounding=ROUND_DOWN) * step
        if floored < self.order_size_min:
            return Decimal("0")
        return floored.normalize()


@dataclass
class RuntimeConfig:
    settle: str
    contract: str
    side: str
    start_ts: float
    interval_seconds: float
    duration_seconds: float
    percent: Decimal
    slice_mode: str
    live: bool
    yes: bool
    preview_only: bool
    initial_size: Decimal | None
    last_order_all: bool
    slip_ratio: Decimal | None
    client_prefix: str
    action_mode: str
    base_url: str
    timeout: float

    @property
    def order_count(self) -> int:
        if self.interval_seconds <= 0:
            raise ValueError("interval must be positive")
        return int(math.floor(self.duration_seconds / self.interval_seconds))


def position_size_from_payload(payload: dict[str, Any]) -> Decimal:
    size = decimal_from(payload.get("size"))
    if size is None:
        raise GateApiError(f"Position response does not contain size: {payload}")
    return size


def current_reducible_abs_size(
    *,
    side: str,
    signed_position_size: Decimal,
) -> Decimal:
    if side == "sell":
        return signed_position_size if signed_position_size > 0 else Decimal("0")
    if side == "buy":
        return -signed_position_size if signed_position_size < 0 else Decimal("0")
    raise ValueError(f"unsupported side: {side}")


def build_order_text(prefix: str, run_id: str, index: int) -> str:
    clean = prefix.strip() or "t-twap"
    if not clean.startswith("t-"):
        clean = f"t-{clean}"
    text = f"{clean}-{run_id}-{index:03d}"
    if len(text) > 28:
        text = f"{clean[:12]}-{run_id[-8:]}-{index:03d}"
    return text


def print_plan_header(config: RuntimeConfig, rules: ContractRules, initial_abs: Decimal | None) -> None:
    print("Gate TWAP reducer")
    print(f"  contract       : {config.contract}")
    print(f"  settle         : {config.settle}")
    print(f"  side           : {config.side} ({'reduce long' if config.side == 'sell' else 'reduce short'})")
    print(f"  mode           : {'LIVE' if config.live else 'DRY RUN'}")
    print(f"  start          : {fmt_ts(config.start_ts)}")
    print(f"  interval       : {config.interval_seconds:g}s")
    print(f"  duration       : {config.duration_seconds:g}s")
    print(f"  order count    : {config.order_count}")
    print(f"  slice mode     : {config.slice_mode}")
    print(f"  percent        : {config.percent}%")
    print(f"  order min/step : min={rules.order_size_min:f}, step={rules.step:f}")
    if rules.market_order_slip_ratio is not None:
        print(f"  gate slip cap  : {rules.market_order_slip_ratio:f}")
    if config.slip_ratio is not None:
        print(f"  custom slip    : {config.slip_ratio:f}")
    if initial_abs is not None:
        print(f"  initial size   : {initial_abs:f} contracts")
    print()


def resolve_initial_size(
    client: GateClient,
    config: RuntimeConfig,
) -> tuple[Decimal | None, Decimal | None]:
    if config.initial_size is not None:
        signed = config.initial_size if config.side == "sell" else -config.initial_size
        return signed, config.initial_size.copy_abs()

    if not client.has_credentials:
        return None, None

    payload = client.get_position(config.settle, config.contract)
    signed = position_size_from_payload(payload)
    return signed, current_reducible_abs_size(side=config.side, signed_position_size=signed)


def preview_schedule(config: RuntimeConfig, rules: ContractRules, initial_abs: Decimal | None) -> None:
    if initial_abs is None:
        print("No initial size available. Add --initial-size or provide API keys to preview exact sizes.")
        return

    remaining = initial_abs
    print("Preview schedule:")
    print("  idx  time                       raw_size      order_size    remaining_after")

    for idx in range(config.order_count):
        target_ts = config.start_ts + idx * config.interval_seconds
        if remaining <= 0:
            break

        if config.slice_mode == "remaining":
            raw_size = remaining * config.percent / Decimal("100")
        else:
            raw_size = initial_abs * config.percent / Decimal("100")

        if config.last_order_all and idx == config.order_count - 1:
            raw_size = remaining

        order_abs = rules.floor_order_size(min(raw_size, remaining))
        if order_abs <= 0:
            print(f"  {idx+1:>3}  {fmt_ts(target_ts)}  {raw_size:f}  <min-size>   {remaining:f}")
            continue

        remaining -= order_abs
        print(
            f"  {idx+1:>3}  {fmt_ts(target_ts)}  "
            f"{raw_size:f}  {order_abs:f}  {remaining:f}"
        )


def place_or_print_order(
    *,
    client: GateClient,
    config: RuntimeConfig,
    order_abs: Decimal,
    order_text: str,
) -> dict[str, Any] | None:
    order: dict[str, Any] = {
        "contract": config.contract,
        "size": signed_order_size(config.side, order_abs),
        "price": "0",
        "tif": "ioc",
        "reduce_only": True,
        "text": order_text,
        "action_mode": config.action_mode,
    }
    if config.slip_ratio is not None:
        order["market_order_slip_ratio"] = f"{config.slip_ratio:f}"

    if not config.live:
        print(f"DRY order: {json.dumps(order, ensure_ascii=False, separators=(',', ':'))}")
        return None

    response = client.create_futures_order(config.settle, order)
    print(f"LIVE response: {json.dumps(response, ensure_ascii=False, separators=(',', ':'))}")
    return response


def run_schedule(client: GateClient, config: RuntimeConfig, rules: ContractRules, initial_abs: Decimal | None) -> None:
    if config.live and not client.has_credentials:
        raise SystemExit("Live mode requires GATE_API_KEY and GATE_API_SECRET.")
    if config.live and not config.yes:
        raise SystemExit("Live mode requires --yes. This is a guard against accidental real orders.")
    if config.order_count <= 0:
        raise SystemExit("duration / interval produced zero orders.")

    run_id = datetime.now().strftime("%m%d%H%M")
    simulated_remaining = initial_abs

    for idx in range(config.order_count):
        target_ts = config.start_ts + idx * config.interval_seconds
        print(f"\n[{idx+1}/{config.order_count}] target {fmt_ts(target_ts)}")
        sleep_until(target_ts)

        if client.has_credentials:
            position_payload = client.get_position(config.settle, config.contract)
            signed_position = position_size_from_payload(position_payload)
            current_abs = current_reducible_abs_size(
                side=config.side,
                signed_position_size=signed_position,
            )
            print(f"Position size: {signed_position:f}, reducible: {current_abs:f}")
        elif simulated_remaining is not None:
            current_abs = simulated_remaining
            print(f"Simulated reducible: {current_abs:f}")
        else:
            raise SystemExit("Need API keys or --initial-size to know order size.")

        if current_abs <= 0:
            print("No reducible position remains; stopping.")
            return

        if config.slice_mode == "remaining":
            raw_order_abs = current_abs * config.percent / Decimal("100")
        else:
            base_abs = initial_abs if initial_abs is not None else current_abs
            raw_order_abs = base_abs * config.percent / Decimal("100")

        if config.last_order_all and idx == config.order_count - 1:
            raw_order_abs = current_abs

        order_abs = rules.floor_order_size(min(raw_order_abs, current_abs))
        if order_abs <= 0:
            print(
                f"Calculated order size {raw_order_abs:f} is below Gate min size "
                f"{rules.order_size_min:f}; skipping."
            )
            continue

        order_text = build_order_text(config.client_prefix, run_id, idx + 1)
        place_or_print_order(client=client, config=config, order_abs=order_abs, order_text=order_text)

        if simulated_remaining is not None and not client.has_credentials:
            simulated_remaining = max(Decimal("0"), simulated_remaining - order_abs)


def parse_args(argv: list[str]) -> RuntimeConfig:
    parser = argparse.ArgumentParser(
        description="Sell/reduce a Gate futures position with timed market IOC orders.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--contract", required=True, help="Gate futures contract, e.g. SKHYNIX_USDT")
    parser.add_argument("--settle", default="usdt", help="Settlement currency path segment")
    parser.add_argument("--side", choices=("sell", "buy"), default="sell", help="sell reduces long; buy reduces short")
    parser.add_argument("--start", default="now", help="Start time: now, unix timestamp, or ISO local time")
    parser.add_argument("--interval", type=float, default=10.0, help="Seconds between orders")
    parser.add_argument("--duration", type=float, default=1000.0, help="Total schedule duration in seconds")
    parser.add_argument("--percent", default="1", help="Percent per order")
    parser.add_argument(
        "--slice-mode",
        choices=("initial", "remaining"),
        default="initial",
        help="initial sells percent of initial position; remaining sells percent of current remaining position",
    )
    parser.add_argument("--initial-size", help="Initial reducible size in contracts; useful for offline dry runs")
    parser.add_argument("--no-last-order-all", action="store_true", help="Do not use the final order to clear remainder")
    parser.add_argument("--slip-ratio", help="Optional Gate market_order_slip_ratio, e.g. 0.003 for 0.3%")
    parser.add_argument("--client-prefix", default="t-twap", help="Gate order text prefix; script enforces t- prefix")
    parser.add_argument("--action-mode", choices=("ACK", "RESULT", "FULL"), default="ACK")
    parser.add_argument("--env-file", help="Optional .env file containing GATE_API_KEY and GATE_API_SECRET")
    parser.add_argument("--base-url", default=GATE_API_BASE, help="Gate API base URL")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout seconds")
    parser.add_argument("--preview-only", action="store_true", help="Print schedule and exit without waiting")
    parser.add_argument("--live", action="store_true", help="Place real Gate orders")
    parser.add_argument("--yes", action="store_true", help="Required together with --live")

    args = parser.parse_args(argv)

    if args.interval <= 0:
        raise SystemExit("--interval must be positive")
    if args.duration <= 0:
        raise SystemExit("--duration must be positive")

    percent = decimal_from(args.percent)
    if percent is None or percent <= 0:
        raise SystemExit("--percent must be a positive number")

    initial_size = decimal_from(args.initial_size) if args.initial_size else None
    if initial_size is not None and initial_size <= 0:
        raise SystemExit("--initial-size must be positive")

    slip_ratio = decimal_from(args.slip_ratio) if args.slip_ratio else None
    if slip_ratio is not None and slip_ratio < 0:
        raise SystemExit("--slip-ratio cannot be negative")

    config = RuntimeConfig(
        settle=args.settle.lower(),
        contract=normalize_contract(args.contract),
        side=args.side,
        start_ts=parse_start_ts(args.start),
        interval_seconds=args.interval,
        duration_seconds=args.duration,
        percent=percent,
        slice_mode=args.slice_mode,
        live=bool(args.live),
        yes=bool(args.yes),
        preview_only=bool(args.preview_only),
        initial_size=initial_size,
        last_order_all=not args.no_last_order_all,
        slip_ratio=slip_ratio,
        client_prefix=args.client_prefix,
        action_mode=args.action_mode,
        base_url=args.base_url,
        timeout=args.timeout,
    )

    load_env_file(args.env_file)
    return config


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    config = parse_args(argv)
    api_key = os.environ.get("GATE_API_KEY")
    api_secret = os.environ.get("GATE_API_SECRET")

    client = GateClient(
        api_key=api_key,
        api_secret=api_secret,
        base_url=os.environ.get("GATE_API_BASE", config.base_url),
        timeout=config.timeout,
    )

    contract_payload: dict[str, Any] | None = None
    try:
        contract_payload = client.get_contract(config.settle, config.contract)
    except GateApiError as exc:
        print(f"Warning: could not fetch contract rules, using conservative defaults: {exc}")

    rules = ContractRules.from_payload(contract_payload)
    signed_initial, initial_abs = resolve_initial_size(client, config)

    if signed_initial is not None:
        reducible = current_reducible_abs_size(side=config.side, signed_position_size=signed_initial)
        if reducible <= 0:
            print(
                f"Warning: signed position size is {signed_initial:f}; "
                f"side={config.side} has no reducible position."
            )

    print_plan_header(config, rules, initial_abs)
    preview_schedule(config, rules, initial_abs)

    if config.preview_only:
        print("\nPreview only; no schedule is running.")
        return 0

    if not config.live:
        print("\nDRY RUN schedule is starting. No live orders will be sent.")
    else:
        print("\nLIVE schedule is starting. Real reduce-only market orders will be sent.")

    try:
        run_schedule(client, config, rules, initial_abs)
    except KeyboardInterrupt:
        print("\nInterrupted by user; stopped.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
