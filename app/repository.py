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

from typing import Any

from sqlalchemy import text

from app.db import connection
from app.security import hash_password

# =====================================================================
# 認証
# =====================================================================
SQL_FIND_USER_BY_EMAIL = """
SELECT u.user_id, u.office_id, u.email, u.password_hash, u.role, u.staff_id
FROM users u
WHERE u.email = :email
  AND u.is_active
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
