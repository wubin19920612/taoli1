"""Add-only arb-mvp client. Signing and transport share the exact same bytes."""
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

import httpx

UNKNOWN_RESULT = "结果未知，请到 arb-mvp 界面确认是否已建卡，不要直接重试"


@dataclass(frozen=True)
class ArbMvpResult:
    success: bool
    message: str
    pair_name: str | None = None


class ArbMvpClient:
    def __init__(self, url: str, api_key: str, *, transport=None):
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._transport = transport

    async def add_pair(self, pair: dict) -> ArbMvpResult:
        # Enforce the money boundary again at the only outbound write point.
        pair = {**pair, "disableOpen": True}
        body = json.dumps({"action": "add", "pair": pair}, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
        timestamp = str(int(time.time() * 1000))
        nonce = secrets.token_hex(12)
        signed = f"{timestamp}\n{nonce}\nPOST\n/api/pairs\n".encode() + body
        signature = hmac.new(self._api_key.encode(), signed, hashlib.sha256).hexdigest()
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False,
                                         transport=self._transport, trust_env=False) as client:
                response = await client.post(self._url + "/api/pairs", content=body, headers={
                    "content-type": "application/json", "x-timestamp": timestamp,
                    "x-nonce": nonce, "x-sign": signature,
                })
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return ArbMvpResult(False, UNKNOWN_RESULT)
        if not isinstance(data, dict):
            return ArbMvpResult(False, UNKNOWN_RESULT)
        if response.status_code == 200 and data.get("code") == 0:
            config = data.get("data")
            config = config.get("config") if isinstance(config, dict) else None
            name = config.get("name") if isinstance(config, dict) else None
            if not isinstance(name, str) or name != pair.get("name"):
                return ArbMvpResult(False, UNKNOWN_RESULT)
            return ArbMvpResult(True, "卡片已上架，状态 STOPPED，禁止开仓", name)
        # Never reflect untrusted response text (it may echo credentials/headers).
        if response.status_code == 409:
            limited = "LIMIT_EXCEEDED" in json.dumps(data)
            return ArbMvpResult(False, "卡片数量已达上限（LIMIT_EXCEEDED）" if limited
                                else "卡片重名（DUPLICATE_NAME），未自动改名或重试")
        messages = {400: "arb-mvp 卡片配置错误", 401: "arb-mvp 签名验证失败",
                    429: "arb-mvp 请求过于频繁，请稍后再试"}
        return ArbMvpResult(False, messages.get(response.status_code, UNKNOWN_RESULT))
