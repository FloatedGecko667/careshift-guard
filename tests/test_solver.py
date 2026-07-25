"""シフト最適化エンジンの回帰テスト。"""
from __future__ import annotations

import pytest

from app.solver import REST, solve
from tests.demo_data import audit, build, care_worker_required_fte

# テストでの求解上限。本番の既定は10秒。
# ここで検証したいのは「制約が守られること」「違反が報告されること」であり、
# 最適性の証明そのものではない。上限に達しても最良解が返る設計のため、
# 短い上限でも検証内容は損なわれない。
# CI の実行時間を短く保つことは、テストを実際に回してもらうために重要である。
TIME_LIMIT = 3.0


# --------------------------------------------------------------- 算定式
@pytest.mark.parametrize("users,expected", [
    (10, 1.0), (15, 1.0), (16, 1.2), (18, 1.6),
    (20, 2.0), (22, 2.4), (25, 3.0), (30, 4.0), (40, 6.0),
])
def test_必要常勤換算の端数を切り上げない(users, expected):
    """条文は「5で除して得た数」であり、端数は切り上げない。

    切り上げると過剰配置を要求し、切り捨てると基準未達を見逃す。
    """
    assert care_worker_required_fte(users) == pytest.approx(expected)


# --------------------------------------------------------------- 実行可能性
def test_必ず解が返る():
    """ハード制約は全員公休で充足するため、INFEASIBLE にならない。"""
    prob = build(20, 31, avg_users=25)
    sol = solve(prob, time_limit=TIME_LIMIT, workers=2)
    assert sol.status in ("OPTIMAL", "FEASIBLE")
    assert len(sol.assign) == len(prob.staff) * prob.num_days


def test_人員が極端に不足していても解が返り違反が報告される():
    """職員10名・利用者35名（必要常勤換算5.0）では充足不能。

    「解なし」ではなく「不足の内訳」を返すことが製品の要件。
    """
    prob = build(10, 31, avg_users=35)
    sol = solve(prob, time_limit=TIME_LIMIT, workers=2)
    assert sol.status in ("OPTIMAL", "FEASIBLE")
    assert sol.violations, "充足不能なのに違反が報告されていない"
    for v in sol.violations:
        assert v.actual < v.required
        assert v.kind in ("fte", "headcount")


# --------------------------------------------------------------- ハード制約
@pytest.mark.parametrize("num_staff", [10, 20, 30, 40, 60])
def test_ハード制約が守られている(num_staff):
    """ソルバーとは独立に実装した監査関数で割当を再検査する。"""
    prob = build(num_staff, 31, avg_users=25)
    sol = solve(prob, time_limit=TIME_LIMIT, workers=2)
    errs = audit(prob, sol)
    assert errs == [], f"制約違反 {len(errs)} 件: {errs[:5]}"


def test_休業日は全職員が公休():
    prob = build(24, 31, avg_users=22)
    sol = solve(prob, time_limit=TIME_LIMIT, workers=2)
    for d, is_closed in enumerate(prob.closed):
        if not is_closed:
            continue
        for i in range(len(prob.staff)):
            assert sol.assign[i, d] == REST, f"休業日{d}に職員{i}が出勤"


# --------------------------------------------------------------- 充足性
def test_十分な人員があれば基準違反ゼロで解ける():
    prob = build(30, 31, avg_users=25)
    sol = solve(prob, time_limit=TIME_LIMIT, workers=2)
    assert sol.violations == [], f"違反 {len(sol.violations)} 件"


def test_常勤換算の算定が勤務時間と一致する():
    """sol.fte が、割当から手計算した値と一致することを確認する。"""
    prob = build(24, 31, avg_users=22)
    sol = solve(prob, time_limit=TIME_LIMIT, workers=2)
    h = [p.work_minutes for p in prob.patterns]
    for job in prob.required_fte:
        for d in range(prob.num_days):
            mins = sum(h[sol.assign[i, d]] * st.weight_for(job)
                       for i, st in enumerate(prob.staff))
            assert sol.fte[job, d] == pytest.approx(
                mins / prob.fulltime_day_minutes, abs=0.011)


# --------------------------------------------------------------- 兼務の按分
def test_兼務者の従事割合が正しく配分される():
    from tests.demo_data import build_demo
    prob = build_demo()
    manager = prob.staff[0]
    assert manager.job == "管理者"
    assert manager.secondary_job == "生活相談員"
    assert manager.weight_for("管理者") == pytest.approx(0.5)
    assert manager.weight_for("生活相談員") == pytest.approx(0.5)
    assert manager.weight_for("介護職員") == 0.0
    assert manager.work_form_code == "B"          # 常勤・兼務

    solo = prob.staff[1]
    assert solo.weight_for("生活相談員") == pytest.approx(1.0)
    assert solo.work_form_code == "A"             # 常勤・専従


def test_管理者は常勤換算の対象外():
    """職種集合に管理者を含めないことで、自動的に算入されない。"""
    from tests.demo_data import build_demo
    prob = build_demo()
    assert "管理者" not in prob.required_fte


# --------------------------------------------------------------- 再現性
def test_決定的モードは同じ解を返す():
    prob = build(20, 31, avg_users=25)
    a = solve(prob, time_limit=2.0, deterministic=True)
    b = solve(prob, time_limit=2.0, deterministic=True)
    assert a.assign == b.assign
    assert a.objective == b.objective
