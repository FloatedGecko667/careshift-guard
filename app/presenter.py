"""シフト表テンプレートへ渡すコンテキストを組み立てる。

Web アプリとサンプル生成スクリプトの双方がここを使う。
表示ロジックを二重に持つと、片方だけ直して食い違う。
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from app.jobs import FTE_TARGET_JOBS, JOB_ORDER
from app.solver import REST, Problem, Solution

WD = ["月", "火", "水", "木", "金", "土", "日"]
KIND_LABEL = {"fte": "常勤換算", "headcount": "実人数"}

# 余裕が少ない日を黄色で示す閾値（常勤換算）
WARN_MARGIN = 0.2


def _hhmm(minute: int) -> str:
    """0時からの経過分を時刻表記にする。"""
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _time_label(pattern: Any) -> str:
    """勤務区分の時間帯。公休など勤務時間が0のものは空にする。"""
    if not pattern.work_minutes:
        return ""
    return f"{_hhmm(pattern.start)}〜{_hhmm(pattern.end)}"


def make_context(prob: Problem, sol: Solution, *, year: int, month: int,
                 office_name: str, schedule_id: int = 1,
                 status: str = "draft",
                 staff_ids: list[int] | None = None) -> dict[str, Any]:
    first = dt.date(year, month, 1)
    closed = prob.closed if prob.closed else [False] * prob.num_days

    days = []
    for d in range(prob.num_days):
        date = first + dt.timedelta(days=d)
        wd = date.weekday()
        cls = "closed" if closed[d] else (
            "sun" if wd == 6 else ("sat" if wd == 5 else ""))
        days.append({"n": date.day, "weekday": WD[wd], "cls": cls,
                     "date": date.isoformat()})

    # 反映できなかった希望を特定する
    missed: set[tuple[int, int]] = set()
    for (i, d), on in prob.pref_off.items():
        if on and sol.assign[i, d] != REST:
            missed.add((i, d))
    for (i, d), p in prob.pref_pattern.items():
        if sol.assign[i, d] != p:
            missed.add((i, d))

    # 職種ごとに職員をまとめる。
    #
    # 表に出す職種は JOB_ORDER（全職種）である。FTE_TARGET_JOBS に
    # 絞ってはいけない。管理者は常勤換算の対象外だが、実際には勤務する。
    # 絞ると管理者の行が画面から消え、勤務形態一覧表（管理者を含む）と
    # 食い違う。管理者に紐づく利用者は自分のシフトも見られなくなる。
    groups = []
    for job in JOB_ORDER:
        rows = []
        for i, st in enumerate(prob.staff):
            if st.job != job:
                continue
            cells = []
            for d in range(prob.num_days):
                p = sol.assign[i, d]
                pat = prob.patterns[p]
                cells.append({
                    "code": "休" if p == REST else pat.name[:1],
                    "is_rest": p == REST,
                    "is_manual": False,
                    "pref_missed": (i, d) in missed,
                    "date": days[d]["date"],
                    # 以下は狭い画面の一覧表示で使う。
                    # 1文字の記号だけでは、スマートフォンで見たときに
                    # 何時から何時までなのか読み取れない。
                    "name": pat.name,
                    "time_label": _time_label(pat),
                    "hours": (round(pat.work_minutes / 60, 1)
                              if pat.work_minutes else None),
                })
            rows.append({
                "id": staff_ids[i] if staff_ids else i + 1,
                "name": st.name,
                "employment": "常勤" if st.is_fulltime else "非常勤",
                "cells": cells,
                "total_hours": round(sol.staff_minutes[i] / 60, 1),
            })
        if rows:
            groups.append({"job_label": job, "rows": rows})

    # 常勤換算の行
    vio_days = {(v.job, v.day) for v in sol.violations}
    fte_rows = []
    for job in FTE_TARGET_JOBS:
        if job not in prob.required_fte:
            continue
        cells = []
        for d in range(prob.num_days):
            req = prob.required_fte[job][d]
            act = sol.fte[job, d]
            if req == 0:
                cls = "closed"
            elif (job, d) in vio_days:
                cls = "ng"
            elif act < req + WARN_MARGIN:
                cls = "warn"
            else:
                cls = ""
            cells.append({"required": f"{req:.1f}", "actual": f"{act:.1f}",
                          "cls": cls})
        fte_rows.append({"job_label": job, "cells": cells})

    violations = []
    for v in sorted(sol.violations, key=lambda v: (v.day, v.job)):
        date = first + dt.timedelta(days=v.day)
        hint = ("応援職員の手配、または当日の利用者受入数の調整"
                if v.kind == "fte" else "出勤者を1名以上確保する")
        violations.append({
            "date_label": f"{date.month}/{date.day}（{WD[date.weekday()]}）",
            "job_label": v.job, "kind_label": KIND_LABEL[v.kind],
            "required": f"{v.required:.1f}", "actual": f"{v.actual:.1f}",
            "shortage": f"{v.shortage:.1f}", "hint": hint,
        })

    mins = sol.staff_minutes or [0]
    pref_off_total = sum(1 for v in prob.pref_off.values() if v)
    stats = {
        "violation_count": len(sol.violations),
        "pref_off_total": pref_off_total,
        "pref_off_ok": pref_off_total - sol.pref_off_broken,
        "pref_pat_total": len(prob.pref_pattern),
        "pref_pat_ok": len(prob.pref_pattern) - sol.pref_pattern_broken,
        "spread_hours": round((max(mins) - min(mins)) / 60, 1),
    }

    patterns = [{"code": "休" if k == REST else p.name[:1], "name": p.name,
                 "hours": round(p.work_minutes / 60, 1) if p.work_minutes else None}
                for k, p in enumerate(prob.patterns)]

    return {
        "office": {"name": office_name},
        # テンプレートのフォームが対象年月を送るため、素の値も渡す
        "year": year, "month": month,
        "schedule": {
            "id": schedule_id, "month_label": f"{year}年{month}月",
            "status": status,
            "status_label": "確定・公開済み" if status == "published" else "作成中",
            "solver_status": sol.status,
            "solve_seconds": f"{sol.solve_seconds:.2f}",
            "objective": sol.objective,
        },
        "days": days, "groups": groups, "fte_rows": fte_rows,
        "violations": violations, "stats": stats, "patterns": patterns,
        # 狭い画面向けの日別サマリー。
        # 31日×職員数の表はスマートフォンでは読めないため、
        # 「その日は基準を満たしているか」だけを縦に並べたものを別に用意する。
        "day_summary": _day_summary(days, fte_rows),
    }


def _day_summary(days: list[dict[str, Any]],
                 fte_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """日ごとの充足状況。狭い画面ではこれを縦に並べる。"""
    out = []
    for d, day in enumerate(days):
        jobs = []
        worst = "ok"
        for row in fte_rows:
            cell = row["cells"][d]
            if cell["cls"] == "closed":
                continue
            jobs.append({"job_label": row["job_label"],
                         "required": cell["required"],
                         "actual": cell["actual"], "cls": cell["cls"]})
            if cell["cls"] == "ng":
                worst = "ng"
            elif cell["cls"] == "warn" and worst == "ok":
                worst = "warn"
        closed = not jobs
        out.append({**day, "jobs": jobs, "closed": closed,
                    "state": "closed" if closed else worst})
    return out
