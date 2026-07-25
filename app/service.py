"""業務ロジック。データベースの行と最適化エンジンの間を変換する。

ここは意図的にデータベースへ触らない純粋関数だけで構成している。
引数は素の辞書とリストであり、戻り値も同様である。
こうしておくと、PostgreSQL を起動しなくても全ロジックを試験できる。
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Any

from app.jobs import FTE_TARGET_JOBS, label
from app.solver import REST, Problem, ShiftPattern, Solution, Staff


# =====================================================================
# 対象月
# =====================================================================
def month_range(year: int, month: int) -> tuple[dt.date, int]:
    """月初日と日数を返す。"""
    first = dt.date(year, month, 1)
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    return first, (nxt - first).days


# =====================================================================
# 人員配置基準ルールの適用
# =====================================================================
def required_fte_for(rule: dict[str, Any], avg_users: float) -> float:
    """1件のルールから必要常勤換算数を求める。

    per_users_step は「閾値を超える部分の数を step_users で除して得た数に
    base_fte を加えた数」である。除して得た数の端数は切り上げない。
    切り上げると過剰配置を要求し、切り捨てると基準未達を見逃す。
    """
    base = float(rule["base_fte"])
    if rule["formula_type"] == "constant":
        return round(base, 2)

    thr = float(rule["threshold_users"])
    step_u = float(rule["step_users"])
    step_f = float(rule["step_fte"])
    excess = max(0.0, avg_users - thr)
    return round(base + excess / step_u * step_f, 2)


def build_requirements(rules: list[dict[str, Any]], avg_users: float,
                       num_days: int, closed: list[bool]):
    """職種別・日別の必要常勤換算数と必要実人数を組み立てる。

    ルールに現れない職種は判定対象にしない。
    休業日は基準の適用外とし、0 を入れる。
    """
    required_fte: dict[str, list[float]] = {}
    required_head: dict[str, list[int]] = {}

    for rule in rules:
        job = label(rule["job_type"])
        if job not in FTE_TARGET_JOBS:
            # 管理者など常勤換算の対象外はここで除く
            continue
        fte = required_fte_for(rule, avg_users)
        head = int(rule["min_headcount"])
        required_fte[job] = [0.0 if closed[d] else fte for d in range(num_days)]
        required_head[job] = [0 if closed[d] else head for d in range(num_days)]

    return required_fte, required_head


def closed_days(office: dict[str, Any], first: dt.date, num_days: int) -> list[bool]:
    """休業曜日から、対象月の日ごとの休業判定を作る。"""
    weekdays = set(office.get("closed_weekdays") or [])
    return [((first + dt.timedelta(days=d)).weekday() in weekdays)
            for d in range(num_days)]


# =====================================================================
# 問題インスタンスの組み立て
# =====================================================================
@dataclass
class Mapping:
    """最適化エンジンの添字とデータベースの ID の対応。"""
    staff_ids: list[int]                 # 添字 → staff_id
    pattern_ids: list[int]               # 添字 → shift_pattern_id
    staff_index: dict[int, int]          # staff_id → 添字
    pattern_index: dict[int, int]        # shift_pattern_id → 添字
    first_date: dt.date


def build_problem(office: dict[str, Any], staff_rows: list[dict[str, Any]],
                  pattern_rows: list[dict[str, Any]], rules: list[dict[str, Any]],
                  request_rows: list[dict[str, Any]], year: int, month: int,
                  avg_users: float) -> tuple[Problem, Mapping]:
    first, nd = month_range(year, month)
    last = first + dt.timedelta(days=nd - 1)

    # --- 勤務区分。公休を必ず添字0に置く（solver の前提） ---
    rest = [p for p in pattern_rows if p["is_rest"]]
    work = [p for p in pattern_rows if not p["is_rest"]]
    if not rest:
        raise ValueError("公休の勤務区分が登録されていません。")
    ordered = [rest[0]] + work

    patterns = [ShiftPattern(name=p["name"], start=p["start_minute"],
                             end=p["end_minute"], break_minutes=p["break_minutes"])
                for p in ordered]
    pattern_ids = [int(p["shift_pattern_id"]) for p in ordered]
    pattern_index = {pid: i for i, pid in enumerate(pattern_ids)}

    # --- 職員 ---
    staff: list[Staff] = []
    staff_ids: list[int] = []
    for row in staff_rows:
        hired = _as_date(row["hired_on"])
        retired = _as_date(row["retired_on"]) if row["retired_on"] else None
        available = []
        for d in range(nd):
            day = first + dt.timedelta(days=d)
            available.append(hired <= day and (retired is None or day <= retired))

        ratio = float(row["secondary_ratio"] or 0)
        sec = row["secondary_job_type"]
        staff.append(Staff(
            name=row["name"],
            job=label(row["job_type"]),
            is_fulltime=bool(row["is_fulltime"]),
            weekly_minutes=int(row["weekly_minutes"]),
            available=available,
            qualifications=list(row.get("qualifications") or []),
            secondary_job=label(sec) if sec else None,
            secondary_ratio=ratio if sec else 0.0))
        staff_ids.append(int(row["staff_id"]))

    staff_index = {sid: i for i, sid in enumerate(staff_ids)}

    # --- 基準 ---
    closed = closed_days(office, first, nd)
    required_fte, required_head = build_requirements(rules, avg_users, nd, closed)

    # --- 勤務希望 ---
    pref_off: dict[tuple[int, int], bool] = {}
    pref_pattern: dict[tuple[int, int], int] = {}
    for row in request_rows:
        sid = int(row["staff_id"])
        if sid not in staff_index:
            continue                      # 退職者などの残存データは無視する
        i = staff_index[sid]
        target = _as_date(row["target_date"])
        if not (first <= target <= last):
            continue
        d = (target - first).days

        kind = row["request_type"]
        if kind == "unavailable":
            # 承認済みの休暇などはハード制約として扱う
            staff[i].available[d] = False
        elif kind == "off":
            pref_off[i, d] = True
        elif kind == "pattern":
            pid = row["shift_pattern_id"]
            if pid is not None and int(pid) in pattern_index:
                idx = pattern_index[int(pid)]
                if idx != REST:
                    pref_pattern[i, d] = idx

    prob = Problem(
        num_days=nd, first_weekday=first.weekday(),
        patterns=patterns, staff=staff,
        required_fte=required_fte, required_head=required_head,
        pref_off=pref_off, pref_pattern=pref_pattern,
        fulltime_day_minutes=int(office["fulltime_day_minutes"]),
        fulltime_week_minutes=int(office["fulltime_week_minutes"]),
        fulltime_month_minutes=int(office["fulltime_month_minutes"]),
        max_weekly_minutes=int(office["max_weekly_minutes"]),
        max_consecutive_days=int(office["max_consecutive_days"]),
        min_rest_days=int(office["min_rest_days"]),
        min_interval_minutes=int(office["min_interval_minutes"]),
        closed=closed)

    mapping = Mapping(staff_ids=staff_ids, pattern_ids=pattern_ids,
                      staff_index=staff_index, pattern_index=pattern_index,
                      first_date=first)
    return prob, mapping


def _as_date(value: Any) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


# =====================================================================
# 解をデータベースの行へ変換
# =====================================================================
def solution_rows(schedule_id: int, prob: Problem, sol: Solution,
                  mapping: Mapping,
                  keep_manual: dict[tuple[int, str], int] | None = None):
    """明細行と違反行を作る。

    keep_manual に (staff_id, 日付文字列) → shift_pattern_id を渡すと、
    管理者が手で直した明細をその内容で維持する。
    再生成のたびに手修正が消えると、実務では使えない。
    """
    keep_manual = keep_manual or {}
    entries: list[dict[str, Any]] = []
    for i, sid in enumerate(mapping.staff_ids):
        for d in range(prob.num_days):
            date = (mapping.first_date + dt.timedelta(days=d)).isoformat()
            manual = keep_manual.get((sid, date))
            entries.append({
                "schedule_id": schedule_id, "staff_id": sid, "target_date": date,
                "shift_pattern_id": manual if manual is not None
                else mapping.pattern_ids[sol.assign[i, d]],
                "is_manual": manual is not None,
            })

    violations = [{
        "schedule_id": schedule_id,
        "target_date": (mapping.first_date + dt.timedelta(days=v.day)).isoformat(),
        "job_type": v.job, "kind": v.kind,
        "required": round(v.required, 2), "actual": round(v.actual, 2),
        "severity": "error",
    } for v in sol.violations]

    return entries, violations


# =====================================================================
# 常勤換算（暦月）
# =====================================================================
def monthly_fte(prob: Problem, sol: Solution) -> dict[str, dict[str, float]]:
    """職種別の暦月の勤務延時間と常勤換算数を返す。

    常勤換算数は小数点以下第2位を切り捨てる。
    """
    h = [p.work_minutes for p in prob.patterns]
    out: dict[str, dict[str, float]] = {}
    for job in FTE_TARGET_JOBS:
        if job not in prob.required_fte:
            continue
        mins = sum(h[sol.assign[i, d]] * st.weight_for(job)
                   for i, st in enumerate(prob.staff)
                   for d in range(prob.num_days))
        out[job] = {
            "minutes": mins,
            "hours": round(mins / 60, 1),
            "fte": math.floor(mins / prob.fulltime_month_minutes * 10) / 10,
            "required": max(prob.required_fte[job]),
        }
    return out
