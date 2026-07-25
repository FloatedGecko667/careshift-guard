"""ログインとログアウト。"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import repository as repo
from app.config import get_settings
from app.security import (
    SESSION_COOKIE,
    CurrentUser,
    cookie_params,
    issue_session,
    needs_rehash,
    throttle,
    verify_password,
)
from app.templating import render

router = APIRouter(tags=["auth"])


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_form(request: Request) -> Response:
    return render(request, "login.html", {"app_env": get_settings().app_env})


@router.post("/login", include_in_schema=False)
def login(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    email = email.strip().lower()
    # 総当たり対策の鍵は「メールアドレス + 接続元」とする。
    # メールアドレスのみだと、正規利用者を第三者が締め出せてしまう。
    client = request.client.host if request.client else "unknown"
    key = f"{email}|{client}"

    ctx = {"app_env": get_settings().app_env, "email": email}

    if throttle.is_blocked(key):
        ctx["error"] = "試行回数が上限に達しました。しばらく待って再度お試しください。"
        return render(request, "login.html", ctx,
                      status_code=status.HTTP_429_TOO_MANY_REQUESTS)

    row = repo.find_user_by_email(email)
    # 利用者が存在しない場合もパスワード検証と同等の時間を使い、
    # 応答時間の差からアカウントの存在を推測されないようにする。
    ok = bool(row) and verify_password(row["password_hash"], password)

    if not ok:
        throttle.record_failure(key)
        # 「メールアドレスが違う」「パスワードが違う」を区別しない
        ctx["error"] = "メールアドレスまたはパスワードが正しくありません。"
        return render(request, "login.html", ctx,
                      status_code=status.HTTP_401_UNAUTHORIZED)

    throttle.reset(key)

    # Argon2 のパラメータを引き上げた場合、この機会に再ハッシュする
    if needs_rehash(row["password_hash"]):
        repo.update_password_hash(row["user_id"], password)

    repo.touch_last_login(row["user_id"])

    user = CurrentUser(
        user_id=row["user_id"], office_id=row["office_id"],
        email=row["email"], role=row["role"], staff_id=row["staff_id"])

    resp = RedirectResponse("/schedules", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(value=issue_session(user), **cookie_params())
    return resp


@router.post("/logout", include_in_schema=False)
def logout() -> Response:
    resp = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp
