"""レスポンシブ対応のテスト。

見た目そのものは自動では検証できない。
ここで守るのは「狭い画面で操作できなくなる作り」に戻らないことである。

守りたい不変条件
  ・viewport の指定がある（無いと拡大縮小した固定幅で表示される）
  ・狭い画面用の断点と、下端の固定タブが存在する
  ・操作要素の最小の高さが指定されている
  ・入力欄の文字が狭い画面で16px以上（iOS Safari の自動拡大を防ぐ）
  ・31日×職員数の表は狭い画面では出さず、縦の一覧に切り替える
  ・職員は自分のシフトだけを縦一覧で見られる
  ・希望休は日付を1回押すだけで付き、もう1回で外れる
  ・一覧の表はカードに組み替えるための data-label を持つ
"""
from __future__ import annotations

import datetime as dt
import importlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, security
from tests.test_service import OFFICE, PATTERNS, RULES, make_staff

PASSWORD = "correct-horse-battery-staple"
YEAR, MONTH = 2026, 8
TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"

# 断点。ここを変えるなら base.html と揃えること。
BREAKPOINT = 719


def _read(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------- 共通の土台
def test_viewportが指定されている():
    """無いと端末が固定幅で描画し、文字が読めない大きさに縮む。"""
    html = _read("base.html")
    assert 'name="viewport"' in html
    assert "width=device-width" in html
    assert "initial-scale=1" in html


def test_狭い画面用の断点がある():
    html = _read("base.html")
    assert f"@media (max-width:{BREAKPOINT}px)" in html


def test_下端の固定タブがある():
    """上端のナビゲーションは片手では親指が届かない。"""
    html = _read("base.html")
    assert "nav.tabbar" in html
    assert "position:fixed" in html
    assert "bottom:0" in html


def test_タブはアイコンと文字を併記する():
    """アイコンだけでは意味が伝わらず、押す前に迷う。"""
    html = _read("base.html")
    tabbar = html[html.index('<nav class="tabbar"'):]
    for label in ("シフト", "希望を出す", "設定"):
        assert label in tabbar, f"タブに「{label}」の文字がない"
    assert tabbar.count("<svg") >= 3, "アイコンが足りない"


def test_操作要素の最小の高さが指定されている():
    html = _read("base.html")
    assert "--tap:44px" in html
    assert "min-height:var(--tap)" in html


def test_入力欄は狭い画面で16px以上にする():
    """16px を下回ると iOS Safari が焦点時に勝手に拡大する。"""
    html = _read("base.html")
    narrow = html[html.index(f"@media (max-width:{BREAKPOINT}px)"):]
    m = re.search(r"input,select,textarea\{font-size:(\d+)px", narrow)
    assert m, "狭い画面で入力欄の文字寸法を指定していない"
    assert int(m.group(1)) >= 16


def test_固定タブに隠れない余白を確保している():
    html = _read("base.html")
    assert "padding-bottom:calc(var(--nav-h)" in html
    assert "safe-area-inset-bottom" in html, "iPhone の下端の余白を考慮していない"


# ----------------------------------------------------------- 表のカード化
@pytest.mark.parametrize("name", ["accounts.html", "audit.html", "masters.html"])
def test_一覧の表はカードに組み替えられる(name):
    """横スクロールする表は片手で読めない。"""
    html = _read(name)
    assert 'class="list cards"' in html, f"{name} の表に cards がない"
    assert "data-label" in html, f"{name} のセルに data-label がない"


def test_カード化の見出しはdata_labelから出す():
    html = _read("base.html")
    assert "content:attr(data-label)" in html


def test_表の見出し数とdata_labelの種類が対応する():
    """列を増やしたのに data-label を付け忘れることを防ぐ。"""
    html = _read("audit.html")
    heads = re.findall(r"<th[^>]*>([^<]+)</th>", html)
    labels = set(re.findall(r'data-label="([^"]+)"', html))
    for h in heads:
        assert h.strip() in labels, f"列「{h.strip()}」に data-label がない"


# ------------------------------------------------------- 画面ごとの切り替え
def test_シフト表の密なグリッドは広い画面だけに出す():
    html = _read("schedule.html")
    assert 'class="scroll only-wide"' in html
    assert 'class="only-narrow"' in html


def test_希望シフトは1タップで登録と取消ができる():
    """登録済みなら取消経路へ、未登録なら希望休の登録経路へ送る。"""
    html = _read("partials/request_row.html")
    assert "/requests/delete' if d.request else '/requests'" in html
    assert 'name="request_type" value="off"' in html
    # 押す領域はセル全体
    assert "<button type=\"submit\"" in html


def test_休業日は押せないようにする():
    html = _read("partials/request_row.html")
    assert "{% if d.closed %}disabled{% endif %}" in html


def test_状態は色だけでなく文字でも示す():
    """色覚特性のある職員に伝わらないため、色だけに頼らない。"""
    html = _read("partials/request_row.html")
    for word in ("希望休", "勤務不可", "区分希望", "休業"):
        assert word in html


def test_希望シフトのカレンダーは7列である():
    html = _read("requests.html")
    assert "grid-template-columns:repeat(7,1fr)" in html
    # 月曜始まり
    assert "['月','火','水','木','金','土','日']" in html


# --------------------------------------------------- 実際の応答を確認する
def _user_row(role: str, staff_id: int | None = None):
    return {"user_id": 1 if role == "admin" else 2, "office_id": 1,
            "email": f"{role}@example.jp",
            "password_hash": security.hash_password(PASSWORD),
            "role": role, "staff_id": staff_id, "session_epoch": 1}


@pytest.fixture
def client(monkeypatch):
    """職員としてログインした状態の TestClient を返す関数。"""
    from app import repository as repo

    staff = make_staff()
    state: dict = {"entries": [], "violations": [], "schedule": None}

    monkeypatch.setattr(repo, "get_office", lambda oid: dict(OFFICE, office_id=oid))
    monkeypatch.setattr(repo, "list_staff", lambda oid, a, u: staff)
    monkeypatch.setattr(repo, "list_shift_patterns", lambda oid: PATTERNS)
    monkeypatch.setattr(repo, "list_staffing_rules", lambda st, a: RULES)
    monkeypatch.setattr(repo, "list_shift_requests", lambda oid, f, t: [])
    monkeypatch.setattr(repo, "list_entries", lambda sid: state["entries"])
    monkeypatch.setattr(repo, "list_violations", lambda sid: state["violations"])
    monkeypatch.setattr(repo, "get_schedule", lambda oid, m: state["schedule"])
    monkeypatch.setattr(repo, "upsert_schedule", lambda oid, m, u: 7)
    monkeypatch.setattr(repo, "touch_last_login", lambda uid: None)
    monkeypatch.setattr(repo, "update_password_hash", lambda uid, pw: None)
    monkeypatch.setattr(repo, "write_audit", lambda **kw: None)

    def save(oid, sid, entries, violations, st, obj, secs):
        state["entries"] = entries
        state["violations"] = violations
        state["schedule"] = {
            "schedule_id": sid, "office_id": oid,
            "target_month": dt.date(YEAR, MONTH, 1), "avg_expected_users": 22.0,
            "status": "draft", "solver_status": st, "objective_value": obj,
            "solve_seconds": secs, "generated_at": dt.datetime.now(),
            "published_at": None}

    monkeypatch.setattr(repo, "save_solution", save)

    def make(role: str, staff_id: int | None = None) -> TestClient:
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("SECRET_KEY", "test-key-not-for-production")
        monkeypatch.setenv("SOLVER_TIME_LIMIT", "1")
        config.get_settings.cache_clear()
        row = _user_row(role, staff_id)
        monkeypatch.setattr(repo, "find_user_by_email", lambda e: row)
        monkeypatch.setattr(repo, "get_session_state", lambda uid: {
            "user_id": row["user_id"], "office_id": row["office_id"],
            "email": row["email"], "role": row["role"],
            "staff_id": row["staff_id"], "session_epoch": 1,
            "is_active": True} if uid == row["user_id"] else None)
        security.throttle._log.clear()
        from app import main
        importlib.reload(main)
        c = TestClient(main.app, follow_redirects=False)
        c.post("/login", data={"email": row["email"], "password": PASSWORD})
        return c

    yield make
    config.get_settings.cache_clear()


def test_職員には確定前のシフトを見せない(client):
    """下書きの段階で配ると、後から変わったときに現場が混乱する。"""
    admin = client("admin")
    r = admin.post("/schedules/generate", data={
        "year": YEAR, "month": MONTH, "avg_expected_users": "22"})
    assert r.status_code == 303

    staff = client("staff", staff_id=1)
    page = staff.get(f"/schedules?year={YEAR}&month={MONTH}")
    assert page.status_code == 200
    assert "まだ確定していません" in page.text
    assert 'class="mylist"' not in page.text


def test_確定後は職員に自分のシフトの一覧が出る(client, monkeypatch):
    admin = client("admin")
    admin.post("/schedules/generate", data={
        "year": YEAR, "month": MONTH, "avg_expected_users": "22"})

    # 確定済みの状態にする
    from app import repository as repo
    original = repo.get_schedule
    monkeypatch.setattr(repo, "get_schedule",
                        lambda oid, m: {**original(oid, m), "status": "published"})

    staff = client("staff", staff_id=1)
    page = staff.get(f"/schedules?year={YEAR}&month={MONTH}")
    assert page.status_code == 200
    assert "さんのシフト" in page.text
    assert 'class="mylist"' in page.text


def test_職員には求解の状態と違反件数を見せない(client, monkeypatch):
    """対処できるのは管理者だけであり、職員に見せても不安を与えるだけ。"""
    admin = client("admin")
    admin.post("/schedules/generate", data={
        "year": YEAR, "month": MONTH, "avg_expected_users": "22"})
    from app import repository as repo
    original = repo.get_schedule
    monkeypatch.setattr(repo, "get_schedule",
                        lambda oid, m: {**original(oid, m), "status": "published"})

    staff = client("staff", staff_id=1)
    page = staff.get(f"/schedules?year={YEAR}&month={MONTH}").text
    assert "求解" not in page
    assert "目的関数" not in page
    assert "人員基準欠如減算" not in page

    admin2 = client("admin")
    page = admin2.get(f"/schedules?year={YEAR}&month={MONTH}").text
    assert "求解" in page


def test_職員に紐づかない管理者には日別サマリーが出る(client):
    admin = client("admin")
    admin.post("/schedules/generate", data={
        "year": YEAR, "month": MONTH, "avg_expected_users": "22"})
    page = admin.get(f"/schedules?year={YEAR}&month={MONTH}")
    assert page.status_code == 200
    assert 'class="daylist"' in page.text
    assert "日別の充足状況" in page.text


def test_タブは職員には3つ管理者には4つ出す(client):
    staff = client("staff", staff_id=1)
    body = staff.get("/requests").text
    tabbar = body[body.index('<nav class="tabbar"'):]
    assert tabbar.count("<a href=") == 3, "職員に不要なタブが出ている"
    assert "/masters" not in tabbar

    admin = client("admin")
    body = admin.get("/requests").text
    tabbar = body[body.index('<nav class="tabbar"'):]
    assert tabbar.count("<a href=") == 4
    assert "/masters" in tabbar


def test_現在の画面がタブで示される(client):
    staff = client("staff", staff_id=1)
    body = staff.get("/requests").text
    tabbar = body[body.index('<nav class="tabbar"'):]
    assert 'href="/requests" aria-current="page"' in tabbar


def test_未ログインでは固定タブを出さない(client):
    staff = client("staff", staff_id=1)
    staff.cookies.clear()
    body = staff.get("/login").text
    assert '<nav class="tabbar"' not in body
