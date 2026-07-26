"""監査ログの記録。

実地指導では「誰がいつ確定したか」を説明できる必要がある。
last_login_at だけでは答えられないため、操作を残す。

行為の名前は「対象.動作」で統一する（例 schedule.publish）。
接頭辞で絞り込めるため、画面側の絞り込みが単純になる。

記録に失敗しても業務操作は止めない。詳細は repository.write_audit を参照。
"""
from __future__ import annotations

from fastapi import Request

from app import repository as repo
from app.security import CurrentUser

# 記録する行為。ここに無い名前を使わないための一覧である。
# データベース側は形式（対象.動作）だけを縛っているため、
# 表記の揺れを防ぐのはこの定数の役目になる。
LOGIN_SUCCESS = "login.success"
LOGIN_FAILURE = "login.failure"
LOGIN_BLOCKED = "login.blocked"
LOGOUT = "login.logout"

STAFF_ADD = "staff.add"
STAFF_RETIRE = "staff.retire"

SCHEDULE_GENERATE = "schedule.generate"
SCHEDULE_EDIT = "schedule.edit"
SCHEDULE_PUBLISH = "schedule.publish"
SCHEDULE_EXPORT = "schedule.export"

ACCOUNT_CREATE = "account.create"
ACCOUNT_ACTIVATE = "account.activate"
ACCOUNT_DEACTIVATE = "account.deactivate"
ACCOUNT_ROLE_CHANGE = "account.role_change"

PASSWORD_RESET_ISSUED = "password.reset_issued"  # noqa: S105  行為の名前
PASSWORD_RESET_DONE = "password.reset_done"      # noqa: S105  行為の名前
PASSWORD_CHANGED = "password.changed"            # noqa: S105  行為の名前

# 画面の絞り込みに出す分類
GROUPS = [
    ("", "すべて"),
    ("login", "ログイン"),
    ("account", "アカウント"),
    ("password", "パスワード"),
    ("schedule", "シフト"),
    ("staff", "職員"),
]

# 行為の日本語表記。画面に英語の識別子をそのまま出さない。
LABELS = {
    LOGIN_SUCCESS: "ログイン",
    LOGIN_FAILURE: "ログイン失敗",
    LOGIN_BLOCKED: "ログイン制限",
    LOGOUT: "ログアウト",
    STAFF_ADD: "職員の追加",
    STAFF_RETIRE: "職員の退職登録",
    SCHEDULE_GENERATE: "シフトの生成",
    SCHEDULE_EDIT: "シフトの手修正",
    SCHEDULE_PUBLISH: "シフトの確定公開",
    SCHEDULE_EXPORT: "勤務形態一覧表の出力",
    ACCOUNT_CREATE: "アカウントの作成",
    ACCOUNT_ACTIVATE: "アカウントの有効化",
    ACCOUNT_DEACTIVATE: "アカウントの無効化",
    ACCOUNT_ROLE_CHANGE: "権限の変更",
    PASSWORD_RESET_ISSUED: "再設定リンクの発行",
    PASSWORD_RESET_DONE: "パスワードの再設定",
    PASSWORD_CHANGED: "パスワードの変更",
}


def client_ip(request: Request) -> str | None:
    """接続元のIPを求める。

    nginx を前に置いているため、request.client は nginx になる。
    X-Forwarded-For の先頭を採る。ただしこの値は偽装できるので、
    監査上の参考値として扱い、認可の判断には使わない。
    """
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        first = fwd.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else None


def record(request: Request, office_id: int, actor_email: str, action: str,
           summary: str, *, actor_user_id: int | None = None,
           target_type: str | None = None,
           target_id: int | None = None) -> None:
    repo.write_audit(
        office_id=office_id, actor_user_id=actor_user_id,
        actor_email=actor_email, action=action, summary=summary,
        target_type=target_type, target_id=target_id,
        ip=client_ip(request), user_agent=request.headers.get("user-agent"))


def record_user(request: Request, user: CurrentUser, action: str,
                summary: str, *, target_type: str | None = None,
                target_id: int | None = None) -> None:
    """ログイン済み利用者の操作を記録する。"""
    record(request, user.office_id, user.email, action, summary,
           actor_user_id=user.user_id,
           target_type=target_type, target_id=target_id)
