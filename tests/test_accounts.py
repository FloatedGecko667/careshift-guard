"""アカウント管理・パスワード再設定・監査ログのテスト。

データベースへは接続できないため repository をモックに差し替える。
検証対象は「権限の境界」「トークンの扱い」「監査に何が残るか」である。

このファイルで守りたい不変条件
  ・職員は管理画面に入れない
  ・有効な管理者を0人にできない
  ・トークンの平文は保存されない／単回使用／期限切れは通らない
  ・パスワード変更で他端末のセッションが切れる
  ・確定公開が監査ログに残る
"""
from __future__ import annotations

import datetime as dt
import importlib

import pytest
from fastapi.testclient import TestClient

from app import audit, config, security

PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "totally-new-secret-2026"  # noqa: S105  テスト用
OFFICE_ID = 1


def _user(role: str, user_id: int, email: str, *,
          staff_id: int | None = None, epoch: int = 1, active: bool = True,
          password: str | None = PASSWORD):
    return {"user_id": user_id, "office_id": OFFICE_ID, "email": email,
            # password=None は「まだ決まっていない」状態。
            # 本実装の insert_user と同じく、推測不能なハッシュを入れる。
            "password_hash": (security.hash_password(password) if password
                              else security.unusable_password_hash()),
            "role": role, "staff_id": staff_id, "session_epoch": epoch,
            "is_active": active, "last_login_at": None,
            "created_at": dt.datetime.now(), "staff_name": None,
            "job_type": None, "retired_on": None, "pending_reset_at": None}


@pytest.fixture
def db(monkeypatch):
    """アカウント関連の最小限の偽データベース。"""
    from app import repository as repo

    state: dict = {
        "users": [
            _user("admin", 1, "admin@example.jp"),
            _user("staff", 2, "suzuki@example.jp", staff_id=11),
        ],
        "linkable": [{"staff_id": 12, "name": "高橋 美咲", "job_type": "nurse"}],
        "tokens": {},        # 平文 -> レコード
        "audit": [],
        "passwords": {},     # user_id -> 設定されたパスワード
    }

    def by_id(uid):
        return next((u for u in state["users"] if u["user_id"] == uid), None)

    monkeypatch.setattr(repo, "list_users", lambda oid: list(state["users"]))
    monkeypatch.setattr(repo, "list_staff_without_user",
                        lambda oid: list(state["linkable"]))
    monkeypatch.setattr(repo, "get_office",
                        lambda oid: {"office_id": oid, "name": "テスト事業所"})
    monkeypatch.setattr(repo, "count_active_admins", lambda oid: sum(
        1 for u in state["users"] if u["role"] == "admin" and u["is_active"]))
    monkeypatch.setattr(repo, "touch_last_login", lambda uid: None)
    monkeypatch.setattr(repo, "update_password_hash", lambda uid, pw: None)
    monkeypatch.setattr(repo, "find_user_by_email", lambda e: next(
        (u for u in state["users"] if u["email"] == e and u["is_active"]), None))
    monkeypatch.setattr(repo, "get_session_state", lambda uid: by_id(uid))
    monkeypatch.setattr(repo, "write_audit",
                        lambda **kw: state["audit"].append(kw))
    def list_audit(oid, limit=200, action_prefix=""):
        # 本実装の SELECT と同じ形（列と並び）で返す。
        # 画面が参照する列が欠けていると、テストは通るのに実機で落ちる。
        rows = []
        for i, a in enumerate(reversed(state["audit"]), start=1):
            if not a["action"].startswith(action_prefix):
                continue
            rows.append({"audit_id": i, "actor_email": a["actor_email"],
                         "action": a["action"],
                         "target_type": a.get("target_type"),
                         "target_id": a.get("target_id"),
                         "summary": a["summary"], "ip": a.get("ip"),
                         "created_at": dt.datetime.now()})
        return rows[:limit]

    monkeypatch.setattr(repo, "list_audit", list_audit)
    monkeypatch.setattr(repo, "count_audit", lambda oid: len(state["audit"]))

    def insert_user(office_id, email, role, staff_id):
        uid = max(u["user_id"] for u in state["users"]) + 1
        state["users"].append(_user(role, uid, email, staff_id=staff_id,
                                    password=None))
        state["linkable"] = [s for s in state["linkable"]
                             if s["staff_id"] != staff_id]
        return uid

    def set_active(office_id, uid, is_active):
        u = by_id(uid)
        if u is None:
            return None
        u["is_active"] = is_active
        if not is_active:
            u["session_epoch"] += 1
        return {"email": u["email"], "is_active": is_active}

    def set_role(office_id, uid, role):
        u = by_id(uid)
        if u is None:
            return None
        u["role"] = role
        u["session_epoch"] += 1
        return {"email": u["email"], "role": role}

    def issue(office_id, uid, issued_by):
        if by_id(uid) is None:
            return None
        raw, digest = security.new_reset_token()
        expires = dt.datetime.now() + dt.timedelta(hours=24)
        # 既存の未使用分は失効させる（本実装と同じ振る舞い）
        for rec in state["tokens"].values():
            if rec["user_id"] == uid:
                rec["used"] = True
        state["tokens"][raw] = {"token_id": len(state["tokens"]) + 1,
                                "user_id": uid, "hash": digest,
                                "expires": expires, "used": False}
        return raw, expires

    def find_token(raw):
        rec = state["tokens"].get(raw)
        if rec is None or rec["used"] or rec["expires"] <= dt.datetime.now():
            return None
        u = by_id(rec["user_id"])
        if u is None or not u["is_active"]:
            return None
        return {"token_id": rec["token_id"], "user_id": u["user_id"],
                "office_id": u["office_id"], "email": u["email"],
                "role": u["role"], "staff_id": u["staff_id"]}

    def complete(token_id, user_id, plain):
        rec = next((r for r in state["tokens"].values()
                    if r["token_id"] == token_id), None)
        if rec is None or rec["used"]:
            return None
        rec["used"] = True
        u = by_id(user_id)
        u["session_epoch"] += 1
        u["password_hash"] = security.hash_password(plain)
        state["passwords"][user_id] = plain
        return u["session_epoch"]

    def set_password(user_id, plain):
        u = by_id(user_id)
        u["session_epoch"] += 1
        u["password_hash"] = security.hash_password(plain)
        state["passwords"][user_id] = plain
        return u["session_epoch"]

    monkeypatch.setattr(repo, "insert_user", insert_user)
    monkeypatch.setattr(repo, "set_user_active", set_active)
    monkeypatch.setattr(repo, "set_user_role", set_role)
    monkeypatch.setattr(repo, "issue_reset_token", issue)
    monkeypatch.setattr(repo, "find_valid_reset_token", find_token)
    monkeypatch.setattr(repo, "complete_password_reset", complete)
    monkeypatch.setattr(repo, "set_password", set_password)
    return state


def _client(monkeypatch, email: str) -> TestClient:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "test-key-not-for-production")
    config.get_settings.cache_clear()
    security.throttle._log.clear()
    from app import main
    importlib.reload(main)
    c = TestClient(main.app, follow_redirects=False)
    r = c.post("/login", data={"email": email, "password": PASSWORD})
    assert r.status_code == 303, "前提となるログインが失敗している"
    return c


@pytest.fixture
def admin(monkeypatch, db):
    yield _client(monkeypatch, "admin@example.jp")
    config.get_settings.cache_clear()


@pytest.fixture
def staff(monkeypatch, db):
    yield _client(monkeypatch, "suzuki@example.jp")
    config.get_settings.cache_clear()


@pytest.fixture
def anon(monkeypatch, db):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "test-key-not-for-production")
    config.get_settings.cache_clear()
    from app import main
    importlib.reload(main)
    yield TestClient(main.app, follow_redirects=False)
    config.get_settings.cache_clear()


# ------------------------------------------------------------------ 権限
def test_管理者はアカウント画面を開ける(admin):
    r = admin.get("/accounts")
    assert r.status_code == 200
    assert "アカウント管理" in r.text


def test_職員はアカウント画面に入れない(staff):
    assert staff.get("/accounts").status_code == 403


def test_職員は監査ログを見られない(staff):
    assert staff.get("/audit").status_code == 403


def test_未ログインではアカウント画面からログインへ誘導される(anon):
    r = anon.get("/accounts")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


# ------------------------------------------------------- アカウントの作成
def test_作成すると初回設定リンクが1度だけ表示される(admin, db):
    r = admin.post("/accounts", data={"email": "TAKAHASHI@Example.JP",
                                      "role": "staff", "staff_id": "12"})
    assert r.status_code == 200
    assert "初回設定リンク" in r.text
    # メールアドレスは小文字に正規化される（DB の CHECK に合わせる）
    assert any(u["email"] == "takahashi@example.jp" for u in db["users"])
    # 平文のトークンが画面に出ているが、保存はハッシュだけである
    raw = next(iter(db["tokens"]))
    assert raw in r.text
    assert db["tokens"][raw]["hash"] == security.hash_reset_token(raw)
    assert raw != db["tokens"][raw]["hash"]


def test_職員権限には職員の紐付けが必須(admin):
    r = admin.post("/accounts", data={"email": "x@example.jp",
                                      "role": "staff", "staff_id": ""})
    assert r.status_code == 400


def test_紐付け候補にない職員IDは拒否される(admin):
    r = admin.post("/accounts", data={"email": "x@example.jp",
                                      "role": "staff", "staff_id": "999"})
    assert r.status_code == 400


def test_重複するメールアドレスは拒否される(admin):
    r = admin.post("/accounts", data={"email": "admin@example.jp",
                                      "role": "admin", "staff_id": ""})
    assert r.status_code == 409


def test_作成しただけではログインできない(admin, db):
    admin.post("/accounts", data={"email": "new@example.jp",
                                  "role": "admin", "staff_id": ""})
    created = next(u for u in db["users"] if u["email"] == "new@example.jp")
    # 推測不能なハッシュが入っており、既知のパスワードでは通らない
    assert not security.verify_password(created["password_hash"], PASSWORD)
    assert not security.verify_password(created["password_hash"], "")


def test_使えないハッシュはどの平文とも一致しない():
    """insert_user が入れる初期値の性質。

    空文字や固定文字列を入れると、照合に成功する平文が存在しうる。
    毎回異なる乱数から作ることで、その可能性を消している。
    """
    a = security.unusable_password_hash()
    b = security.unusable_password_hash()
    assert a != b
    assert a.startswith("$argon2id$")
    for guess in ("", " ", "password", "admin", PASSWORD):
        assert not security.verify_password(a, guess)


# --------------------------------------------------------- 無効化と権限変更
def test_自分自身は無効化できない(admin):
    r = admin.post("/accounts/1/active", data={"is_active": "0"})
    assert r.status_code == 400


def test_最後の管理者は無効化できない(admin, db):
    # 管理者をもう1人作ってから、片方を無効化できることを確かめる
    db["users"].append(_user("admin", 9, "admin2@example.jp"))
    assert admin.post("/accounts/9/active",
                      data={"is_active": "0"}).status_code == 303
    # これで有効な管理者は1人。その1人を落とす操作は拒否される
    r = admin.post("/accounts/1/active", data={"is_active": "0"})
    assert r.status_code == 400


def test_無効化すると世代が進み既存セッションが切れる(admin, db, monkeypatch):
    db["users"].append(_user("admin", 9, "admin2@example.jp"))
    before = next(u for u in db["users"] if u["user_id"] == 9)["session_epoch"]
    admin.post("/accounts/9/active", data={"is_active": "0"})
    after = next(u for u in db["users"] if u["user_id"] == 9)["session_epoch"]
    assert after == before + 1


def test_職員に紐付かないアカウントは職員権限にできない(admin, db):
    db["users"].append(_user("admin", 9, "admin2@example.jp"))
    r = admin.post("/accounts/9/role", data={"role": "staff"})
    assert r.status_code == 400


def test_自分の権限は下げられない(admin):
    r = admin.post("/accounts/1/role", data={"role": "staff"})
    assert r.status_code == 400


# ----------------------------------------------------- パスワード再設定
def _issue_link(admin, db, user_id: int = 2) -> str:
    admin.post(f"/accounts/{user_id}/reset")
    return next(raw for raw, rec in db["tokens"].items()
                if rec["user_id"] == user_id and not rec["used"])


def test_再設定リンクでパスワードを設定できる(admin, db, anon):
    raw = _issue_link(admin, db)
    assert anon.get(f"/password/reset?token={raw}").status_code == 200
    r = anon.post("/password/reset", data={
        "token": raw, "password": NEW_PASSWORD,
        "password_confirm": NEW_PASSWORD})
    assert r.status_code == 303
    assert r.headers["location"] == "/login?reset=done"
    assert db["passwords"][2] == NEW_PASSWORD


def test_同じリンクは2回使えない(admin, db, anon):
    raw = _issue_link(admin, db)
    anon.post("/password/reset", data={"token": raw, "password": NEW_PASSWORD,
                                       "password_confirm": NEW_PASSWORD})
    r = anon.post("/password/reset", data={"token": raw,
                                           "password": "another-secret-1234",
                                           "password_confirm": "another-secret-1234"})
    assert r.status_code == 400


def test_期限切れのリンクは通らない(admin, db, anon):
    raw = _issue_link(admin, db)
    db["tokens"][raw]["expires"] = dt.datetime.now() - dt.timedelta(seconds=1)
    assert anon.get(f"/password/reset?token={raw}").status_code == 400


def test_再発行すると前のリンクは無効になる(admin, db, anon):
    old = _issue_link(admin, db)
    _issue_link(admin, db)
    assert anon.get(f"/password/reset?token={old}").status_code == 400


def test_存在しないトークンは通らない(anon):
    assert anon.get("/password/reset?token=deadbeef").status_code == 400


def test_確認用が一致しないと設定できない(admin, db, anon):
    raw = _issue_link(admin, db)
    r = anon.post("/password/reset", data={
        "token": raw, "password": NEW_PASSWORD,
        "password_confirm": NEW_PASSWORD + "x"})
    assert r.status_code == 400
    assert 2 not in db["passwords"]


def test_短いパスワードは拒否される(admin, db, anon):
    raw = _issue_link(admin, db)
    r = anon.post("/password/reset", data={
        "token": raw, "password": "short1234", "password_confirm": "short1234"})
    assert r.status_code == 400


def test_設定後に自動ログインしない(admin, db, anon):
    raw = _issue_link(admin, db)
    r = anon.post("/password/reset", data={
        "token": raw, "password": NEW_PASSWORD,
        "password_confirm": NEW_PASSWORD})
    # セッションを発行していないこと
    assert "careshift_session=" not in r.headers.get("set-cookie", "").replace(
        'careshift_session=""', "")


# ------------------------------------------------ 自分でのパスワード変更
def test_現在のパスワードが必要(admin):
    r = admin.post("/password/change", data={
        "current_password": "wrong-password-value",
        "password": NEW_PASSWORD, "password_confirm": NEW_PASSWORD})
    assert r.status_code == 400


def test_変更すると世代が進み操作中の端末だけ継続する(admin, db):
    before = next(u for u in db["users"] if u["user_id"] == 1)["session_epoch"]
    r = admin.post("/password/change", data={
        "current_password": PASSWORD,
        "password": NEW_PASSWORD, "password_confirm": NEW_PASSWORD})
    assert r.status_code == 303
    after = next(u for u in db["users"] if u["user_id"] == 1)["session_epoch"]
    assert after == before + 1
    # 新しい世代の Cookie が発行されている
    assert "careshift_session=" in r.headers.get("set-cookie", "")
    # そのまま操作を続けられる
    assert admin.get("/accounts").status_code == 200


def test_メールアドレスの一部を含むパスワードは拒否される(admin):
    r = admin.post("/password/change", data={
        "current_password": PASSWORD,
        "password": "admin-no-password", "password_confirm": "admin-no-password"})
    assert r.status_code == 400


# ------------------------------------------------------------ 監査ログ
def test_ログインが監査に残る(admin, db):
    actions = [a["action"] for a in db["audit"]]
    assert audit.LOGIN_SUCCESS in actions


def test_アカウント作成と発行が監査に残る(admin, db):
    admin.post("/accounts", data={"email": "new@example.jp",
                                  "role": "admin", "staff_id": ""})
    actions = [a["action"] for a in db["audit"]]
    assert audit.ACCOUNT_CREATE in actions
    assert audit.PASSWORD_RESET_ISSUED in actions


def test_監査ログの行為名は対象と動作の形式である():
    for name, label in audit.LABELS.items():
        assert name.count(".") == 1, f"{name} の形式が違う"
        target, action = name.split(".")
        assert target and action
        assert label, f"{name} に日本語表記がない"


def test_監査ログ画面に記録が出る(admin, db):
    r = admin.get("/audit")
    assert r.status_code == 200
    assert "監査ログ" in r.text
    assert "ログイン" in r.text


def test_監査ログは分類で絞り込める(admin, db):
    admin.post("/accounts", data={"email": "new@example.jp",
                                  "role": "admin", "staff_id": ""})
    r = admin.get("/audit?group=account")
    assert r.status_code == 200
    assert "アカウントの作成" in r.text
    # ログインの記録は含まれない
    assert "login.success" not in r.text


def test_不正な分類は無視して全件表示にする(admin):
    assert admin.get("/audit?group=../etc/passwd").status_code == 200


def test_表示件数は上限で丸める(admin):
    assert admin.get("/audit?limit=999999").status_code == 200
    assert admin.get("/audit?limit=0").status_code == 200


# ------------------------------------------------------- パスワード方針
@pytest.mark.parametrize("bad", [
    "short",                     # 短い
    "a" * 200,                   # 長すぎる
    "password12345",             # 推測されやすい
    "CareShiftGuard2026",        # 製品名
    "aaaaaaaaaaaaaaaa",          # 文字種が少ない
])
def test_使えないパスワードを弾く(bad):
    assert security.password_problem(bad) is not None


@pytest.mark.parametrize("good", [
    "correct-horse-battery-staple",
    "きょうは晴れだから洗濯をする",
    "Tsukiga-Kirei-desune-2026",
])
def test_十分な長さのパスワードは通る(good):
    assert security.password_problem(good) is None


def test_全角と半角を同じ扱いにする():
    assert security.normalize_password("ａｂｃ１２３") == "abc123"


def test_トークンは毎回異なりハッシュは一致する():
    a_raw, a_hash = security.new_reset_token()
    b_raw, b_hash = security.new_reset_token()
    assert a_raw != b_raw
    assert a_hash != b_hash
    assert security.hash_reset_token(a_raw) == a_hash
    assert len(a_hash) == 64
    assert all(c in "0123456789abcdef" for c in a_hash)
