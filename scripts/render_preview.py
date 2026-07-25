"""ソルバーの実出力を schedule.html テンプレートへ流し込み、静的HTMLを書き出す。

用途
  ・画面設計の確認（実装前に見た目と情報量を確定させる）
  ・docs/samples/ に置くサンプルの生成

コンテキストの組み立ては app/presenter.py を共用する。
表示ロジックをここに複製すると、片方だけ直して食い違う。

使い方:
    python3 -m scripts.render_preview [出力先] [職員数] [平均利用者数] [seed]
"""
from __future__ import annotations

import datetime as dt
import sys

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.presenter import make_context
from app.solver import solve
from tests.demo_data import build

TEMPLATE_DIR = "app/templates"


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "docs/samples/preview_schedule.html"
    n_staff = int(sys.argv[2]) if len(sys.argv) > 2 else 24
    users = float(sys.argv[3]) if len(sys.argv) > 3 else 22
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 42

    year, month = 2026, 8
    fw = dt.date(year, month, 1).weekday()
    prob = build(n_staff, 31, avg_users=users, seed=seed, first_weekday=fw)
    sol = solve(prob, time_limit=10.0, workers=2)

    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR),
                      autoescape=select_autoescape(["html"]))
    ctx = make_context(prob, sol, year=year, month=month,
                       office_name="デイサービスさくら")
    # 静的サンプルでは操作できないため、管理者向けのフォームは出さない
    ctx["is_admin"] = False
    ctx["current_user"] = None
    html = env.get_template("schedule.html").render(**ctx)

    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"求解: {sol.status} / {sol.solve_seconds:.2f}秒 / "
          f"違反 {len(sol.violations)}件")
    print(f"出力: {out}  ({len(html):,} バイト)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
