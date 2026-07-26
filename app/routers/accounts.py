"""アカウント管理と監査ログ。管理者のみ。

方針
  ・利用者の行は削除しない。無効化で運用する。
    監査ログが actor_user_id で参照しており、消すと辿れなくなる。
  ・管理者は他人のパスワードを設定しない。
    初回設定・再設定はワンタイムリンクを発行し、本人が決める。
    管理者が決めて口頭やチャットで伝えると、伝達経路に平文が残る。
  ・有効な管理者を0人にする操作は拒否する。
    誰もアカウントを直せない状態になり、SQL 作業が必要になる。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import audit
from app import repository as repo
from app.config import get_settings
from app.deps import AdminDep
from app.jobs import JOB_LABEL
from app.templating import render

router = APIRouter(tags=["accounts"])

ROLES = {"admin": "管理者", "staff": "職員"}
# 監査ログの表示件数。既定は直近200件。
AUDIT_LIMIT = 200
AUDIT_LIMIT_MAX = 1000


def _reset_url(request: Request, raw_token: str) -> str:
    """再設定リンクの絶対URLを組む。

    request.url_for に任せてはいけない。
    アプリが見ているホストは逆プロキシが渡した Host であり、
    利用者がブラウザで見ているものとは一致しないことがある。
    実際に nginx が `Host $host` を渡していたためポートが落ち、
    http://localhost/password/reset という使えないリンクになった。
    ロードバランサで TLS を終端する本番では scheme もずれる。

    そのため PUBLIC_BASE_URL を正とし、未設定のとき（開発）だけ
    要求から組む。本番では config が未設定を起動時に拒否する。
    """
    base = get_settings().public_base_url
    if not base:
        # X-Forwarded-Proto と Host から組む。開発用の推測経路。
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        host = request.headers.get("host") or request.url.netloc
        base = f"{proto}://{host}"
    return f"{base}/password/reset?token={raw_token}"


def _accounts_context(user: AdminDep, **extra: object) -> dict[str, object]:
    return {
        "office": repo.get_office(user.office_id),
        "users": repo.list_users(user.office_id),
        "linkable_staff": repo.list_staff_without_user(user.office_id),
        "active_admins": repo.count_active_admins(user.office_id),
        "job_labels": JOB_LABEL,
        "roles": ROLES,
        **extra,
    }


@router.get("/accounts", response_class=HTMLResponse, include_in_schema=False)
def show(request: Request, user: AdminDep) -> Response:
    return render(request, "accounts.html", _accounts_context(user))


@router.post("/accounts", include_in_schema=False)
def create(request: Request, user: AdminDep,
           email: Annotated[str, Form()],
           role: Annotated[str, Form()],
           staff_id: Annotated[str, Form()] = "") -> Response:
    # データベースの CHECK が小文字を要求する。ここで揃える。
    email = email.strip().lower()
    if "@" not in email or len(email) < 5:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "メールアドレスの形式が不正です。")
    if role not in ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "権限が不正です。")

    sid: int | None = None
    if staff_id.strip():
        sid = int(staff_id)
        # 他事業所の職員IDを渡されても紐付けられないようにする。
        candidates = {s["staff_id"] for s in
                      repo.list_staff_without_user(user.office_id)}
        if sid not in candidates:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "その職員は選べません（既にアカウントがある、"
                "退職済み、または他事業所の職員です）。")
    elif role == "staff":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "職員権限のアカウントには職員の紐付けが必要です。")

    if any(u["email"] == email for u in repo.list_users(user.office_id)):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "そのメールアドレスは既に使われています。")

    new_id = repo.insert_user(user.office_id, email, role, sid)
    audit.record_user(request, user, audit.ACCOUNT_CREATE,
                      f"{email} を権限 {role} で作成した"
                      + (f"（職員ID {sid} に紐付け）" if sid else ""),
                      target_type="user", target_id=new_id)

    # 作成しただけではログインできない。続けて初回設定リンクを発行する。
    return _issue_link(request, user, new_id, first_time=True)


@router.post("/accounts/{target_id}/reset", include_in_schema=False)
def issue_reset(request: Request, user: AdminDep, target_id: int) -> Response:
    return _issue_link(request, user, target_id, first_time=False)


def _issue_link(request: Request, user: AdminDep, target_id: int,
                *, first_time: bool) -> Response:
    issued = repo.issue_reset_token(user.office_id, target_id, user.user_id)
    if issued is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "対象のアカウントが見つかりません。")
    raw, expires_at = issued
    target = next((u for u in repo.list_users(user.office_id)
                   if u["user_id"] == target_id), None)

    audit.record_user(
        request, user, audit.PASSWORD_RESET_ISSUED,
        ("初回設定リンクを発行した" if first_time else "再設定リンクを発行した")
        + f"（有効期限 {expires_at:%Y-%m-%d %H:%M}）",
        target_type="user", target_id=target_id)

    # リンクは一度しか表示しない。トークンの平文は保存していないため、
    # この画面を閉じたら再発行するしかない。その旨を画面に書く。
    return render(request, "accounts.html", _accounts_context(
        user,
        issued_link=_reset_url(request, raw),
        issued_email=target["email"] if target else "",
        issued_expires=expires_at,
        issued_first_time=first_time,
    ))


@router.post("/accounts/{target_id}/active", include_in_schema=False)
def set_active(request: Request, user: AdminDep, target_id: int,
               is_active: Annotated[str, Form()]) -> Response:
    active = is_active == "1"

    if not active:
        if target_id == user.user_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "自分自身を無効化することはできません。")
        target = next((u for u in repo.list_users(user.office_id)
                       if u["user_id"] == target_id), None)
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                "対象のアカウントが見つかりません。")
        if target["role"] == "admin" and repo.count_active_admins(
                user.office_id) <= 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "有効な管理者が0人になる操作はできません。"
                "先に別の管理者を用意してください。")

    row = repo.set_user_active(user.office_id, target_id, active)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "対象のアカウントが見つかりません。")
    audit.record_user(
        request, user,
        audit.ACCOUNT_ACTIVATE if active else audit.ACCOUNT_DEACTIVATE,
        f"{row['email']} を" + ("有効化した" if active else
                                "無効化した（既存セッションも無効化）"),
        target_type="user", target_id=target_id)
    return RedirectResponse("/accounts", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/accounts/{target_id}/role", include_in_schema=False)
def set_role(request: Request, user: AdminDep, target_id: int,
             role: Annotated[str, Form()]) -> Response:
    if role not in ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "権限が不正です。")
    if target_id == user.user_id and role != "admin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "自分自身の権限を下げることはできません。")

    target = next((u for u in repo.list_users(user.office_id)
                   if u["user_id"] == target_id), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "対象のアカウントが見つかりません。")
    if role == "staff" and target["staff_id"] is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "職員に紐付いていないアカウントは"
                            "職員権限にできません。")
    if (target["role"] == "admin" and role != "admin"
            and repo.count_active_admins(user.office_id) <= 1):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "有効な管理者が0人になる操作はできません。")

    row = repo.set_user_role(user.office_id, target_id, role)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "対象のアカウントが見つかりません。")
    audit.record_user(request, user, audit.ACCOUNT_ROLE_CHANGE,
                      f"{row['email']} の権限を {target['role']} から "
                      f"{role} に変更した（既存セッションも無効化）",
                      target_type="user", target_id=target_id)
    return RedirectResponse("/accounts", status_code=status.HTTP_303_SEE_OTHER)


# =====================================================================
# 監査ログの閲覧
# =====================================================================
@router.get("/audit", response_class=HTMLResponse, include_in_schema=False)
def audit_log(request: Request, user: AdminDep,
              group: str = "", limit: int = AUDIT_LIMIT) -> Response:
    valid = {g for g, _ in audit.GROUPS}
    if group not in valid:
        group = ""
    limit = max(1, min(limit, AUDIT_LIMIT_MAX))
    return render(request, "audit.html", {
        "rows": repo.list_audit(user.office_id, limit=limit,
                                action_prefix=group),
        "total": repo.count_audit(user.office_id),
        "groups": audit.GROUPS,
        "labels": audit.LABELS,
        "group": group,
        "limit": limit,
    })
