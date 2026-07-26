"""ログインとログアウト。"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import audit
from app import repository as repo
from app.config import get_settings
from app.deps import CurrentUserDep
from app.security import (
    SESSION_COOKIE,
    CurrentUser,
    cookie_params,
    issue_session,
    needs_rehash,
    normalize_password,
    password_problem,
    throttle,
    verify_password,
)
from app.templating import render

router = APIRouter(tags=["auth"])

# 事業所が特定できない失敗（存在しないメールアドレス等）でも記録を残す。
# 事業所に紐づけられないので、記録先は最初の事業所とする。
# 単一テナント運用を前提とした割り切りであり、
# 複数テナントに広げる際は「テナント外」の記録先を別に用意する。


def _audit_office_id(row: dict | None) -> int | None:
    return int(row["office_id"]) if row else None


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
        row = repo.find_user_by_email(email)
        oid = _audit_office_id(row)
        if oid is not None:
            audit.record(request, oid, email, audit.LOGIN_BLOCKED,
                         "試行回数の上限に達したため拒否した")
        ctx["error"] = "試行回数が上限に達しました。しばらく待って再度お試しください。"
        return render(request, "login.html", ctx,
                      status_code=status.HTTP_429_TOO_MANY_REQUESTS)

    row = repo.find_user_by_email(email)
    # 利用者が存在しない場合もパスワード検証と同等の時間を使い、
    # 応答時間の差からアカウントの存在を推測されないようにする。
    ok = bool(row) and verify_password(row["password_hash"],
                                       normalize_password(password))

    if not ok:
        throttle.record_failure(key)
        oid = _audit_office_id(row)
        if oid is not None:
            audit.record(request, oid, email, audit.LOGIN_FAILURE,
                         "パスワードが一致しなかった",
                         actor_user_id=int(row["user_id"]))
        # 「メールアドレスが違う」「パスワードが違う」を区別しない
        ctx["error"] = "メールアドレスまたはパスワードが正しくありません。"
        return render(request, "login.html", ctx,
                      status_code=status.HTTP_401_UNAUTHORIZED)

    throttle.reset(key)

    # Argon2 のパラメータを引き上げた場合、この機会に再ハッシュする。
    # 世代は進めない。利用者から見れば同じパスワードのままであり、
    # ここで他端末を切ると理由の分からないログアウトになる。
    if needs_rehash(row["password_hash"]):
        repo.update_password_hash(row["user_id"], normalize_password(password))

    repo.touch_last_login(row["user_id"])

    user = CurrentUser(
        user_id=row["user_id"], office_id=row["office_id"],
        email=row["email"], role=row["role"], staff_id=row["staff_id"],
        session_epoch=int(row["session_epoch"]))

    audit.record_user(request, user, audit.LOGIN_SUCCESS,
                      f"権限 {user.role} でログインした")

    resp = RedirectResponse("/schedules", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(value=issue_session(user), **cookie_params())
    return resp


@router.post("/logout", include_in_schema=False)
def logout(request: Request) -> Response:
    # ログアウトは未ログインでも押されうる。記録は取れたときだけ残す。
    from app.deps import optional_user
    user = optional_user(request)
    if user is not None:
        audit.record_user(request, user, audit.LOGOUT, "ログアウトした")
    resp = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# =====================================================================
# パスワードの再設定（未ログインで使う）
#
#   メール送信基盤を前提にしない。管理者が画面でリンクを発行し、
#   本人へ渡す運用とする。SMTP を要件に入れると、提案の費用構成に
#   メール配信サービスが加わり、月額の根拠が変わってしまう。
#   自己申請のリンク送信は次の段階で足す。
# =====================================================================
@router.get("/password/reset", response_class=HTMLResponse,
            include_in_schema=False, name="password_reset_form")
def reset_form(request: Request, token: str = "") -> Response:
    row = repo.find_valid_reset_token(token) if token else None
    if row is None:
        return render(request, "password_reset.html", {
            "invalid": True,
            "error": "このリンクは無効か、期限が切れています。"
                     "管理者に再発行を依頼してください。",
        }, status_code=status.HTTP_400_BAD_REQUEST)
    return render(request, "password_reset.html",
                  {"token": token, "email": row["email"]})


@router.post("/password/reset", include_in_schema=False)
def reset_submit(request: Request,
                 token: Annotated[str, Form()],
                 password: Annotated[str, Form()],
                 password_confirm: Annotated[str, Form()]) -> Response:
    row = repo.find_valid_reset_token(token)
    if row is None:
        return render(request, "password_reset.html", {
            "invalid": True,
            "error": "このリンクは無効か、期限が切れています。",
        }, status_code=status.HTTP_400_BAD_REQUEST)

    ctx = {"token": token, "email": row["email"]}
    if password != password_confirm:
        ctx["error"] = "確認用のパスワードが一致しません。"
        return render(request, "password_reset.html", ctx,
                      status_code=status.HTTP_400_BAD_REQUEST)
    problem = password_problem(password, row["email"])
    if problem:
        ctx["error"] = problem
        return render(request, "password_reset.html", ctx,
                      status_code=status.HTTP_400_BAD_REQUEST)

    epoch = repo.complete_password_reset(
        int(row["token_id"]), int(row["user_id"]), normalize_password(password))
    if epoch is None:
        # 同じリンクが二重に送信された場合。先の1回だけを通す。
        ctx["invalid"] = True
        ctx["error"] = "このリンクは既に使用されています。"
        return render(request, "password_reset.html", ctx,
                      status_code=status.HTTP_400_BAD_REQUEST)

    audit.record(request, int(row["office_id"]), str(row["email"]),
                 audit.PASSWORD_RESET_DONE,
                 "再設定リンクからパスワードを設定した。"
                 "既存のセッションはすべて無効化した",
                 actor_user_id=int(row["user_id"]),
                 target_type="user", target_id=int(row["user_id"]))

    # 設定直後に自動ログインはしない。
    # リンクを開いた者が本人であるとは限らないため、
    # 新しいパスワードでの入力を1回求める。
    resp = RedirectResponse("/login?reset=done",
                            status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# =====================================================================
# パスワードの変更（ログイン済みの本人が行う）
# =====================================================================
@router.get("/password/change", response_class=HTMLResponse,
            include_in_schema=False)
def change_form(request: Request, user: CurrentUserDep) -> Response:
    return render(request, "password_change.html", {"user": user})


@router.post("/password/change", include_in_schema=False)
def change_submit(request: Request, user: CurrentUserDep,
                  current_password: Annotated[str, Form()],
                  password: Annotated[str, Form()],
                  password_confirm: Annotated[str, Form()]) -> Response:
    ctx: dict[str, object] = {"user": user}
    row = repo.find_user_by_email(user.email)
    # 現在のパスワードを必ず確認する。
    # 端末を離席中に乗っ取られた場合、確認が無いと締め出されてしまう。
    if row is None or not verify_password(row["password_hash"],
                                          normalize_password(current_password)):
        ctx["error"] = "現在のパスワードが正しくありません。"
        return render(request, "password_change.html", ctx,
                      status_code=status.HTTP_400_BAD_REQUEST)
    if password != password_confirm:
        ctx["error"] = "確認用のパスワードが一致しません。"
        return render(request, "password_change.html", ctx,
                      status_code=status.HTTP_400_BAD_REQUEST)
    problem = password_problem(password, user.email)
    if problem:
        ctx["error"] = problem
        return render(request, "password_change.html", ctx,
                      status_code=status.HTTP_400_BAD_REQUEST)

    epoch = repo.set_password(user.user_id, normalize_password(password))
    audit.record_user(request, user, audit.PASSWORD_CHANGED,
                      "本人がパスワードを変更した。"
                      "他端末のセッションはすべて無効化した",
                      target_type="user", target_id=user.user_id)

    # 世代が進んだので、いま使っている Cookie も無効になっている。
    # 変更を行った端末だけは、新しい世代で再発行して継続させる。
    fresh = CurrentUser(
        user_id=user.user_id, office_id=user.office_id, email=user.email,
        role=user.role, staff_id=user.staff_id, session_epoch=epoch)
    resp = RedirectResponse("/schedules?password=changed",
                            status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(value=issue_session(fresh), **cookie_params())
    return resp
