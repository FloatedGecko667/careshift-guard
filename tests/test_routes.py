"""ルーティングと権限のテスト。

データベースへは接続できないため repository をモックに差し替える。
検証対象は「経路と権限とテンプレート描画が正しいか」である。
"""
from __future__ import annotations

import datetime as dt
import importlib

import pytest
from fastapi.testclient import TestClient

from app import config, security
from tests.test_service import OFFICE, PATTERNS, RULES, make_staff

PASSWORD = "correct-horse-battery-staple"
YEAR, MONTH = 2026, 8


def _user_row(role: str, staff_id: int | None = None):
    return {"user_id": 1 if role == "admin" else 2, "office_id": 1,
            "email": f"{role}@example.jp",
            "password_hash": security.hash_password(PASSWORD),
            "role": role, "staff_id": staff_id, "session_epoch": 1}


@pytest.fixture
def fake_db(monkeypatch):
    """データベースの代わりに素データを返す。"""
    from app import repository as repo

    state = {
        "entries": [],
        "violations": [],
        "requests": [],
        "schedule": None,
        "published": False,
        "saved": None,
    }

    monkeypatch.setattr(repo, "get_office", lambda oid: dict(OFFICE, office_id=oid))
    monkeypatch.setattr(repo, "list_staff", lambda oid, a, u: make_staff())
    monkeypatch.setattr(repo, "list_shift_patterns", lambda oid: PATTERNS)
    monkeypatch.setattr(repo, "list_staffing_rules", lambda st, a: RULES)
    monkeypatch.setattr(repo, "list_employment_types",
                        lambda oid: [{"employment_type_id": 1, "name": "常勤",
                                      "is_fulltime": True, "weekly_minutes": 2400}])
    monkeypatch.setattr(repo, "list_shift_requests",
                        lambda oid, f, t: state["requests"])
    monkeypatch.setattr(repo, "get_schedule",
                        lambda oid, m: state["schedule"])
    monkeypatch.setattr(repo, "list_entries", lambda sid: state["entries"])
    monkeypatch.setattr(repo, "list_violations", lambda sid: state["violations"])
    monkeypatch.setattr(repo, "upsert_schedule",
                        lambda oid, m, u: 7)
    monkeypatch.setattr(repo, "touch_last_login", lambda uid: None)
    monkeypatch.setattr(repo, "update_password_hash", lambda uid, pw: None)

    def save(oid, sid, entries, violations, st, obj, secs):
        state["saved"] = {"entries": entries, "violations": violations,
                          "status": st, "objective": obj}
        state["entries"] = entries
        state["violations"] = violations
        state["schedule"] = {
            "schedule_id": sid, "office_id": oid,
            "target_month": dt.date(YEAR, MONTH, 1), "avg_expected_users": 22.0,
            "status": "draft", "solver_status": st, "objective_value": obj,
            "solve_seconds": secs, "generated_at": dt.datetime.now(),
            "published_at": None}

    monkeypatch.setattr(repo, "save_solution", save)
    monkeypatch.setattr(repo, "update_entry",
                        lambda oid, sid, st, d, p: None)
    monkeypatch.setattr(repo, "insert_staff", lambda oid, **kw: 99)
    monkeypatch.setattr(repo, "retire_staff", lambda oid, sid, d: None)
    def upsert_request(office_id, staff_id, target_date, request_type,
                       shift_pattern_id=None, note=None):
        # 実装が読み戻すため、本物と同じ形の辞書で持つ
        state["requests"] = [r for r in state["requests"]
                             if not (r["staff_id"] == staff_id
                                     and r["target_date"] == target_date)]
        state["requests"].append({
            "staff_id": staff_id, "target_date": target_date,
            "request_type": request_type,
            "shift_pattern_id": shift_pattern_id, "note": note})

    def delete_request(office_id, staff_id, target_date):
        state["requests"] = [r for r in state["requests"]
                             if not (r["staff_id"] == staff_id
                                     and r["target_date"] == target_date)]

    monkeypatch.setattr(repo, "upsert_shift_request", upsert_request)
    monkeypatch.setattr(repo, "delete_shift_request", delete_request)

    def publish(oid, sid):
        if state["violations"]:
            return False
        state["published"] = True
        return True

    monkeypatch.setattr(repo, "publish_schedule", publish)

    # 監査ログ。書かれた内容をテストから確認できるよう溜める。
    state["audit"] = []
    monkeypatch.setattr(repo, "write_audit",
                        lambda **kw: state["audit"].append(kw))
    return state


def _client(monkeypatch, role: str, staff_id: int | None = None):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "test-key-not-for-production")
    # ここで検証するのは HTTP の挙動であり解の質ではない。
    # 求解上限を最小にして、テスト全体の実行時間を抑える。
    monkeypatch.setenv("SOLVER_TIME_LIMIT", "1")
    monkeypatch.setenv("SOLVER_WORKERS", "2")
    config.get_settings.cache_clear()

    from app import repository as repo
    row = _user_row(role, staff_id)
    monkeypatch.setattr(repo, "find_user_by_email", lambda e: row)
    # セッションの世代検証は DB を引く。整合する値を返す。
    monkeypatch.setattr(repo, "get_session_state", lambda uid: {
        "user_id": row["user_id"], "office_id": row["office_id"],
        "email": row["email"], "role": row["role"],
        "staff_id": row["staff_id"], "session_epoch": row["session_epoch"],
        "is_active": True} if uid == row["user_id"] else None)

    from app import main
    importlib.reload(main)
    c = TestClient(main.app, follow_redirects=False)
    c.post("/login", data={"email": row["email"], "password": PASSWORD})
    return c


@pytest.fixture
def admin(monkeypatch, fake_db):
    security.throttle._log.clear()
    yield _client(monkeypatch, "admin")
    config.get_settings.cache_clear()


@pytest.fixture
def staff(monkeypatch, fake_db):
    security.throttle._log.clear()
    yield _client(monkeypatch, "staff", staff_id=10)
    config.get_settings.cache_clear()


# --------------------------------------------------------------- 未ログイン
def test_未ログインは保護経路からログイン画面へ誘導される(monkeypatch, fake_db):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "test-key")
    config.get_settings.cache_clear()
    from app import main
    importlib.reload(main)
    c = TestClient(main.app, follow_redirects=False)
    for path in ("/schedules", "/masters", "/requests"):
        r = c.get(path)
        assert r.status_code == 303, path
        assert r.headers["location"] == "/login"
    config.get_settings.cache_clear()


def test_htmxからの要求はHXリダイレクトを返す(monkeypatch, fake_db):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "test-key")
    config.get_settings.cache_clear()
    from app import main
    importlib.reload(main)
    c = TestClient(main.app, follow_redirects=False)
    r = c.get("/schedules", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert r.headers["HX-Redirect"] == "/login"
    config.get_settings.cache_clear()


# --------------------------------------------------------------- 権限
def test_職員はマスタ管理にアクセスできない(staff):
    assert staff.get("/masters").status_code == 403


def test_職員はシフト生成を実行できない(staff):
    r = staff.post("/schedules/generate",
                   data={"year": YEAR, "month": MONTH, "avg_expected_users": 22})
    assert r.status_code == 403


def test_管理者はマスタ管理を開ける(admin):
    r = admin.get("/masters")
    assert r.status_code == 200
    assert "職員を追加する" in r.text
    assert "勤務区分" in r.text


# --------------------------------------------------------------- シフト生成
def test_未生成の月は生成を促す画面になる(admin):
    r = admin.get(f"/schedules?year={YEAR}&month={MONTH}")
    assert r.status_code == 200
    assert "まだ生成されていません" in r.text


def test_シフトを生成して表示できる(admin, fake_db):
    r = admin.post("/schedules/generate",
                   data={"year": YEAR, "month": MONTH,
                         "avg_expected_users": 22, "keep_manual": "true"})
    assert r.status_code == 303
    assert fake_db["saved"] is not None

    n_staff = len(make_staff())
    assert len(fake_db["saved"]["entries"]) == n_staff * 31

    r = admin.get(f"/schedules?year={YEAR}&month={MONTH}")
    assert r.status_code == 200
    assert "常勤換算" in r.text
    assert "シフトを再生成" in r.text
    # 職員名が表示されていること
    assert "鈴木 花子" in r.text


def test_平均利用者数が範囲外なら400(admin):
    r = admin.post("/schedules/generate",
                   data={"year": YEAR, "month": MONTH,
                         "avg_expected_users": 5000})
    assert r.status_code == 400


def test_対象年月が不正なら400(admin):
    r = admin.get("/schedules?year=1800&month=13")
    assert r.status_code == 400


# --------------------------------------------------------------- 確定
def test_違反があると確定できない(admin, fake_db):
    admin.post("/schedules/generate",
               data={"year": YEAR, "month": MONTH, "avg_expected_users": 22})
    fake_db["violations"] = [{
        "target_date": dt.date(YEAR, MONTH, 4), "job_type": "生活相談員",
        "kind": "fte", "required": 1.0, "actual": 0.5, "severity": "error"}]
    r = admin.post("/schedules/7/publish", data={"year": YEAR, "month": MONTH})
    assert r.status_code == 409
    assert not fake_db["published"]


def test_違反がなければ確定できる(admin, fake_db):
    admin.post("/schedules/generate",
               data={"year": YEAR, "month": MONTH, "avg_expected_users": 22})
    fake_db["violations"] = []
    r = admin.post("/schedules/7/publish", data={"year": YEAR, "month": MONTH})
    assert r.status_code == 303
    assert fake_db["published"]


# --------------------------------------------------------------- 帳票
def test_勤務形態一覧表をxlsxで取得できる(admin, fake_db):
    admin.post("/schedules/generate",
               data={"year": YEAR, "month": MONTH, "avg_expected_users": 22})
    r = admin.get(f"/schedules/7/export.xlsx?year={YEAR}&month={MONTH}")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert r.content[:2] == b"PK"          # xlsx は ZIP


# --------------------------------------------------------------- 希望シフト
def test_職員は自分の希望シフト画面を開ける(staff):
    r = staff.get(f"/requests?year={YEAR}&month={MONTH}")
    assert r.status_code == 200
    assert "希望シフト" in r.text


def test_職員が他人のstaff_idを渡しても自分の希望として扱われる(staff, fake_db):
    """引数を無条件に信用すると他人の希望を書き換えられる。"""
    r = staff.post("/requests", data={
        "target_date": f"{YEAR}-0{MONTH}-03", "request_type": "off",
        "staff_id": 9999})
    assert r.status_code == 303
    recorded = fake_db["requests"][-1]
    assert recorded["staff_id"] == 10, "職員自身の staff_id で記録されるべき"


def test_希望の種別が不正なら400(staff):
    r = staff.post("/requests", data={
        "target_date": f"{YEAR}-0{MONTH}-03", "request_type": "invalid"})
    assert r.status_code == 400


def test_区分希望に区分の指定がなければ400(staff):
    r = staff.post("/requests", data={
        "target_date": f"{YEAR}-0{MONTH}-03", "request_type": "pattern"})
    assert r.status_code == 400


def test_希望休に区分を指定したら400(staff):
    r = staff.post("/requests", data={
        "target_date": f"{YEAR}-0{MONTH}-03", "request_type": "off",
        "shift_pattern_id": 102})
    assert r.status_code == 400


def test_日付の形式が不正なら400(staff):
    r = staff.post("/requests", data={
        "target_date": "2026/08/03", "request_type": "off"})
    assert r.status_code == 400


# --------------------------------------------------------------- 段階的強化
def test_htmxなら1日分の断片だけを返す(staff):
    """該当行のみ差し替える。全画面を再描画しない。"""
    r = staff.post("/requests",
                   data={"target_date": f"{YEAR}-0{MONTH}-03",
                         "request_type": "off"},
                   headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert f'id="day-{YEAR}-0{MONTH}-03"' in r.text
    # 断片であること（レイアウト全体を含まない）
    assert "<!DOCTYPE html>" not in r.text
    assert "<header" not in r.text


def test_htmxがなくてもフォーム送信で完結する(staff):
    """htmx は任意依存。無い場合は通常の遷移になる。"""
    r = staff.post("/requests", data={"target_date": f"{YEAR}-0{MONTH}-03",
                                      "request_type": "off"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/requests?")


def test_希望シフト画面はhtmxなしでも操作要素が揃う(staff):
    """script が読み込まれない環境でもフォームが機能すること。"""
    r = staff.get(f"/requests?year={YEAR}&month={MONTH}")
    assert r.status_code == 200
    assert 'method="post"' in r.text
    assert 'action="/requests"' in r.text


# --------------------------------------------------------------- マスタ登録
def test_職員を追加できる(admin):
    r = admin.post("/masters/staff", data={
        "name": "新規 職員", "job_type": "care_worker",
        "employment_type_id": 1, "hired_on": "2026-09-01",
        "qualifications": "介護福祉士、実務者研修修了"})
    assert r.status_code == 303


def test_兼務先に主たる職種と同じものを指定したら400(admin):
    r = admin.post("/masters/staff", data={
        "name": "誤り", "job_type": "care_worker", "employment_type_id": 1,
        "hired_on": "2026-09-01", "secondary_job_type": "care_worker",
        "secondary_ratio": 0.5})
    assert r.status_code == 400


@pytest.mark.parametrize("ratio", [0, 1, 1.5, -0.2])
def test_兼務の従事割合が範囲外なら400(admin, ratio):
    r = admin.post("/masters/staff", data={
        "name": "誤り", "job_type": "care_worker", "employment_type_id": 1,
        "hired_on": "2026-09-01", "secondary_job_type": "counselor",
        "secondary_ratio": ratio})
    assert r.status_code == 400


def test_氏名が空なら400(admin):
    r = admin.post("/masters/staff", data={
        "name": "   ", "job_type": "care_worker", "employment_type_id": 1,
        "hired_on": "2026-09-01"})
    assert r.status_code == 400


def test_職種が不正なら400(admin):
    r = admin.post("/masters/staff", data={
        "name": "誤り", "job_type": "chef", "employment_type_id": 1,
        "hired_on": "2026-09-01"})
    assert r.status_code == 400
