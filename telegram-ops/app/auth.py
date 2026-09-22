import hashlib
import hmac
import ipaddress
import secrets
import time

from fastapi import Request

from app.config import get_settings


from app.passwords import _b64, _unb64, hash_password, verify_password


def configured_password_hash() -> str:
    from app.database import session_scope
    from app.relay_models import ConsoleState
    from sqlalchemy.exc import SQLAlchemyError
    try:
        with session_scope() as db:
            value = db.get(ConsoleState, "admin_password_hash")
            if value:
                return value.value
    except SQLAlchemyError:
        pass  # Before first database initialization.
    return get_settings().admin_password_hash


def signing_key() -> bytes:
    settings = get_settings()
    return (settings.app_secret_key + "|" + configured_password_hash()).encode("utf-8")


def admin_password_ok(password: str) -> bool:
    settings = get_settings()
    stored = configured_password_hash()
    if stored:
        return verify_password(password, stored)
    if not settings.admin_password:
        return False
    return secrets.compare_digest(password, settings.admin_password)


def create_session_token(username: str) -> str:
    settings = get_settings()
    expires_at = int(time.time() + settings.admin_session_hours * 3600)
    payload = f"{username}|{expires_at}"
    sig = hmac.new(signing_key(), payload.encode("utf-8"), hashlib.sha256).digest()
    return f"{_b64(payload.encode('utf-8'))}.{_b64(sig)}"


def verify_session_token(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    settings = get_settings()
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _unb64(payload_b64).decode("utf-8")
        expected_sig = hmac.new(signing_key(), payload.encode("utf-8"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64(expected_sig), sig_b64):
            return None
        username, expires_at = payload.rsplit("|", 1)
        if username != settings.admin_username or int(expires_at) < int(time.time()):
            return None
        return username
    except Exception:
        return None


def current_admin(request: Request) -> str | None:
    settings = get_settings()
    return verify_session_token(request.cookies.get(settings.admin_session_cookie))


def client_ip_allowed(client_ip: str | None) -> bool:
    settings = get_settings()
    rules = [item.strip() for item in settings.admin_allowed_ips.split(",") if item.strip()]
    if not rules:
        return True
    if not client_ip:
        return False
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for rule in rules:
        try:
            if "/" in rule:
                if ip in ipaddress.ip_network(rule, strict=False):
                    return True
            elif ip == ipaddress.ip_address(rule):
                return True
        except ValueError:
            continue
    return False
