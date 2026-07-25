"""認証のテスト。

データベースには接続できないため、repository をモックに差し替える。
検証対象は「認証ロジックが正しいか」であり、DB アクセス自体ではない。
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from app import config, security

PASSWORD = "correct-horse-battery-staple"
OTHER = "wrong-password"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "test-key-not-for-production")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@pytest.fixture
def admin_row():
    return {
        "user_id": 1, "office_id": 10, "email": "admin@example.jp",
        "password_hash": security.hash_password(PASSWORD),
        "role": "admin", "staff_id": None,
    }


@pytest.fixture
def client(env, monkeypatch, admin_row):
    from app import repository as repo
    monkeypatch.setattr(repo, "find_user_by_email",
                        lambda email: admin_row if email == admin_row["email"] else None)
    monkeypatch.setattr(repo, "touch_last_login", lambda uid: None)
    monkeypatch.setattr(repo, "update_password_hash", lambda uid, pw: None)

    security.throttle.reset(f"{admin_row['email']}|testclient")
    security.throttle._log.clear()

    from app import main
    importlib.reload(main)
    return TestClient(main.app, follow_redirects=False)


# --------------------------------------------------------------- ハッシュ
def test_同じパスワードでもハッシュ値は毎回異なる(env):
    a = security.hash_password(PASSWORD)
    b = security.hash_password(PASSWORD)
    assert a != b, "ソルトが効いていない"
    assert security.verify_password(a, PASSWORD)
    assert security.verify_password(b, PASSWORD)


def test_誤ったパスワードは検証に失敗する(env):
    h = security.hash_password(PASSWORD)
    assert not security.verify_password(h, OTHER)


def test_ハッシュ値にArgon2idの識別子が含まれる(env):
    assert security.hash_password(PASSWORD).startswith("$argon2id$")


def test_壊れたハッシュ値でも例外を投げず失敗を返す(env):
    assert not security.verify_password("not-a-hash", PASSWORD)
    assert not security.verify_password("", PASSWORD)


# --------------------------------------------------------------- セッション
def test_セッションは往復できる(env):
    u = security.CurrentUser(user_id=1, office_id=10,
                             email="a@example.jp", role="admin")
    back = security.read_session(security.issue_session(u))
    assert back == u


def test_改竄されたセッションは拒否される(env):
    u = security.CurrentUser(user_id=1, office_id=10,
                             email="a@example.jp", role="admin")
    token = security.issue_session(u)
    assert security.read_session(token[:-3] + "aaa") is None
    assert security.read_session("") is None
    assert security.read_session(None) is None


def test_別の鍵で署名されたセッションは拒否される(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "key-one")
    config.get_settings.cache_clear()
    token = security.issue_session(
        security.CurrentUser(user_id=1, office_id=10,
                             email="a@example.jp", role="admin"))

    monkeypatch.setenv("SECRET_KEY", "key-two")
    config.get_settings.cache_clear()
    assert security.read_session(token) is None
    config.get_settings.cache_clear()


def test_権限判定(env):
    admin = security.CurrentUser(1, 10, "a@example.jp", "admin")
    staff = security.CurrentUser(2, 10, "b@example.jp", "staff", staff_id=5)
    assert admin.is_admin
    assert not staff.is_admin


# --------------------------------------------------------------- ログイン経路
def test_ログイン画面が描画される(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "メールアドレス" in r.text


def test_正しい認証情報でログインできる(client):
    r = client.post("/login", data={"email": "admin@example.jp",
                                    "password": PASSWORD})
    assert r.status_code == 303
    assert r.headers["location"] == "/schedules"
    assert security.SESSION_COOKIE in r.cookies


def test_大文字のメールアドレスでもログインできる(client):
    r = client.post("/login", data={"email": "ADMIN@Example.JP",
                                    "password": PASSWORD})
    assert r.status_code == 303


def test_誤ったパスワードは401でCookieを発行しない(client):
    r = client.post("/login", data={"email": "admin@example.jp",
                                    "password": OTHER})
    assert r.status_code == 401
    assert security.SESSION_COOKIE not in r.cookies


def test_存在しない利用者と誤パスワードで応答を区別しない(client):
    """アカウントの存在を推測されないようにする。"""
    a = client.post("/login", data={"email": "admin@example.jp",
                                    "password": OTHER})
    b = client.post("/login", data={"email": "nobody@example.jp",
                                    "password": OTHER})
    assert a.status_code == b.status_code == 401
    msg = "メールアドレスまたはパスワードが正しくありません"
    assert msg in a.text
    assert msg in b.text


def test_試行回数の上限を超えると429を返す(client):
    for _ in range(security.throttle.max_attempts):
        client.post("/login", data={"email": "admin@example.jp",
                                    "password": OTHER})
    r = client.post("/login", data={"email": "admin@example.jp",
                                    "password": OTHER})
    assert r.status_code == 429
    assert "試行回数" in r.text


def test_成功すると試行回数がリセットされる(client):
    for _ in range(3):
        client.post("/login", data={"email": "admin@example.jp",
                                    "password": OTHER})
    assert client.post("/login", data={"email": "admin@example.jp",
                                       "password": PASSWORD}).status_code == 303
    # リセットされているので、また失敗を重ねられる
    assert client.post("/login", data={"email": "admin@example.jp",
                                       "password": OTHER}).status_code == 401


def test_ログアウトでCookieが破棄される(client):
    client.post("/login", data={"email": "admin@example.jp", "password": PASSWORD})
    r = client.post("/logout")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    assert 'careshift_session=""' in r.headers.get("set-cookie", "") \
        or "Max-Age=0" in r.headers.get("set-cookie", "")


# --------------------------------------------------------------- Cookie の属性
def test_Cookieの属性が安全側に設定される(env):
    p = security.cookie_params()
    assert p["httponly"] is True
    assert p["samesite"] == "strict"
    assert p["secure"] is False          # 開発環境では HTTP を許す


def test_本番ではSecure属性が付く(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "x" * 64)
    monkeypatch.setenv("DATABASE_URL", "postgresql+pg8000://u:p@h:5432/d")
    config.get_settings.cache_clear()
    assert security.cookie_params()["secure"] is True
    config.get_settings.cache_clear()
