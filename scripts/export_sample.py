"""サンプルの勤務形態一覧表を生成する。

docs/samples/ に置くサンプル出力をこれで作る。

使い方:
    python3 -m scripts.export_sample [出力先.xlsx]
"""
from __future__ import annotations

import sys

from app.excel import export
from app.solver import solve
from tests.demo_data import build_demo


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "docs/samples/勤務形態一覧表_2026年8月.xlsx"
    year, month, users = 2026, 8, 22.0

    prob = build_demo(year, month, users)
    sol = solve(prob, time_limit=10.0, workers=2)
    print(f"求解: {sol.status} / {sol.solve_seconds:.2f}秒 / 違反 {len(sol.violations)}件")

    export(prob, sol, out, year=year, month=month,
           office_name="デイサービスさくら", avg_users=users)
    print(f"出力: {out}")

    h = [p.work_minutes for p in prob.patterns]
    print("\n職種別 常勤換算（暦月・小数点以下第2位切り捨て）")
    for job in ("生活相談員", "看護職員", "介護職員", "機能訓練指導員"):
        mins = sum(h[sol.assign[i, d]] * st.weight_for(job)
                   for i, st in enumerate(prob.staff) for d in range(prob.num_days))
        fte = int(mins / prob.fulltime_month_minutes * 10) / 10
        req = max(prob.required_fte[job])
        print(f"  {job:8s} 勤務延 {mins / 60:7.1f}h  常勤換算 {fte:4.1f}  "
              f"基準 {req:.1f}  {'適合' if fte >= req else '日別に要確認'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
