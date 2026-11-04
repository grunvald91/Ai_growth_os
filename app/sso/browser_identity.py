"""Read the signed current-account cookie issued by BookingBot on servgo.ru.

Never trusts the user ID without verifying the ServGo HMAC. This proof only
binds a Studio SSO session to the current browser login; it does not confer
company access. The latter is checked against ServGo on each Studio request.
"""
import base64
import binascii
import hashlib
import hmac
import json
import time

COOKIE = "sg_identity"
MAX_AGE = 8 * 3600
PURPOSE = b"servgo-browser-identity-v1:"


def read_identity(settings, token: str | None, *, now: int | None = None) -> tuple[int, str] | None:
    secret = settings.servgo_sso_secret or ""
    if len(secret) < 32 or not token or len(token) > 1024 or token.count(".") != 1:
        return None
    try:
        body, signature = token.split(".")
        actual = base64.urlsafe_b64encode(
            hmac.new(secret.encode(), PURPOSE + body.encode(), hashlib.sha256).digest()
        ).decode().rstrip("=")
        if not hmac.compare_digest(signature, actual):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        current = int(time.time()) if now is None else now
        if (type(data.get("v")) is not int or data["v"] != 1 or
            type(data.get("u")) is not int or data["u"] <= 0 or
            type(data.get("e")) is not int or data["e"] < current or
            data["e"] > current + MAX_AGE or
            not isinstance(data.get("n"), str) or len(data["n"]) < 24):
            return None
        return data["u"], hashlib.sha256(token.encode()).hexdigest()
    except (ValueError, KeyError, TypeError, UnicodeError, binascii.Error, AttributeError):
        return None
