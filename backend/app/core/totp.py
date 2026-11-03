"""RFC 6238 SHA-1 TOTP, with no third-party dependencies."""
import base64
import hashlib
import hmac
import re
import struct


def decode_secret(secret: str) -> bytes:
    try:
        normalized = secret.strip().upper().rstrip("=")
        key = base64.b32decode(normalized + "=" * (-len(normalized) % 8))
        if len(key) < 20:
            raise ValueError
        return key
    except Exception:
        raise ValueError("Invalid TOTP configuration") from None


def totp_at(secret: str, counter: int, digits: int = 6) -> str:
    digest = hmac.new(decode_secret(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff
    return str(number % (10 ** digits)).zfill(digits)


def matching_counter(secret: str, code: str, now: float, last_counter: int) -> int | None:
    if not re.fullmatch(r"[0-9]{6}", code):
        return None
    current = int(now // 30)
    matches = [counter for counter in range(max(0, current - 1), current + 2)
               if hmac.compare_digest(totp_at(secret, counter), code) and counter > last_counter]
    return max(matches) if matches else None
