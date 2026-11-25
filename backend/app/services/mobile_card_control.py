"""Session-scoped authorization and single-field writes to live Astro cards."""

import asyncio
import base64
import logging
import secrets
import sqlite3
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from starlette.datastructures import State

from app.db.repositories import MobileOpsRepository
from app.models.mobile import CardControlUpdate, DeviceBinding, DeviceProof, UnlockCode
from app.services.astro_client import AstroClientError

logger = logging.getLogger(__name__)
card_update_logger = logging.getLogger("astro_card_update")
FIELDS = {
    "status": ("status", "运行状态"),
    "disable_open": ("disableOpen", "禁止开仓"),
    "disable_close": ("disableClose", "禁止清仓"),
}
CONFLICT = "卡片已变化，请刷新"


@dataclass(frozen=True)
class Challenge:
    session_hash: str
    device_id: str
    nonce: str
    expires_at: float


@dataclass(frozen=True)
class UnlockWindow:
    until: float
    method: str


def _public_key(encoded: str) -> ec.EllipticCurvePublicKey:
    key = serialization.load_der_public_key(base64.b64decode(encoded, validate=True))
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise UnsupportedAlgorithm("设备公钥必须使用 P-256")
    return key


def _verify_signature(public_key: str, proof: DeviceProof, nonce: str) -> bool:
    signature = proof.signature.get_secret_value()
    if len(signature) > 96:  # A DER P-256 signature is at most 72 bytes.
        return False
    message = f"astro-mobile-unlock\n{proof.challenge_id}\n{nonce}".encode()
    try:
        decoded = base64.b64decode(signature, validate=True)
        _public_key(public_key).verify(decoded, message, ec.ECDSA(hashes.SHA256()))
    except (ValueError, InvalidSignature, UnsupportedAlgorithm):
        return False
    return True


async def _latest_pair(client, card_id: str) -> dict:
    pairs = [p for p in await client.list_pairs() if p.get("id") == card_id]
    if not pairs:
        raise HTTPException(404, "找不到卡片")
    if len(pairs) != 1 or not isinstance(pairs[0].get("name"), str) or not pairs[0]["name"].strip():
        raise HTTPException(409, CONFLICT)
    return pairs[0]


class MobileCardControl:
    def __init__(self, repo: MobileOpsRepository, state: State):
        self.repo = repo
        self.state = state
        self._challenges: dict[str, Challenge] = {}
        self._windows: dict[str, UnlockWindow] = {}
        self._attempts: dict[str, deque[float]] = {}
        self._kill_attempts: dict[str, deque[float]] = {}
        self._switch_lock = asyncio.Lock()
        self._unlock_generation = 0

    async def switch_status(self) -> dict:
        try:
            runtime, changed_at, changed_by = await self.repo.runtime_switch()
        except Exception:
            logger.warning("无法读取手机改卡运行时开关，按关闭处理", exc_info=True)
            runtime, changed_at, changed_by = False, None, None
        settings = self.state.settings
        env = settings.mobile_card_control_enabled is True
        dry_run = settings.astro_dry_run_only
        return {"env_enabled": env, "dry_run": dry_run, "runtime_enabled": runtime,
                "effective": env and dry_run is False and runtime, "changed_at": changed_at,
                "changed_by": changed_by}

    async def control_enabled(self) -> bool:
        return (await self.switch_status())["effective"]

    async def set_runtime_switch(self, enabled: bool, source: str) -> dict:
        if source == "web":
            title = "手机改卡已在网页重新打开" if enabled else "手机改卡已在网页停用"
        else:
            title = "手机改卡已紧急停用"
        async with self._switch_lock:
            now = time.time()
            body = f"来源：{source}，时间：{datetime.fromtimestamp(now, UTC).isoformat(timespec='seconds')}"
            try:
                changed = await self.repo.change_runtime_switch(enabled, now, source, title, body)
            except (sqlite3.Error, ValueError):
                raise HTTPException(503, "手机改卡开关保存失败") from None
            if changed and not enabled:
                self._unlock_generation += 1
                self._windows.clear()
            status = await self.switch_status()
        if changed:
            try:
                await self.state.mobile_push.send_urgent(
                    kind="card_control", title=title, body=body, card_id="", card_name="",
                )
            except Exception:
                logger.warning("手机改卡开关已更新，紧急推送失败")
        return status

    async def kill(self, session: str, browser: str):
        self._admit_attempt(self._kill_attempts, session, 10)
        await self.set_runtime_switch(False, f"mobile:{browser}")
        return {"enabled": False}

    def _window(self, session: str) -> UnlockWindow | None:
        window = self._windows.get(session)
        if window is not None and window.until <= time.time():
            del self._windows[session]
            return None
        return window

    def require_unlock(self, session: str) -> str:
        window = self._window(session)
        if window is None:
            raise HTTPException(403, "请先解锁")
        return window.method

    async def status(self, session: str) -> dict:
        switch = await self.switch_status()
        enabled = switch["effective"]
        window = self._window(session) if enabled else None
        return {"enabled": enabled, "env_enabled": switch["env_enabled"], "runtime_enabled": switch["runtime_enabled"],
                "changed_at": switch["changed_at"], "changed_by": switch["changed_by"],
                "unlocked_until": window.until if window else None,
                "unlock_minutes": await self.repo.unlock_minutes(), "devices": await self.repo.devices()}

    async def _verify_factor(self, ip: str, *, code: UnlockCode | None = None, signature_valid: bool = False):
        totp = (self.state.settings.dashboard_totp_secret, code.code.get_secret_value()) if code else None
        result = await self.state.auth_store.verify_mobile_factor(
            ip=ip, now=time.time(), totp=totp, signature_valid=signature_valid,
        )
        if result in ("locked", "new_lock"):
            raise HTTPException(429, "验证失败次数过多，请稍后再试")
        if result != "verified":
            raise HTTPException(401, "验证码或设备验证失败")

    async def bind(self, ip: str, payload: DeviceBinding) -> dict:
        try:
            _public_key(payload.public_key)
        except (ValueError, UnsupportedAlgorithm):
            raise HTTPException(422, "设备公钥无效，请使用 P-256 公钥") from None
        await self._verify_factor(ip, code=payload)
        device_id = secrets.token_urlsafe(16)
        if not await self.repo.add_device(device_id, payload.public_key, payload.label, time.time()):
            raise HTTPException(409, "绑定设备已达三台上限")
        return {"device_id": device_id}

    async def revoke(self, session: str, device_id: str):
        self.require_unlock(session)
        if not await self.repo.revoke_device(device_id, time.time()):
            raise HTTPException(404, "设备不存在或已吊销")

    async def challenge(self, session: str, device_id: str) -> dict:
        if await self.repo.active_public_key(device_id) is None:
            raise HTTPException(404, "设备不存在或已吊销")
        now = time.time()
        self._challenges = {k: c for k, c in self._challenges.items() if c.expires_at > now}
        if len(self._challenges) >= 100:
            del self._challenges[next(iter(self._challenges))]
        challenge_id = secrets.token_urlsafe(16)
        nonce = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        self._challenges[challenge_id] = Challenge(session, device_id, nonce, now + 60)
        return {"challenge_id": challenge_id, "nonce": nonce, "expires_at": now + 60}

    def consume_challenge(self, challenge_id: str) -> Challenge | None:
        return self._challenges.pop(challenge_id, None)

    async def _device_proof_valid(self, session: str, proof: DeviceProof) -> bool:
        # Consume before any await, including failed, cross-session and locked-IP attempts.
        challenge = self.consume_challenge(proof.challenge_id)
        if not challenge or challenge.session_hash != session or challenge.device_id != proof.device_id:
            return False
        key = await self.repo.active_public_key(proof.device_id)
        return key is not None and challenge.expires_at > time.time() and _verify_signature(key, proof, challenge.nonce)

    async def unlock(self, session: str, ip: str, proof: UnlockCode | DeviceProof) -> dict:
        generation = self._unlock_generation
        if isinstance(proof, DeviceProof):
            valid = await self._device_proof_valid(session, proof)
            await self._verify_factor(ip, signature_valid=valid)
            method = "设备签名"
        else:
            await self._verify_factor(ip, code=proof)
            method = "验证码"
        minutes = await self.repo.unlock_minutes()
        async with self._switch_lock:
            if generation != self._unlock_generation or not await self.control_enabled():
                raise HTTPException(503, "手机改卡未启用")
            now = time.time()
            self._windows = {s: w for s, w in self._windows.items() if w.until > now}
            until = now + minutes * 60
            self._windows[session] = UnlockWindow(until, method)
        return {"unlocked_until": until}

    def _admit_attempt(self, sessions: dict[str, deque[float]], session: str, limit: int):
        now = time.time()
        cutoff = now - 60
        for expired in [s for s, times in sessions.items() if times[-1] <= cutoff]:
            del sessions[expired]
        attempts = sessions.setdefault(session, deque())
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if len(attempts) >= limit:
            raise HTTPException(429, f"每分钟最多操作{limit}次，请稍后再试")
        attempts.append(now)

    def _control_method(self, session: str, change: CardControlUpdate) -> str:
        if change.field == "disable_open" and change.expected is False and change.value is True:
            return "免解锁"
        return self.require_unlock(session)

    async def _record_change(self, name: str, change: CardControlUpdate, label: str, method: str):
        card_update_logger.info(
            "astro_card_update pair=%s field=%s before=%s after=%s auth=%s",
            name, change.field, change.expected, change.value, method,
        )
        on, off = ("运行", "暂停") if change.field == "status" else ("开", "关")
        transition = f"{off}→{on}" if change.value else f"{on}→{off}"
        title = f"手机修改：{name} {label} {transition}"
        body = f"验证方式：{method}"
        try:
            await self.state.mobile_alert_repo.record(
                kind="card_control", title=title, body=body, level="urgent", card_name=name,
            )
        except (sqlite3.Error, ValueError):  # aiosqlite raises ValueError for a closed connection.
            logger.error("手机改卡成功，提醒记录写入失败 card_id=%s", change.card_id)
            return title, body, False
        return title, body, True

    async def control(self, session: str, change: CardControlUpdate) -> dict:
        self._admit_attempt(self._attempts, session, 20)
        self._control_method(session, change)
        client, service = self.state.astro_client, self.state.astro_alert_service
        if service is None or not hasattr(service, "pair_lock"):
            raise HTTPException(503, "Astro 卡片锁未就绪")
        field, label = FIELDS[change.field]
        result = {"ok": True, "card_id": change.card_id, "field": change.field, "value": change.value}
        try:
            # The request has only the ID. Discover its lock name, then discard that snapshot.
            name = (await _latest_pair(client, change.card_id))["name"]
            async with service.pair_lock(name):
                pair = await _latest_pair(client, change.card_id)
                if not await self.control_enabled():
                    raise HTTPException(503, "手机改卡未启用")
                method = self._control_method(session, change)
                # Identity comparison also rejects absent and non-boolean upstream values.
                if pair["name"] != name or pair.get(field) is not change.expected:
                    raise HTTPException(409, CONFLICT)
                if change.expected is change.value:
                    return dict(result, changed=False)
                await client.update_pair(dict(pair, **{field: change.value}))
                title, body, recorded = await self._record_change(name, change, label, method)
        except AstroClientError as exc:
            logger.warning("Astro 拒绝修改（HTTP %s）: %s", exc.status_code, exc.message)
            raise HTTPException(exc.status_code, f"Astro 拒绝修改（HTTP {exc.status_code}）") from None

        # FCM may take seconds. Release the shared Astro write lock before sending.
        try:
            await self.state.mobile_push.send_urgent(
                kind="card_control", title=title, body=body, card_id=change.card_id, card_name=name,
            )
        except Exception:  # noqa: BLE001 - a notification failure must preserve the confirmed write.
            logger.warning("手机改卡成功，紧急推送失败 card_id=%s", change.card_id)
        if not recorded:
            raise HTTPException(503, "卡片已修改，提醒记录写入失败，请刷新")
        return dict(result, changed=True)
