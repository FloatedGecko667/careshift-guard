"""データベースアクセス層。

方針
  ・SQL は SQL_ 接頭辞の定数として一箇所に集約する。
    こうしておくと、全 SQL を PostgreSQL 本体のパーサで
    構文検証するテストが書ける（tests/test_sql.py）。
  ・すべての業務クエリは office_id で絞り込む。
    呼び出し側が office_id を渡し忘れられない形にする。
  ・SQLAlchemy は ORM ではなく Core として使い、
    スキーマ定義は db/schema.sql を唯一の正とする。
"""
from __future__ import annotations

import sys
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import connection
from app.security import (
    RESET_TOKEN_TTL,
    hash_password,
    hash_reset_token,
    new_reset_token,
    unusable_password_hash,
)

# =====================================================================
# 認証
# =====================================================================
SQL_FIND_USER_BY_EMAIL = """
SELECT u.user_id, u.office_id, u.email, u.password_hash, u.role, u.staff_id,
       u.session_epoch
FROM users u
WHERE u.email = :email
  AND u.is_active
"""

# セッションの有効性確認。要求ごとに1回引く。
# 署名Cookieだけを信じると、無効化やパスワード変更が既存の
# ログインに効かない。主キー1件の参照なので費用は小さい。
SQL_GET_SESSION_STATE = """
SELECT u.user_id, u.office_id, u.email, u.role, u.staff_id,
       u.session_epoch, u.is_active
FROM users u
WHERE u.user_id = :user_id
"""

# noqa の位置に注意。三重引用符の内側に書くと SQL 文の一部になってしまう。
SQL_UPDATE_PASSWORD_HASH = """
UPDATE users SET password_hash = :password_hash
WHERE user_id = :user_id
"""  # noqa: S105  SQL文であり秘密情報ではない

SQL_TOUCH_LAST_LOGIN = """
UPDATE users SET last_login_at = now()
WHERE user_id = :user_id
"""


def find_user_by_email(email: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(text(SQL_FIND_USER_BY_EMAIL), {"email": email}).mappings().first()
    return dict(row) if row else None


def update_password_hash(user_id: int, plain_password: str) -> None:
    with connection() as conn:
        conn.execute(text(SQL_UPDATE_PASSWORD_HASH),
                     {"user_id": user_id, "password_hash": hash_password(plain_password)})


def touch_last_login(user_id: int) -> None:
    with connection() as conn:
        conn.execute(text(SQL_TOUCH_LAST_LOGIN), {"user_id": user_id})


def get_session_state(user_id: int) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(text(SQL_GET_SESSION_STATE),
                           {"user_id": user_id}).mappings().first()
    return dict(row) if row else None


# =====================================================================
# アカウント管理
#
#   利用者の行は削除しない。監査ログが actor_user_id で参照しており、
#   消すと「誰が操作したか」を辿れなくなる。無効化で運用する。
# =====================================================================
SQL_LIST_USERS = """
SELECT u.user_id, u.email, u.role, u.staff_id, u.is_active,
       u.last_login_at, u.created_at, s.name AS staff_name,
       s.job_type, s.retired_on,
       (SELECT max(t.created_at) FROM password_reset_tokens t
         WHERE t.user_id = u.user_id AND t.used_at IS NULL
           AND t.expires_at > now())               AS pending_reset_at
FROM users u
LEFT JOIN staff s ON s.staff_id = u.staff_id
WHERE u.office_id = :office_id
ORDER BY u.is_active DESC, u.role, u.email
"""

# アカウントを持たない在職者。紐付け候補の一覧に使う。
SQL_LIST_STAFF_WITHOUT_USER = """
SELECT s.staff_id, s.name, s.job_type
FROM staff s
WHERE s.office_id = :office_id
  AND s.retired_on IS NULL
  AND NOT EXISTS (SELECT 1 FROM users u WHERE u.staff_id = s.staff_id)
ORDER BY s.job_type, s.staff_id
"""

SQL_INSERT_USER = """
INSERT INTO users (office_id, email, password_hash, role, staff_id)
VALUES (:office_id, :email, :password_hash, :role, :staff_id)
RETURNING user_id
"""

# 無効化と再有効化。無効化時は世代を進めて既存セッションを失効させる。
SQL_SET_USER_ACTIVE = """
UPDATE users
SET is_active = :is_active,
    session_epoch = session_epoch + CASE WHEN :is_active THEN 0 ELSE 1 END
WHERE office_id = :office_id
  AND user_id = :user_id
RETURNING email, is_active
"""

SQL_SET_USER_ROLE = """
UPDATE users
SET role = :role,
    session_epoch = session_epoch + 1
WHERE office_id = :office_id
  AND user_id = :user_id
RETURNING email, role
"""

# 事業所に有効な管理者が何人いるか。最後の1人を落とさないために使う。
SQL_COUNT_ACTIVE_ADMINS = """
SELECT count(*) FROM users
WHERE office_id = :office_id
  AND role = 'admin'
  AND is_active
"""


def list_users(office_id: int) -> list[dict[str, Any]]:
    with connection() as conn:
        return [dict(r) for r in conn.execute(
            text(SQL_LIST_USERS), {"office_id": office_id}).mappings()]


def list_staff_without_user(office_id: int) -> list[dict[str, Any]]:
    with connection() as conn:
        return [dict(r) for r in conn.execute(
            text(SQL_LIST_STAFF_WITHOUT_USER),
            {"office_id": office_id}).mappings()]


def insert_user(office_id: int, email: str, role: str,
                staff_id: int | None) -> int:
    """ログインできない状態でアカウントを作る。

    パスワードは初回設定リンクを使って本人が決める。
    管理者が代わりに設定して口頭で伝える運用は、
    伝達経路に平文が残るため採らない。
    """
    with connection() as conn:
        return int(conn.execute(text(SQL_INSERT_USER), {
            "office_id": office_id, "email": email,
            "password_hash": unusable_password_hash(),
            "role": role, "staff_id": staff_id}).scalar_one())


def set_user_active(office_id: int, user_id: int,
                    is_active: bool) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(text(SQL_SET_USER_ACTIVE), {
            "office_id": office_id, "user_id": user_id,
            "is_active": is_active}).mappings().first()
    return dict(row) if row else None


def set_user_role(office_id: int, user_id: int,
                  role: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(text(SQL_SET_USER_ROLE), {
            "office_id": office_id, "user_id": user_id,
            "role": role}).mappings().first()
    return dict(row) if row else None


def count_active_admins(office_id: int) -> int:
    with connection() as conn:
        return int(conn.execute(text(SQL_COUNT_ACTIVE_ADMINS),
                                {"office_id": office_id}).scalar_one())


# =====================================================================
# パスワード再設定
# =====================================================================
SQL_INVALIDATE_RESET_TOKENS = """
UPDATE password_reset_tokens
SET used_at = now()
WHERE user_id = :user_id
  AND used_at IS NULL
"""  # noqa: S105  SQL文であり秘密情報ではない

SQL_INSERT_RESET_TOKEN = """
INSERT INTO password_reset_tokens
       (user_id, token_hash, expires_at, issued_by_user_id)
SELECT u.user_id, :token_hash,
       now() + make_interval(secs => :ttl_seconds), :issued_by
FROM users u
WHERE u.user_id = :user_id
  AND u.office_id = :office_id
RETURNING token_id, expires_at
"""  # noqa: S105  SQL文であり秘密情報ではない

# 有効なトークンを引く。期限と使用済みの判定を SQL 側で行う。
# アプリ側で時刻を比べると、コンテナの時計ずれで判定が変わりうる。
SQL_FIND_VALID_RESET_TOKEN = """
SELECT t.token_id, t.user_id, u.office_id, u.email, u.role, u.staff_id
FROM password_reset_tokens t
JOIN users u ON u.user_id = t.user_id
WHERE t.token_hash = :token_hash
  AND t.used_at IS NULL
  AND t.expires_at > now()
  AND u.is_active
"""  # noqa: S105  SQL文であり秘密情報ではない

SQL_CONSUME_RESET_TOKEN = """
UPDATE password_reset_tokens
SET used_at = now()
WHERE token_id = :token_id
  AND used_at IS NULL
RETURNING token_id
"""  # noqa: S105  SQL文であり秘密情報ではない

# パスワード変更と同時に世代を進める。
# 変更前に発行された Cookie をすべて無効にする。
SQL_SET_PASSWORD = """
UPDATE users
SET password_hash = :password_hash,
    session_epoch = session_epoch + 1
WHERE user_id = :user_id
RETURNING session_epoch
"""  # noqa: S105  SQL文であり秘密情報ではない


def issue_reset_token(office_id: int, user_id: int,
                      issued_by: int | None) -> tuple[str, Any] | None:
    """再設定リンクの平文トークンと期限を返す。既存の未使用分は失効させる。

    複数の有効なリンクが並存すると、どれが最新か分からなくなる。
    発行のたびに前の分を使用済みにする。
    """
    raw, digest = new_reset_token()
    with connection() as conn:
        conn.execute(text(SQL_INVALIDATE_RESET_TOKENS), {"user_id": user_id})
        row = conn.execute(text(SQL_INSERT_RESET_TOKEN), {
            "user_id": user_id, "office_id": office_id,
            "token_hash": digest, "ttl_seconds": RESET_TOKEN_TTL,
            "issued_by": issued_by}).mappings().first()
    if row is None:
        return None
    return raw, row["expires_at"]


def find_valid_reset_token(raw_token: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(text(SQL_FIND_VALID_RESET_TOKEN),
                           {"token_hash": hash_reset_token(raw_token)}
                           ).mappings().first()
    return dict(row) if row else None


def complete_password_reset(token_id: int, user_id: int,
                            plain_password: str) -> int | None:
    """トークンを使用済みにし、パスワードを設定する。

    同一トランザクションで行う。片方だけ成功すると、
    「リンクは使えないのにパスワードも変わっていない」状態になる。
    UPDATE ... RETURNING で使用済み化に成功した場合のみ進める
    ことで、同時に2回押されても1回しか通らない。
    """
    with connection() as conn:
        used = conn.execute(text(SQL_CONSUME_RESET_TOKEN),
                            {"token_id": token_id}).mappings().first()
        if used is None:
            return None
        epoch = conn.execute(text(SQL_SET_PASSWORD), {
            "user_id": user_id,
            "password_hash": hash_password(plain_password)}).scalar_one()
    return int(epoch)


def set_password(user_id: int, plain_password: str) -> int:
    """自分でのパスワード変更。世代を進めて他端末のセッションを切る。"""
    with connection() as conn:
        return int(conn.execute(text(SQL_SET_PASSWORD), {
            "user_id": user_id,
            "password_hash": hash_password(plain_password)}).scalar_one())


# =====================================================================
# 監査ログ
#
#   追記のみ。更新と削除の SQL をここに置かない。
#   置かないだけでは手作業で消せるため、データベース側でも
#   トリガで拒否している（db/schema.sql 参照）。
# =====================================================================
SQL_INSERT_AUDIT = """
INSERT INTO audit_logs (office_id, actor_user_id, actor_email, action,
                        target_type, target_id, summary, ip, user_agent)
VALUES (:office_id, :actor_user_id, :actor_email, :action,
        :target_type, :target_id, :summary, CAST(:ip AS inet), :user_agent)
"""

SQL_LIST_AUDIT = """
SELECT a.audit_id, a.actor_email, a.action, a.target_type, a.target_id,
       a.summary, a.ip, a.created_at
FROM audit_logs a
WHERE a.office_id = :office_id
  AND (:action_prefix = '' OR a.action LIKE :action_prefix || '%')
ORDER BY a.created_at DESC, a.audit_id DESC
LIMIT :limit
"""

SQL_COUNT_AUDIT = """
SELECT count(*) FROM audit_logs WHERE office_id = :office_id
"""


def write_audit(office_id: int, actor_user_id: int | None, actor_email: str,
                action: str, summary: str, *, target_type: str | None = None,
                target_id: int | None = None, ip: str | None = None,
                user_agent: str | None = None) -> None:
    """監査ログを1件書く。

    記録に失敗しても業務操作は続行させる。
    ログのために利用者の操作を止めるのは本末転倒である。
    ただし失敗を黙って捨てると気づけないため、標準エラーへ出す。
    """
    try:
        with connection() as conn:
            conn.execute(text(SQL_INSERT_AUDIT), {
                "office_id": office_id, "actor_user_id": actor_user_id,
                "actor_email": actor_email[:255], "action": action,
                "target_type": target_type, "target_id": target_id,
                "summary": summary[:1000], "ip": ip,
                "user_agent": (user_agent or "")[:255] or None})
    except SQLAlchemyError as e:  # pragma: no cover  記録失敗時のみ
        print(f"監査ログの記録に失敗しました: {action}: {e}", file=sys.stderr)


def list_audit(office_id: int, limit: int = 200,
               action_prefix: str = "") -> list[dict[str, Any]]:
    with connection() as conn:
        return [dict(r) for r in conn.execute(text(SQL_LIST_AUDIT), {
            "office_id": office_id, "limit": limit,
            "action_prefix": action_prefix}).mappings()]


def count_audit(office_id: int) -> int:
    with connection() as conn:
        return int(conn.execute(text(SQL_COUNT_AUDIT),
                                {"office_id": office_id}).scalar_one())


# =====================================================================
# 事業所
# =====================================================================
SQL_GET_OFFICE = """
SELECT office_id, name, service_type, designation_number, capacity,
       fulltime_day_minutes, fulltime_week_minutes, fulltime_month_minutes,
       max_weekly_minutes, max_consecutive_days, min_rest_days,
       min_interval_minutes, closed_weekdays
FROM offices
WHERE office_id = :office_id
"""


def get_office(office_id: int) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(text(SQL_GET_OFFICE),
                           {"office_id": office_id}).mappings().first()
    return dict(row) if row else None


# =====================================================================
# マスタ
# =====================================================================
SQL_LIST_EMPLOYMENT_TYPES = """
SELECT employment_type_id, name, is_fulltime, weekly_minutes
FROM employment_types
WHERE office_id = :office_id
ORDER BY is_fulltime DESC, weekly_minutes DESC
"""

SQL_LIST_STAFF = """
SELECT s.staff_id, s.name, s.job_type, s.qualifications,
       s.secondary_job_type, s.secondary_ratio, s.hired_on, s.retired_on,
       e.name AS employment_name, e.is_fulltime, e.weekly_minutes
FROM staff s
JOIN employment_types e ON e.employment_type_id = s.employment_type_id
WHERE s.office_id = :office_id
  AND (s.retired_on IS NULL OR s.retired_on >= :as_of)
  AND s.hired_on <= :until
ORDER BY s.job_type, s.staff_id
"""

SQL_LIST_SHIFT_PATTERNS = """
SELECT shift_pattern_id, code, name, start_minute, end_minute,
       break_minutes, work_minutes, is_rest, is_night, display_order
FROM shift_patterns
WHERE office_id = :office_id
ORDER BY display_order, shift_pattern_id
"""

SQL_INSERT_STAFF = """
INSERT INTO staff (office_id, name, job_type, employment_type_id,
                   qualifications, secondary_job_type, secondary_ratio, hired_on)
VALUES (:office_id, :name, :job_type, :employment_type_id,
        :qualifications, :secondary_job_type, :secondary_ratio, :hired_on)
RETURNING staff_id
"""

SQL_RETIRE_STAFF = """
UPDATE staff SET retired_on = :retired_on
WHERE staff_id = :staff_id AND office_id = :office_id
"""


def list_employment_types(office_id: int) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(text(SQL_LIST_EMPLOYMENT_TYPES),
                            {"office_id": office_id}).mappings().all()
    return [dict(r) for r in rows]


def list_staff(office_id: int, as_of: str, until: str) -> list[dict[str, Any]]:
    """対象期間に在職している職員を返す。

    as_of  : 期間の開始日。これ以前に退職した職員は除く
    until  : 期間の終了日。これより後に入職する職員は除く
    """
    with connection() as conn:
        rows = conn.execute(text(SQL_LIST_STAFF),
                            {"office_id": office_id, "as_of": as_of,
                             "until": until}).mappings().all()
    return [dict(r) for r in rows]


def list_shift_patterns(office_id: int) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(text(SQL_LIST_SHIFT_PATTERNS),
                            {"office_id": office_id}).mappings().all()
    return [dict(r) for r in rows]


def insert_staff(office_id: int, **kw: Any) -> int:
    params = {"office_id": office_id, "secondary_job_type": None,
              "secondary_ratio": 0, "qualifications": [], **kw}
    with connection() as conn:
        return int(conn.execute(text(SQL_INSERT_STAFF), params).scalar_one())


def retire_staff(office_id: int, staff_id: int, retired_on: str) -> None:
    with connection() as conn:
        conn.execute(text(SQL_RETIRE_STAFF),
                     {"office_id": office_id, "staff_id": staff_id,
                      "retired_on": retired_on})


# =====================================================================
# 人員配置基準ルール
# =====================================================================
SQL_LIST_STAFFING_RULES = """
SELECT job_type, formula_type, base_fte, threshold_users,
       step_users, step_fte, min_headcount, valid_from, valid_to
FROM staffing_rules
WHERE service_type = :service_type
  AND valid_from <= :as_of
  AND (valid_to IS NULL OR valid_to > :as_of)
ORDER BY job_type
"""


def list_staffing_rules(service_type: str, as_of: str) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(text(SQL_LIST_STAFFING_RULES),
                            {"service_type": service_type,
                             "as_of": as_of}).mappings().all()
    return [dict(r) for r in rows]


# =====================================================================
# 希望シフト
# =====================================================================
SQL_LIST_SHIFT_REQUESTS = """
SELECT staff_id, target_date, request_type, shift_pattern_id, note
FROM shift_requests
WHERE office_id = :office_id
  AND target_date >= :from_date
  AND target_date <= :to_date
ORDER BY staff_id, target_date
"""

SQL_UPSERT_SHIFT_REQUEST = """
INSERT INTO shift_requests (office_id, staff_id, target_date,
                            request_type, shift_pattern_id, note)
VALUES (:office_id, :staff_id, :target_date,
        :request_type, :shift_pattern_id, :note)
ON CONFLICT (staff_id, target_date) DO UPDATE
SET request_type = EXCLUDED.request_type,
    shift_pattern_id = EXCLUDED.shift_pattern_id,
    note = EXCLUDED.note
"""

SQL_DELETE_SHIFT_REQUEST = """
DELETE FROM shift_requests
WHERE office_id = :office_id AND staff_id = :staff_id AND target_date = :target_date
"""


def list_shift_requests(office_id: int, from_date: str,
                        to_date: str) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(text(SQL_LIST_SHIFT_REQUESTS),
                            {"office_id": office_id, "from_date": from_date,
                             "to_date": to_date}).mappings().all()
    return [dict(r) for r in rows]


def upsert_shift_request(office_id: int, staff_id: int, target_date: str,
                         request_type: str, shift_pattern_id: int | None = None,
                         note: str | None = None) -> None:
    with connection() as conn:
        conn.execute(text(SQL_UPSERT_SHIFT_REQUEST), {
            "office_id": office_id, "staff_id": staff_id,
            "target_date": target_date, "request_type": request_type,
            "shift_pattern_id": shift_pattern_id, "note": note})


def delete_shift_request(office_id: int, staff_id: int, target_date: str) -> None:
    with connection() as conn:
        conn.execute(text(SQL_DELETE_SHIFT_REQUEST),
                     {"office_id": office_id, "staff_id": staff_id,
                      "target_date": target_date})


# =====================================================================
# シフト表
# =====================================================================
SQL_GET_SCHEDULE = """
SELECT schedule_id, office_id, target_month, avg_expected_users, status,
       solver_status, objective_value, solve_seconds, generated_at, published_at
FROM schedules
WHERE office_id = :office_id AND target_month = :target_month
"""

SQL_UPSERT_SCHEDULE = """
INSERT INTO schedules (office_id, target_month, avg_expected_users, status)
VALUES (:office_id, :target_month, :avg_expected_users, 'draft')
ON CONFLICT (office_id, target_month) DO UPDATE
SET avg_expected_users = EXCLUDED.avg_expected_users
RETURNING schedule_id
"""

SQL_UPDATE_SOLVER_RESULT = """
UPDATE schedules
SET solver_status = :solver_status,
    objective_value = :objective_value,
    solve_seconds = :solve_seconds,
    generated_at = now()
WHERE schedule_id = :schedule_id AND office_id = :office_id
"""

SQL_PUBLISH_SCHEDULE = """
UPDATE schedules
SET status = 'published', published_at = now()
WHERE schedule_id = :schedule_id AND office_id = :office_id
  AND NOT EXISTS (
    SELECT 1 FROM violations v WHERE v.schedule_id = schedules.schedule_id
  )
"""

SQL_DELETE_ENTRIES = """
DELETE FROM schedule_entries WHERE schedule_id = :schedule_id
"""

SQL_INSERT_ENTRY = """
INSERT INTO schedule_entries (schedule_id, staff_id, target_date,
                              shift_pattern_id, is_manual)
VALUES (:schedule_id, :staff_id, :target_date, :shift_pattern_id, :is_manual)
"""

SQL_UPDATE_ENTRY = """
UPDATE schedule_entries
SET shift_pattern_id = :shift_pattern_id, is_manual = true
WHERE schedule_id = :schedule_id AND staff_id = :staff_id
  AND target_date = :target_date
"""

SQL_LIST_ENTRIES = """
SELECT e.staff_id, e.target_date, e.shift_pattern_id, e.is_manual,
       p.code, p.name AS pattern_name, p.work_minutes, p.is_rest
FROM schedule_entries e
JOIN shift_patterns p ON p.shift_pattern_id = e.shift_pattern_id
WHERE e.schedule_id = :schedule_id
ORDER BY e.staff_id, e.target_date
"""


def get_schedule(office_id: int, target_month: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(text(SQL_GET_SCHEDULE),
                           {"office_id": office_id,
                            "target_month": target_month}).mappings().first()
    return dict(row) if row else None


def upsert_schedule(office_id: int, target_month: str,
                    avg_expected_users: float) -> int:
    with connection() as conn:
        return int(conn.execute(text(SQL_UPSERT_SCHEDULE), {
            "office_id": office_id, "target_month": target_month,
            "avg_expected_users": avg_expected_users}).scalar_one())


def save_solution(office_id: int, schedule_id: int, entries: list[dict[str, Any]],
                  violations: list[dict[str, Any]], solver_status: str,
                  objective_value: int, solve_seconds: float) -> None:
    """明細と違反を差し替える。1トランザクションで行う。

    途中で失敗して「明細が消えたまま」になる状態を作らない。
    """
    with connection() as conn:
        conn.execute(text(SQL_UPDATE_SOLVER_RESULT), {
            "schedule_id": schedule_id, "office_id": office_id,
            "solver_status": solver_status, "objective_value": objective_value,
            "solve_seconds": round(solve_seconds, 2)})
        conn.execute(text(SQL_DELETE_ENTRIES), {"schedule_id": schedule_id})
        conn.execute(text(SQL_DELETE_VIOLATIONS), {"schedule_id": schedule_id})
        if entries:
            conn.execute(text(SQL_INSERT_ENTRY), entries)
        if violations:
            conn.execute(text(SQL_INSERT_VIOLATION), violations)


def update_entry(office_id: int, schedule_id: int, staff_id: int,
                 target_date: str, shift_pattern_id: int) -> None:
    with connection() as conn:
        conn.execute(text(SQL_UPDATE_ENTRY), {
            "schedule_id": schedule_id, "staff_id": staff_id,
            "target_date": target_date, "shift_pattern_id": shift_pattern_id})


def list_entries(schedule_id: int) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(text(SQL_LIST_ENTRIES),
                            {"schedule_id": schedule_id}).mappings().all()
    return [dict(r) for r in rows]


def publish_schedule(office_id: int, schedule_id: int) -> bool:
    """違反が1件も無い場合のみ確定する。

    条件を SQL 側に書いているのが要点である。
    アプリ側のチェックだけに頼ると、経路が増えたときに漏れる。
    """
    with connection() as conn:
        result = conn.execute(text(SQL_PUBLISH_SCHEDULE),
                              {"schedule_id": schedule_id, "office_id": office_id})
        return result.rowcount == 1


# =====================================================================
# 基準違反
# =====================================================================
SQL_DELETE_VIOLATIONS = """
DELETE FROM violations WHERE schedule_id = :schedule_id
"""

SQL_INSERT_VIOLATION = """
INSERT INTO violations (schedule_id, target_date, job_type, kind,
                        required, actual, severity)
VALUES (:schedule_id, :target_date, :job_type, :kind,
        :required, :actual, :severity)
"""

SQL_LIST_VIOLATIONS = """
SELECT target_date, job_type, kind, required, actual, severity
FROM violations
WHERE schedule_id = :schedule_id
ORDER BY target_date, job_type, kind
"""


def list_violations(schedule_id: int) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(text(SQL_LIST_VIOLATIONS),
                            {"schedule_id": schedule_id}).mappings().all()
    return [dict(r) for r in rows]


# =====================================================================
# 常勤換算（ビュー経由）
# =====================================================================
SQL_DAILY_FTE = """
SELECT target_date, job_type, total_work_minutes, headcount, fte
FROM v_daily_fte
WHERE schedule_id = :schedule_id
ORDER BY target_date, job_type
"""


def daily_fte(schedule_id: int) -> list[dict[str, Any]]:
    """日別・職種別の常勤換算。

    算定式はビュー v_daily_fte に一箇所だけ置いている。
    画面と帳票が別々に計算すると必ず値がずれるため。
    """
    with connection() as conn:
        rows = conn.execute(text(SQL_DAILY_FTE),
                            {"schedule_id": schedule_id}).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------
# テストから参照する SQL の一覧
# ---------------------------------------------------------------------
def all_sql() -> dict[str, str]:
    """SQL_ 接頭辞の定数をすべて返す。構文検証テストで使う。"""
    return {k: v for k, v in globals().items()
            if k.startswith("SQL_") and isinstance(v, str)}
