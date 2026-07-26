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

from app import repository as repo
from app.security import SESSION_COOKIE, CurrentUser, read_session


class LoginRequired(Exception):
    """未ログイン。例外ハンドラでログイン画面へ誘導する。"""


def optional_user(request: Request) -> CurrentUser | None:
    return read_session(request.cookies.get(SESSION_COOKIE))


def require_user(request: Request) -> CurrentUser:
    """署名の検証に加えて、データベース側の状態も確認する。

    セッションはサーバに状態を持たない署名Cookieである。
    署名だけを信じると次の3つが既存のログインに効かない。
      ・アカウントの無効化
      ・パスワードの変更（漏えい時の締め出し）
      ・権限の変更（職員へ降格しても管理画面に入れてしまう）

    そのため users.session_epoch を要求ごとに1件参照する。
    主キー1件の索引参照なので、この確実さに対して費用は小さい。
    権限は Cookie の値ではなく、必ず DB の現在値を採用する。
    """
    user = optional_user(request)
    if user is None:
        raise LoginRequired

    state = repo.get_session_state(user.user_id)
    if state is None or not state["is_active"]:
        raise LoginRequired
    if int(state["session_epoch"]) != user.session_epoch:
        raise LoginRequired

    return CurrentUser(
        user_id=int(state["user_id"]), office_id=int(state["office_id"]),
        email=str(state["email"]), role=str(state["role"]),
        staff_id=int(state["staff_id"]) if state["staff_id"] is not None else None,
        session_epoch=int(state["session_epoch"]))


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
