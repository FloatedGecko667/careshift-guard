"""業務ロジックのテスト。

app/service.py は意図的にデータベースへ触らない純粋関数で構成しているため、
PostgreSQL を起動せずに全経路を試験できる。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.service import (
    build_problem,
    build_requirements,
    closed_days,
    month_range,
    monthly_fte,
    required_fte_for,
    solution_rows,
)
from app.solver import REST, solve

# ---------------------------------------------------------------- 素データ
OFFICE = {
    "office_id": 1, "name": "デイサービスさくら", "service_type": "day_service",
    "capacity": 35, "fulltime_day_minutes": 480, "fulltime_week_minutes": 2400,
    "fulltime_month_minutes": 9600, "max_weekly_minutes": 2400,
    "max_consecutive_days": 5, "min_rest_days": 8, "min_interval_minutes": 540,
    "closed_weekdays": [6],
}

PATTERNS = [
    {"shift_pattern_id": 101, "code": "休", "name": "公休", "start_minute": 0,
     "end_minute": 0, "break_minutes": 0, "work_minutes": 0, "is_rest": True},
    {"shift_pattern_id": 102, "code": "早", "name": "早番", "start_minute": 420,
     "end_minute": 960, "break_minutes": 60, "work_minutes": 480, "is_rest": False},
    {"shift_pattern_id": 103, "code": "日", "name": "日勤", "start_minute": 510,
     "end_minute": 1050, "break_minutes": 60, "work_minutes": 480, "is_rest": False},
]

RULES = [
    {"job_type": "care_worker", "formula_type": "per_users_step", "base_fte": 1.0,
     "threshold_users": 15, "step_users": 5, "step_fte": 1.0, "min_headcount": 1},
    {"job_type": "nurse", "formula_type": "constant", "base_fte": 1.0,
     "threshold_users": None, "step_users": None, "step_fte": None,
     "min_headcount": 1},
    {"job_type": "counselor", "formula_type": "constant", "base_fte": 1.0,
     "threshold_users": None, "step_users": None, "step_fte": None,
     "min_headcount": 1},
    {"job_type": "trainer", "formula_type": "constant", "base_fte": 1.0,
     "threshold_users": None, "step_users": None, "step_fte": None,
     "min_headcount": 1},
    # 管理者は常勤換算の対象外。ルールがあっても判定対象に入らないこと
    {"job_type": "manager", "formula_type": "constant", "base_fte": 1.0,
     "threshold_users": None, "step_users": None, "step_fte": None,
     "min_headcount": 1},
]


def staff_row(sid, name, job, *, ft=True, wm=2400, hired="2020-04-01",
              retired=None, sec=None, ratio=0.0, quals=None):
    return {"staff_id": sid, "name": name, "job_type": job,
            "qualifications": quals or [], "secondary_job_type": sec,
            "secondary_ratio": ratio, "hired_on": hired, "retired_on": retired,
            "employment_name": "常勤" if ft else "非常勤",
            "is_fulltime": ft, "weekly_minutes": wm}


def make_staff():
    rows = [
        staff_row(1, "佐藤 一郎", "manager", sec="counselor", ratio=0.5),
        staff_row(2, "鈴木 花子", "counselor"),
        staff_row(3, "高橋 美咲", "nurse"),
        staff_row(4, "田中 良子", "nurse", ft=False, wm=1440),
        staff_row(5, "伊藤 健", "trainer", sec="care_worker", ratio=0.3),
        staff_row(6, "渡辺 直美", "trainer", ft=False, wm=1440),
    ]
    for k in range(12):
        rows.append(staff_row(10 + k, f"介護 {k + 1:02d}", "care_worker",
                              ft=k < 7, wm=2400 if k < 7 else 1440))
    return rows


# ---------------------------------------------------------------- 対象月
@pytest.mark.parametrize("y,m,days", [
    (2026, 1, 31), (2026, 2, 28), (2026, 4, 30), (2026, 8, 31), (2026, 12, 31),
    (2028, 2, 29),   # うるう年
])
def test_月の日数が正しい(y, m, days):
    first, nd = month_range(y, m)
    assert first == dt.date(y, m, 1)
    assert nd == days


# ---------------------------------------------------------------- 基準の算定
@pytest.mark.parametrize("users,expected", [
    (10, 1.0), (15, 1.0), (16, 1.2), (18, 1.6),
    (20, 2.0), (22, 2.4), (25, 3.0), (30, 4.0), (40, 6.0),
])
def test_段階加算の端数を切り上げない(users, expected):
    rule = RULES[0]
    assert required_fte_for(rule, users) == pytest.approx(expected)


def test_固定値のルールは利用者数に依存しない():
    for users in (5, 20, 100):
        assert required_fte_for(RULES[1], users) == 1.0


def test_休業曜日から休業日を判定する():
    first, nd = month_range(2026, 8)
    closed = closed_days(OFFICE, first, nd)
    # 2026年8月1日は土曜。日曜は 2, 9, 16, 23, 30 日
    assert [d + 1 for d, c in enumerate(closed) if c] == [2, 9, 16, 23, 30]


def test_管理者は判定対象から除かれる():
    first, nd = month_range(2026, 8)
    closed = closed_days(OFFICE, first, nd)
    fte, head = build_requirements(RULES, 22.0, nd, closed)
    assert "管理者" not in fte
    assert set(fte) == {"介護職員", "看護職員", "生活相談員", "機能訓練指導員"}
    assert head["介護職員"][0] == 1


def test_休業日は必要数が0になる():
    first, nd = month_range(2026, 8)
    closed = closed_days(OFFICE, first, nd)
    fte, head = build_requirements(RULES, 22.0, nd, closed)
    assert fte["介護職員"][1] == 0.0      # 8月2日は日曜
    assert head["介護職員"][1] == 0
    assert fte["介護職員"][0] == 2.4      # 8月1日は土曜（営業日）


# ---------------------------------------------------------------- 組み立て
def test_公休が必ず添字0になる():
    """勤務区分の登録順に依存せず、公休が先頭に来ること。"""
    shuffled = [PATTERNS[1], PATTERNS[2], PATTERNS[0]]
    prob, mp = build_problem(OFFICE, make_staff(), shuffled, RULES, [],
                            2026, 8, 22.0)
    assert prob.patterns[REST].work_minutes == 0
    assert mp.pattern_ids[REST] == 101


def test_公休が未登録なら明確に失敗する():
    with pytest.raises(ValueError, match="公休"):
        build_problem(OFFICE, make_staff(), [PATTERNS[1]], RULES, [],
                      2026, 8, 22.0)


def test_職種コードが表示名へ変換される():
    prob, _ = build_problem(OFFICE, make_staff(), PATTERNS, RULES, [],
                            2026, 8, 22.0)
    jobs = {s.job for s in prob.staff}
    assert jobs == {"管理者", "生活相談員", "看護職員", "介護職員", "機能訓練指導員"}


def test_兼務の按分が引き継がれる():
    prob, _ = build_problem(OFFICE, make_staff(), PATTERNS, RULES, [],
                            2026, 8, 22.0)
    manager = prob.staff[0]
    assert manager.job == "管理者"
    assert manager.secondary_job == "生活相談員"
    assert manager.weight_for("生活相談員") == pytest.approx(0.5)
    assert manager.work_form_code == "B"


def test_在職期間外は勤務不可になる():
    rows = make_staff()
    rows[1]["hired_on"] = "2026-08-15"      # 月中入職
    rows[2]["retired_on"] = "2026-08-10"    # 月中退職
    prob, _ = build_problem(OFFICE, rows, PATTERNS, RULES, [], 2026, 8, 22.0)
    assert not prob.staff[1].available[0]    # 8月1日は入職前
    assert prob.staff[1].available[14]       # 8月15日は在職
    assert prob.staff[2].available[9]        # 8月10日は在職
    assert not prob.staff[2].available[10]   # 8月11日は退職後


def test_希望の種別が正しく振り分けられる():
    reqs = [
        {"staff_id": 10, "target_date": "2026-08-03", "request_type": "off",
         "shift_pattern_id": None, "note": None},
        {"staff_id": 10, "target_date": "2026-08-05", "request_type": "pattern",
         "shift_pattern_id": 102, "note": None},
        {"staff_id": 11, "target_date": "2026-08-07", "request_type": "unavailable",
         "shift_pattern_id": None, "note": "有給"},
    ]
    prob, mp = build_problem(OFFICE, make_staff(), PATTERNS, RULES, reqs,
                            2026, 8, 22.0)
    i10, i11 = mp.staff_index[10], mp.staff_index[11]
    assert prob.pref_off[(i10, 2)] is True                 # 8月3日
    assert prob.pref_pattern[(i10, 4)] == 1                # 早番の添字
    assert not prob.staff[i11].available[6]                # 8月7日は勤務不可


def test_対象月の外や退職者の希望は無視される():
    reqs = [
        {"staff_id": 10, "target_date": "2026-07-31", "request_type": "off",
         "shift_pattern_id": None, "note": None},      # 前月
        {"staff_id": 999, "target_date": "2026-08-03", "request_type": "off",
         "shift_pattern_id": None, "note": None},      # 在籍しない職員
    ]
    prob, _ = build_problem(OFFICE, make_staff(), PATTERNS, RULES, reqs,
                            2026, 8, 22.0)
    assert prob.pref_off == {}


def test_公休を希望勤務区分に指定しても無視される():
    """公休の希望は request_type='off' で表す。区分指定では受けない。"""
    reqs = [{"staff_id": 10, "target_date": "2026-08-03", "request_type": "pattern",
             "shift_pattern_id": 101, "note": None}]
    prob, _ = build_problem(OFFICE, make_staff(), PATTERNS, RULES, reqs,
                            2026, 8, 22.0)
    assert prob.pref_pattern == {}


def test_事業所の労務設定が引き継がれる():
    office = dict(OFFICE, max_consecutive_days=4, min_rest_days=9,
                  min_interval_minutes=660)
    prob, _ = build_problem(office, make_staff(), PATTERNS, RULES, [],
                            2026, 8, 22.0)
    assert prob.max_consecutive_days == 4
    assert prob.min_rest_days == 9
    assert prob.min_interval_minutes == 660


# ---------------------------------------------------------------- 解の変換
@pytest.fixture(scope="module")
def solved():
    prob, mp = build_problem(OFFICE, make_staff(), PATTERNS, RULES, [],
                            2026, 8, 22.0)
    sol = solve(prob, time_limit=5.0, workers=2, deterministic=True)
    return prob, mp, sol


def test_明細行が職員数かける日数だけ作られる(solved):
    prob, mp, sol = solved
    entries, _ = solution_rows(7, prob, sol, mp)
    assert len(entries) == len(prob.staff) * prob.num_days
    assert {e["schedule_id"] for e in entries} == {7}
    assert all(e["shift_pattern_id"] in mp.pattern_ids for e in entries)
    assert all(e["is_manual"] is False for e in entries)


def test_手修正した明細は再生成でも維持される(solved):
    prob, mp, sol = solved
    keep = {(10, "2026-08-03"): 103}
    entries, _ = solution_rows(7, prob, sol, mp, keep_manual=keep)
    target = [e for e in entries
              if e["staff_id"] == 10 and e["target_date"] == "2026-08-03"]
    assert len(target) == 1
    assert target[0]["shift_pattern_id"] == 103
    assert target[0]["is_manual"] is True


def test_違反行に日付と職種が入る(solved):
    prob, mp, sol = solved
    _, violations = solution_rows(7, prob, sol, mp)
    assert len(violations) == len(sol.violations)
    for v in violations:
        assert v["schedule_id"] == 7
        dt.date.fromisoformat(v["target_date"])       # 形式が正しいこと
        assert v["kind"] in ("fte", "headcount")
        assert v["actual"] < v["required"]


def test_暦月の常勤換算は第2位切り捨て(solved):
    prob, _, sol = solved
    got = monthly_fte(prob, sol)
    assert set(got) == {"介護職員", "看護職員", "生活相談員", "機能訓練指導員"}
    for job, v in got.items():
        expected = int(v["minutes"] / prob.fulltime_month_minutes * 10) / 10
        assert v["fte"] == pytest.approx(expected), job
        # 小数第1位までであること
        assert round(v["fte"], 1) == v["fte"]


def test_管理者の勤務は生活相談員に半分だけ算入される(solved):
    """管理者そのものは常勤換算の対象外だが、兼務分は算入される。"""
    prob, _, sol = solved
    h = [p.work_minutes for p in prob.patterns]
    manager = prob.staff[0]
    total = sum(h[sol.assign[0, d]] for d in range(prob.num_days))
    counselor_part = sum(h[sol.assign[0, d]] * manager.weight_for("生活相談員")
                         for d in range(prob.num_days))
    assert counselor_part == pytest.approx(total * 0.5)
    assert "管理者" not in monthly_fte(prob, sol)
