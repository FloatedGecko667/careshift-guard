"""FastAPI の依存関係。

テナント分離の要点
  すべての DB アクセスは CurrentUser.office_id で絞り込む。
  経路の引数から office_id を受け取らない設計にしている。
  受け取ると、他事業所の ID を渡されたときに漏えいしうる。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.security import SESSION_COOKIE, CurrentUser, read_session


class LoginRequired(Exception):
    """未ログイン。例外ハンドラでログイン画面へ誘導する。"""


def optional_user(request: Request) -> CurrentUser | None:
    return read_session(request.cookies.get(SESSION_COOKIE))


def require_user(request: Request) -> CurrentUser:
    user = optional_user(request)
    if user is None:
        raise LoginRequired
    return user


def require_admin(
    user: Annotated[CurrentUser, Depends(require_user)],
) -> CurrentUser:
    """管理者のみ。職員は自分の希望入力とシフト閲覧しか行えない。"""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="この操作は管理者のみ実行できます。")
    return user


def login_redirect(request: Request) -> RedirectResponse:
    """htmx からの要求なら遷移指示ヘッダを返す。通常要求なら 303 で誘導する。"""
    if request.headers.get("HX-Request") == "true":
        r = RedirectResponse("/login", status_code=status.HTTP_200_OK)
        r.headers["HX-Redirect"] = "/login"
        return r
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


CurrentUserDep = Annotated[CurrentUser, Depends(require_user)]
AdminDep = Annotated[CurrentUser, Depends(require_admin)]
OptionalUserDep = Annotated[CurrentUser | None, Depends(optional_user)]
