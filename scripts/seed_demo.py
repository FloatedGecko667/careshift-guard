"""デモ用のデータを投入する。web コンテナ内で実行する。

seed.sql は事業所・雇用区分・勤務区分・管理者アカウントまでを作る。
このスクリプトはそこに職員18名と勤務希望を追加し、管理者パスワードを設定する。

職員構成は tests/demo_data.build_demo と同じにしている。
管理者が生活相談員を50％兼務し、機能訓練指導員が2名しかいないため、
平均利用者数を上げると日別の基準違反が発生する。
デモで見せたい「月次では適合、日別では違反」がこの構成で再現される。

使い方:
    docker compose exec web python3 -m scripts.seed_demo [管理者パスワード]
"""
from __future__ import annotations

import random
import sys

from sqlalchemy import text

from app.db import connection
from app.security import hash_password

DEFAULT_PASSWORD = "CareShift2026!"  # noqa: S105  デモ用。本番では使わない

# (氏名, 職種, 常勤か, 兼務先, 従事割合, 資格)
STAFF = [
    ("佐藤 一郎", "manager", True, "counselor", 0.50,
     ["介護福祉士", "認知症介護実践者研修修了"]),
    ("鈴木 花子", "counselor", True, None, 0, ["社会福祉士"]),
    ("高橋 美咲", "nurse", True, None, 0, ["看護師"]),
    ("田中 良子", "nurse", False, None, 0, ["准看護師"]),
    ("伊藤 健", "trainer", True, "care_worker", 0.30, ["理学療法士"]),
    ("渡辺 直美", "trainer", False, None, 0, ["柔道整復師"]),
]
CARE_QUALS = ["介護福祉士", "実務者研修修了", "初任者研修修了"]
for _k in range(12):
    STAFF.append((f"介護 {_k + 1:02d}", "care_worker", _k < 7, None, 0,
                  [CARE_QUALS[_k % 3]]))

SQL_OFFICE = "SELECT office_id FROM offices ORDER BY office_id LIMIT 1"

SQL_EMP = """
SELECT employment_type_id, is_fulltime
FROM employment_types
WHERE office_id = :office_id
ORDER BY is_fulltime DESC, weekly_minutes DESC
"""

SQL_COUNT_STAFF = "SELECT count(*) FROM staff WHERE office_id = :office_id"

# qualifications は text[]。pg8000 の配列変換に依存せず、
# リテラル文字列を明示的にキャストする。
SQL_INSERT = """
INSERT INTO staff (office_id, name, job_type, employment_type_id,
                   qualifications, secondary_job_type, secondary_ratio, hired_on)
VALUES (:office_id, :name, :job_type, :employment_type_id,
        CAST(:quals AS text[]), :sec, :ratio, DATE '2024-04-01')
RETURNING staff_id
"""

SQL_SET_PASSWORD = """
UPDATE users SET password_hash = :h WHERE office_id = :office_id
"""  # noqa: S105  SQL文であり秘密情報ではない

SQL_CLEAR_REQ = "DELETE FROM shift_requests WHERE office_id = :office_id"

SQL_INSERT_REQ = """
INSERT INTO shift_requests (office_id, staff_id, target_date, request_type, note)
VALUES (:office_id, :staff_id, :target_date, 'off', :note)
ON CONFLICT (staff_id, target_date) DO NOTHING
"""


def pg_array(values: list[str]) -> str:
    """PostgreSQL の配列リテラルへ変換する。"""
    return "{" + ",".join('"' + v.replace('"', '\\"') + '"' for v in values) + "}"


def main() -> int:
    password = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PASSWORD
    # 希望休の日付を散らすだけ。暗号用途ではない。
    rnd = random.Random(11)  # noqa: S311

    with connection() as conn:
        office_id = conn.execute(text(SQL_OFFICE)).scalar_one_or_none()
        if office_id is None:
            print("事業所がありません。先に seed.sql を適用してください。")
            return 1

        emp = conn.execute(text(SQL_EMP), {"office_id": office_id}).mappings().all()
        if not emp:
            print("雇用区分がありません。先に seed.sql を適用してください。")
            return 1
        full = next(e["employment_type_id"] for e in emp if e["is_fulltime"])
        part = next((e["employment_type_id"] for e in emp if not e["is_fulltime"]),
                    full)

        already = conn.execute(text(SQL_COUNT_STAFF),
                               {"office_id": office_id}).scalar_one()
        staff_ids: list[int] = []
        if already:
            print(f"職員が既に {already} 名登録されています。追加はしません。")
        else:
            for name, job, is_ft, sec, ratio, quals in STAFF:
                sid = conn.execute(text(SQL_INSERT), {
                    "office_id": office_id, "name": name, "job_type": job,
                    "employment_type_id": full if is_ft else part,
                    "quals": pg_array(quals),
                    "sec": sec, "ratio": ratio,
                }).scalar_one()
                staff_ids.append(int(sid))
            print(f"職員 {len(staff_ids)} 名を登録しました。")

        # 勤務希望。各職員に4日ぶんの希望休を入れる。
        # 希望が反映されない日が色分けされるところを見せるため。
        if staff_ids:
            import datetime as dt
            today = dt.date.today()
            first = dt.date(today.year + (today.month == 12),
                            today.month % 12 + 1, 1)
            nxt = dt.date(first.year + (first.month == 12),
                          first.month % 12 + 1, 1)
            nd = (nxt - first).days
            conn.execute(text(SQL_CLEAR_REQ), {"office_id": office_id})
            n = 0
            for sid in staff_ids:
                for d in rnd.sample(range(nd), 4):
                    conn.execute(text(SQL_INSERT_REQ), {
                        "office_id": office_id, "staff_id": sid,
                        "target_date": (first + dt.timedelta(days=d)).isoformat(),
                        "note": None})
                    n += 1
            print(f"勤務希望 {n} 件を登録しました（対象月 {first:%Y年%m月}）。")

        conn.execute(text(SQL_SET_PASSWORD),
                     {"h": hash_password(password), "office_id": office_id})
        print("管理者パスワードを設定しました。")

    print("\n--- ログイン情報 ---")
    print("  メールアドレス: admin@example.jp")
    print(f"  パスワード    : {password}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
