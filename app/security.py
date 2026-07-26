"""認証まわり。パスワードハッシュとセッション。

方針
  ・パスワードは Argon2id でハッシュ化する。平文は一切保存しない。
  ・セッションは署名付き Cookie に持つ。サーバ側にセッションストアを
    置かないため、web コンテナを増やしても共有ストアが不要になる。
  ・Cookie は HttpOnly / SameSite=Strict。本番では Secure も付ける。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import unicodedata
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

SESSION_COOKIE = "careshift_session"
# 1勤務帯を想定した有効期限
SESSION_MAX_AGE = 12 * 60 * 60

# パスワード再設定リンクの有効期間。
# 短すぎると管理者が伝える前に切れ、長すぎると漏えい時の被害が伸びる。
# 職員へ口頭やチャットで伝える運用を想定して24時間とする。
RESET_TOKEN_TTL = 24 * 60 * 60

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


def unusable_password_hash() -> str:
    """ログインに使えないハッシュを作る。

    アカウントを作った時点ではパスワードが決まっていない。
    password_hash は NOT NULL なので、推測不能な値を入れておき、
    初回設定リンクを使うまでログインできない状態にする。

    空文字や固定文字列を入れてはいけない。
    照合に成功する平文が存在してしまう可能性を残すべきではない。
    """
    return hash_password(secrets.token_urlsafe(32))


# ------------------------------------------------------------ パスワード方針
# NIST SP 800-63B に沿う。
#   ・長さを主要な強度とする（12文字以上）
#   ・記号や大文字の混在を強制しない。覚えられない文字列を強いると
#     付箋に書かれ、かえって弱くなる
#   ・推測されやすい値だけを拒否する
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128

# 介護業界の事業所名や本製品名を含む、この文脈で狙われやすい値。
# 網羅は不可能なので、明らかなものだけを止める。
_WEAK_SUBSTRINGS = (
    "password", "passw0rd", "careshift", "kaigo", "care1234",
    "123456", "qwerty", "abc123", "admin",
)


def normalize_password(plain: str) -> str:
    """比較と保存の前に正規化する。

    日本語入力では全角と半角が混ざる。NFKC で寄せておかないと
    「設定したのにログインできない」が起きる。
    前後の空白は落とさない（NIST は空白を含む入力を許容すべきとする）。
    """
    return unicodedata.normalize("NFKC", plain)


def password_problem(plain: str, email: str = "") -> str | None:
    """使えないパスワードなら理由を返す。問題なければ None。"""
    p = normalize_password(plain)
    if len(p) < PASSWORD_MIN_LENGTH:
        return f"パスワードは{PASSWORD_MIN_LENGTH}文字以上にしてください。"
    if len(p) > PASSWORD_MAX_LENGTH:
        return f"パスワードは{PASSWORD_MAX_LENGTH}文字以内にしてください。"
    low = p.lower()
    if any(w in low for w in _WEAK_SUBSTRINGS):
        return "推測されやすい語が含まれています。別の文字列にしてください。"
    local = email.split("@")[0].lower()
    if local and len(local) >= 3 and local in low:
        return "メールアドレスの一部を含めないでください。"
    if len(set(p)) <= 3:
        return "同じ文字の繰り返しは使えません。"
    return None


# ------------------------------------------------------- 再設定用トークン
def new_reset_token() -> tuple[str, str]:
    """(平文, SHA-256の16進) を返す。

    平文は発行直後に一度だけ画面へ出し、保存しない。
    データベースにはハッシュのみを置く。漏えいしても、
    そこから有効なリンクは作れない。

    ハッシュに Argon2 を使わない理由は、トークンが32バイトの
    乱数であり、辞書攻撃の対象にならないためである。
    照合のたびに数十ミリ秒かける必要がない。
    """
    raw = secrets.token_urlsafe(32)
    return raw, hash_reset_token(raw)


def hash_reset_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    """比較の所要時間から内容を推測されないようにする。"""
    return hmac.compare_digest(a, b)


# ------------------------------------------------------------------ セッション
@dataclass(frozen=True)
class CurrentUser:
    user_id: int
    office_id: int
    email: str
    role: str
    staff_id: int | None = None
    # セッションの世代。DB 側の users.session_epoch と一致しなければ失効。
    session_epoch: int = 1

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="careshift-session")


def issue_session(user: CurrentUser) -> str:
    return _serializer().dumps({
        "uid": user.user_id, "oid": user.office_id,
        "email": user.email, "role": user.role, "sid": user.staff_id,
        "ep": user.session_epoch,
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
            staff_id=int(data["sid"]) if data.get("sid") is not None else None,
            # 0002 より前に発行された Cookie には ep が無い。
            # 既定を1にすると、移行直後の利用者を無用に締め出さずに済む。
            session_epoch=int(data.get("ep", 1)))
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
