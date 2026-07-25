"""認証まわり。パスワードハッシュとセッション。

方針
  ・パスワードは Argon2id でハッシュ化する。平文は一切保存しない。
  ・セッションは署名付き Cookie に持つ。サーバ側にセッションストアを
    置かないため、web コンテナを増やしても共有ストアが不要になる。
  ・Cookie は HttpOnly / SameSite=Strict。本番では Secure も付ける。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

SESSION_COOKIE = "careshift_session"
# 1勤務帯を想定した有効期限
SESSION_MAX_AGE = 12 * 60 * 60

_hasher = PasswordHasher()


# ------------------------------------------------------------------ パスワード
def hash_password(plain: str) -> str:
    """Argon2id のハッシュ値を返す。"""
    return _hasher.hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    try:
        _hasher.verify(stored_hash, plain)
        return True
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """Argon2 のパラメータを引き上げた際、次回ログイン時に再ハッシュするか。"""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except (InvalidHashError, ValueError):
        return False


# ------------------------------------------------------------------ セッション
@dataclass(frozen=True)
class CurrentUser:
    user_id: int
    office_id: int
    email: str
    role: str
    staff_id: int | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="careshift-session")


def issue_session(user: CurrentUser) -> str:
    return _serializer().dumps({
        "uid": user.user_id, "oid": user.office_id,
        "email": user.email, "role": user.role, "sid": user.staff_id,
    })


def read_session(token: str | None) -> CurrentUser | None:
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    try:
        return CurrentUser(
            user_id=int(data["uid"]), office_id=int(data["oid"]),
            email=str(data["email"]), role=str(data["role"]),
            staff_id=int(data["sid"]) if data.get("sid") is not None else None)
    except (KeyError, TypeError, ValueError):
        return None


def cookie_params() -> dict[str, object]:
    """set_cookie に渡す共通パラメータ。"""
    return {
        "key": SESSION_COOKIE,
        "httponly": True,
        "samesite": "strict",
        "secure": get_settings().is_production,
        "max_age": SESSION_MAX_AGE,
        "path": "/",
    }


# ------------------------------------------------------------------ 総当たり対策
class LoginThrottle:
    """ログイン試行の回数制限。

    プロセス内のメモリに持つ簡易実装である。
    web コンテナを複数に増やした段階では共有ストア（Valkey 等）へ
    移す必要がある。初版は1コンテナ構成のため、これで足りる。
    """

    def __init__(self, max_attempts: int = 10, window: int = 300) -> None:
        self.max_attempts = max_attempts
        self.window = window
        self._log: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        return [t for t in self._log.get(key, []) if now - t < self.window]

    def is_blocked(self, key: str) -> bool:
        now = time.monotonic()
        recent = self._recent(key, now)
        self._log[key] = recent
        return len(recent) >= self.max_attempts

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        self._log[key] = self._recent(key, now) + [now]

    def reset(self, key: str) -> None:
        self._log.pop(key, None)


throttle = LoginThrottle()
