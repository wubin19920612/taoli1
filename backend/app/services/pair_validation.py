from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta

from app.models.market_identity import (
    MarketKey,
    PairCapabilities,
    PairValidationResult,
    ValidationIssue,
    utc,
)


def quote_fingerprint(a, b):
    values = [
        (
            r.bid,
            r.ask,
            str(r.book_timestamp or r.upstream_timestamp or r.timestamp),
            r.binding_revision,
            r.binding_fingerprint,
            r.quote_conversion_rate,
        )
        for r in (a, b)
    ]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def validate_pair(
    a,
    b,
    *,
    now=None,
    comparison_kind="same_asset",
    stale_after_seconds=30,
    max_leg_time_skew_seconds=10,
    huge_spread_pct=10,
    review_passed=False,
):
    now = utc(now or datetime.now(UTC))
    blockers = []
    warnings = []
    keys = []
    times = []
    valid_until = [now + timedelta(seconds=15)]

    def block(code, message, leg="pair"):
        blockers.append(ValidationIssue(code=code, message=message, leg=leg))

    for leg, row in (("buy", a), ("sell", b)):
        try:
            keys.append(MarketKey.from_snapshot(row))
        except ValueError:
            keys.append(None)
            block("DEX_IDENTITY_CONFLICT", "DEX 与原始市场代码冲突", leg)
        if row.identity_status == "conflict":
            block("ASSET_IDENTITY_CONFLICT", "资产或合约规格与核验记录冲突", leg)
        elif (
            row.identity_status != "verified"
            or not row.asset_id
            or not row.binding_valid_until
            or utc(row.binding_valid_until) <= now
        ):
            block("ASSET_IDENTITY_UNVERIFIED", "资产身份待核验或证据已过期", leg)
        if row.unit_status != "verified":
            block("UNIT_UNVERIFIED", "价格与合约单位待核验", leg)
        if row.binding_valid_until:
            valid_until.append(utc(row.binding_valid_until))
        estimated = {x.lower() for x in row.estimated_fields}
        if row.quote_kind != "book" or estimated.intersection({"bid", "ask"}):
            block("REFERENCE_QUOTE_ONLY", "仅有参考价格，缺少真实买卖报价", leg)
        if (
            not math.isfinite(row.bid)
            or not math.isfinite(row.ask)
            or row.bid <= 0
            or row.ask <= 0
            or row.bid > row.ask
        ):
            block("INVALID_ORDER_BOOK", "盘口价格无效或倒挂", leg)
        if row.contract_kind == "inverse":
            block("INCOMPATIBLE_CONTRACT_SPEC", "反向合约的成交与数量换算尚未核验", leg)
        observed = utc(row.book_timestamp or row.upstream_timestamp or row.timestamp)
        if (row.book_timestamp is not None and row.quote_time_source != "upstream") or (
            row.book_timestamp is None and row.upstream_timestamp is None
        ):
            warnings.append(
                ValidationIssue(
                    code="LOCAL_RECEIVE_TIME",
                    message="来源未提供上游行情时间，时效按本地接收时间计算",
                    leg=leg,
                )
            )
        times.append(observed)
        valid_until.append(observed + timedelta(seconds=stale_after_seconds))
        if (now - observed).total_seconds() > stale_after_seconds or (
            observed - now
        ).total_seconds() > 5:
            block("STALE_LEG_QUOTE", "报价已过期或时间异常", leg)
        currency = row.original_quote_currency or row.quote
        if currency != "USDT":
            if (
                not row.quote_conversion_source
                or not row.quote_conversion_valid_until
                or utc(row.quote_conversion_valid_until) <= now
            ):
                block("QUOTE_CONVERSION_UNVERIFIED", f"{currency} 到 USDT 缺少有效换算", leg)
            else:
                valid_until.append(utc(row.quote_conversion_valid_until))
        if row.volume_24h_usdt is None:
            warnings.append(
                ValidationIssue(code="UNKNOWN_VOLUME", message="本侧 24h 成交额未知", leg=leg)
            )
    statuses = {a.identity_status, b.identity_status}
    relation = "conflict" if "conflict" in statuses else "unresolved"
    if a.asset_id and b.asset_id and a.asset_id != b.asset_id:
        relation = "different"
    elif statuses == {"verified"} and a.asset_id and b.asset_id:
        relation = "same"
    if a.asset_kind and b.asset_kind and a.asset_kind != b.asset_kind:
        relation = "different"
    if relation == "different" and comparison_kind == "same_asset":
        block("DIFFERENT_UNDERLYING_ASSET", "双方属于不同底层资产，不能作为同币配对")
    if (a.contract_kind == "valuation" or b.contract_kind == "valuation") and (
        a.contract_kind != b.contract_kind
        or not a.tracking_definition
        or a.tracking_definition != b.tracking_definition
    ):
        block("INCOMPATIBLE_CONTRACT_SPEC", "估值口径与计价单位不一致，不能直接比较")
    if (
        (a.identity_collision or b.identity_collision)
        and relation != "same"
        and comparison_kind == "same_asset"
    ):
        relation = "conflict"
        block("ASSET_IDENTITY_CONFLICT", "同名代码已确认对应多个资产，须先选择并核验精确市场")
    if abs((times[0] - times[1]).total_seconds()) > max_leg_time_skew_seconds:
        block("LEG_TIME_SKEW", "双方行情时间差过大")
    scale = max(b.bid, a.ask)
    spread = (
        abs(200 * (b.bid / scale - a.ask / scale) / (b.bid / scale + a.ask / scale))
        if math.isfinite(scale) and scale > 0
        else 0
    )
    anomaly = "normal"
    if spread >= huge_spread_pct and comparison_kind == "same_asset":
        anomaly = "passed" if review_passed else "pending_review"
        if not review_passed:
            block("SPREAD_REVIEW_PENDING", "大价差等待双腿盘口与深度复核")
    allowed = not blockers
    same = allowed and relation == "same" and comparison_kind == "same_asset"
    return PairValidationResult(
        comparison_kind=comparison_kind,
        identity_relation=relation,
        unit_status="verified" if a.unit_status == b.unit_status == "verified" else "unverified",
        quote_status="reference"
        if any(x.code == "REFERENCE_QUOTE_ONLY" for x in blockers)
        else "invalid"
        if any(
            x.code
            in {
                "INVALID_ORDER_BOOK",
                "STALE_LEG_QUOTE",
                "LEG_TIME_SKEW",
                "QUOTE_CONVERSION_UNVERIFIED",
            }
            for x in blockers
        )
        else "executable",
        anomaly_status=anomaly,
        buy_market=keys[0],
        sell_market=keys[1],
        buy_asset_id=a.asset_id,
        sell_asset_id=b.asset_id,
        buy_revision=a.binding_revision,
        sell_revision=b.binding_revision,
        buy_spec_fingerprint=a.binding_fingerprint,
        sell_spec_fingerprint=b.binding_fingerprint,
        quote_fingerprint=quote_fingerprint(a, b),
        blockers=blockers,
        warnings=warnings,
        capabilities=PairCapabilities(
            reference_comparison=relation == "same" or comparison_kind == "custom_cross_asset",
            same_asset_opportunity=same,
            strategy_alert=same,
            automatic_card=same,
            manual_card=allowed and (same or comparison_kind == "custom_cross_asset"),
        ),
        validated_at=now,
        expires_at=min(valid_until),
    )


def same_asset_eligible(a, b):
    """Legacy isolated calculations may be unannotated; live rows are annotated."""
    if "conflict" in {a.identity_status, b.identity_status}:
        return False
    if a.asset_kind and b.asset_kind and a.asset_kind != b.asset_kind:
        return False
    if a.asset_id and b.asset_id and a.asset_id != b.asset_id:
        return False
    if (a.identity_collision or b.identity_collision) and not (
        a.identity_status == b.identity_status == "verified"
        and a.asset_id
        and a.asset_id == b.asset_id
    ):
        return False
    if not (a.identity_enforced or b.identity_enforced):
        return True
    return (
        a.identity_status == b.identity_status == "verified"
        and a.asset_id is not None
        and a.asset_id == b.asset_id
        and a.unit_status == b.unit_status == "verified"
    )
