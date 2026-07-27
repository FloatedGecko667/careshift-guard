"""シフト表。生成・表示・手修正・確定・帳票出力。

この画面が製品の中核である。
常勤換算と基準判定をシフト表と同一画面に表示する。
別画面に分けると管理者が気づかず、減算を防ぐ目的を果たせない。
"""
from __future__ import annotations

import datetime as dt
import io
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse

from app import audit, service
from app import repository as repo
from app.config import get_settings
from app.deps import AdminDep, CurrentUserDep
from app.excel import build_workbook
from app.presenter import make_context
from app.solver import solve
from app.templating import render

router = APIRouter(prefix="/schedules", tags=["schedules"])

XLSX_MEDIA = ("application/vnd.openxmlformats-officedocument"
              ".spreadsheetml.sheet")


def _target_month(year: int | None, month: int | None) -> tuple[int, int]:
    """未指定なら翌月を対象とする。シフトは前月に組むため。"""
    if year and month:
        if not (2000 <= year <= 2100 and 1 <= month <= 12):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "対象年月が不正です。")
        return year, month
    today = dt.date.today()
    nxt = dt.date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    return nxt.year, nxt.month


def _load(office_id: int, year: int, month: int, avg_users: float | None):
    """シフト生成に必要な一式をデータベースから読む。"""
    office = repo.get_office(office_id)
    if office is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "事業所が見つかりません。")

    first, nd = service.month_range(year, month)
    last = first + dt.timedelta(days=nd - 1)

    schedule = repo.get_schedule(office_id, first.isoformat())
    if avg_users is None:
        avg_users = float(schedule["avg_expected_users"]) if schedule else 0.0

    staff_rows = repo.list_staff(office_id, first.isoformat(), last.isoformat())
    patterns = repo.list_shift_patterns(office_id)
    rules = repo.list_staffing_rules(office["service_type"], first.isoformat())
    requests = repo.list_shift_requests(office_id, first.isoformat(),
                                       last.isoformat())
    return office, schedule, staff_rows, patterns, rules, requests, avg_users


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def show(request: Request, user: CurrentUserDep,
         year: Annotated[int | None, Query()] = None,
         month: Annotated[int | None, Query()] = None) -> Response:
    year, month = _target_month(year, month)
    office, schedule, staff_rows, patterns, rules, reqs, avg = _load(
        user.office_id, year, month, None)

    if not staff_rows or not patterns:
        return render(request, "schedule_empty.html", {
            "office": office, "year": year, "month": month,
            "reason": "職員または勤務区分が登録されていません。"
                      "まず「マスタ管理」から登録してください。"})

    if schedule is None or schedule.get("generated_at") is None:
        return render(request, "schedule_empty.html", {
            "office": office, "year": year, "month": month,
            "avg_expected_users": avg,
            "reason": f"{year}年{month}月のシフトはまだ生成されていません。"})

    # 保存済みの明細から解を復元して表示する。
    # 再求解しないため、画面を開くだけで内容が変わることがない。
    prob, mapping = service.build_problem(
        office, staff_rows, patterns, rules, reqs, year, month, avg)
    sol = _restore(prob, mapping, schedule)

    ctx = make_context(prob, sol, year=year, month=month,
                      office_name=office["name"],
                      schedule_id=schedule["schedule_id"],
                      status=schedule["status"],
                      staff_ids=mapping.staff_ids)
    ctx["is_admin"] = user.is_admin
    ctx["avg_expected_users"] = avg
    # 手修正フォームの選択肢。公休も選べるようにする
    ctx["pattern_options"] = patterns
    # 狭い画面では31日×職員数の表を読めない。
    # 職員には自分の行だけを縦に並べた一覧を出す。
    ctx["my_staff_id"] = user.staff_id
    ctx["my_row"] = _find_row(ctx["groups"], user.staff_id)
    return render(request, "schedule.html", ctx)


def _find_row(groups: list[dict[str, Any]],
              staff_id: int | None) -> dict[str, Any] | None:
    """ログイン中の職員に対応する行を探す。

    管理者アカウントは職員に紐づいていないことがある（staff_id が None）。
    その場合は None を返し、画面側は日別サマリーを出す。
    """
    if staff_id is None:
        return None
    for g in groups:
        for row in g["rows"]:
            if int(row["id"]) == int(staff_id):
                return {**row, "job_label": g["job_label"]}
    return None


def _restore(prob, mapping, schedule):
    """保存済みの明細から Solution 相当を組み立てる。"""
    from app.solver import Solution, Violation

    entries = repo.list_entries(schedule["schedule_id"])
    assign: dict[tuple[int, int], int] = {}
    manual: set[tuple[int, int]] = set()
    for e in entries:
        i = mapping.staff_index.get(int(e["staff_id"]))
        if i is None:
            continue
        d = (service._as_date(e["target_date"]) - mapping.first_date).days
        if 0 <= d < prob.num_days:
            assign[i, d] = mapping.pattern_index[int(e["shift_pattern_id"])]
            if e["is_manual"]:
                manual.add((i, d))

    # 欠けている組み合わせは公休として扱う（職員追加直後など）
    for i in range(len(prob.staff)):
        for d in range(prob.num_days):
            assign.setdefault((i, d), 0)

    h = [p.work_minutes for p in prob.patterns]
    fte = {}
    for job in prob.required_fte:
        for d in range(prob.num_days):
            mins = sum(h[assign[i, d]] * st.weight_for(job)
                       for i, st in enumerate(prob.staff))
            fte[job, d] = round(mins / prob.fulltime_day_minutes, 2)

    violations = [
        Violation(day=(service._as_date(v["target_date"])
                       - mapping.first_date).days,
                  job=v["job_type"], kind=v["kind"],
                  required=float(v["required"]), actual=float(v["actual"]))
        for v in repo.list_violations(schedule["schedule_id"])]

    staff_minutes = [sum(h[assign[i, d]] for d in range(prob.num_days))
                     for i in range(len(prob.staff))]

    return Solution(
        status=schedule.get("solver_status") or "RESTORED",
        solve_seconds=float(schedule.get("solve_seconds") or 0),
        assign=assign, violations=violations, fte=fte,
        pref_off_broken=sum(1 for (i, d), on in prob.pref_off.items()
                            if on and assign[i, d] != 0),
        pref_pattern_broken=sum(1 for (i, d), p in prob.pref_pattern.items()
                                if assign[i, d] != p),
        staff_minutes=staff_minutes,
        objective=int(schedule.get("objective_value") or 0))


@router.post("/generate", include_in_schema=False)
def generate(request: Request, user: AdminDep,
             year: Annotated[int, Form()],
             month: Annotated[int, Form()],
             avg_expected_users: Annotated[float, Form()],
             keep_manual: Annotated[bool, Form()] = True) -> Response:
    """シフトを自動生成して保存する。

    求解は同期実行する。上限は設定値（既定10秒）であり、
    上限に達した場合もその時点の最良解が返る。
    """
    if not 0 <= avg_expected_users <= 1000:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "平均利用者数が不正です。")
    year, month = _target_month(year, month)
    s = get_settings()

    office, _, staff_rows, patterns, rules, reqs, _ = _load(
        user.office_id, year, month, avg_expected_users)
    if not staff_rows or not patterns:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "職員と勤務区分を先に登録してください。")

    first, _ = service.month_range(year, month)
    schedule_id = repo.upsert_schedule(user.office_id, first.isoformat(),
                                       avg_expected_users)

    prob, mapping = service.build_problem(
        office, staff_rows, patterns, rules, reqs, year, month,
        avg_expected_users)
    sol = solve(prob, time_limit=s.solver_time_limit, workers=s.solver_workers)

    keep: dict[tuple[int, str], int] = {}
    if keep_manual:
        keep = {(int(e["staff_id"]), service._as_date(e["target_date"]).isoformat()):
                int(e["shift_pattern_id"])
                for e in repo.list_entries(schedule_id) if e["is_manual"]}

    entries, violations = service.solution_rows(
        schedule_id, prob, sol, mapping, keep_manual=keep)
    repo.save_solution(user.office_id, schedule_id, entries, violations,
                       sol.status, sol.objective, sol.solve_seconds)

    audit.record_user(
        request, user, audit.SCHEDULE_GENERATE,
        f"{year}年{month}月を生成した（平均利用者 {avg_expected_users:.1f}名・"
        f"求解 {sol.status}・{sol.solve_seconds:.2f}秒・"
        f"違反 {len(violations)}件・手修正の保持 "
        f"{'あり' if keep_manual else 'なし'}）",
        target_type="schedule", target_id=schedule_id)

    return RedirectResponse(f"/schedules?year={year}&month={month}",
                            status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{schedule_id}/cell", include_in_schema=False)
def edit_cell(request: Request, user: AdminDep, schedule_id: int,
              staff_id: Annotated[int, Form()],
              target_date: Annotated[str, Form()],
              shift_pattern_id: Annotated[int, Form()]) -> Response:
    """1セルの勤務区分を手で変更する。変更後は基準を再判定する。"""
    try:
        date = dt.date.fromisoformat(target_date)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "日付の形式が不正です。") from e

    repo.update_entry(user.office_id, schedule_id, staff_id,
                      date.isoformat(), shift_pattern_id)
    audit.record_user(
        request, user, audit.SCHEDULE_EDIT,
        f"職員ID {staff_id} の {date.isoformat()} を勤務区分ID "
        f"{shift_pattern_id} に手修正した",
        target_type="schedule", target_id=schedule_id)
    return RedirectResponse(f"/schedules?year={date.year}&month={date.month}",
                            status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{schedule_id}/publish", include_in_schema=False)
def publish(request: Request, user: AdminDep, schedule_id: int,
            year: Annotated[int, Form()],
            month: Annotated[int, Form()]) -> Response:
    """確定して職員に公開する。

    違反が1件でもあれば確定できない。判定は SQL 側で行っており、
    経路が増えてもこの条件は抜けない。
    """
    if not repo.publish_schedule(user.office_id, schedule_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "人員配置基準の違反が解消されていないため確定できません。")
    # 実地指導で最初に問われるのがこの記録である。
    audit.record_user(request, user, audit.SCHEDULE_PUBLISH,
                      f"{year}年{month}月のシフトを確定して公開した"
                      "（違反0件を SQL 側で確認済み）",
                      target_type="schedule", target_id=schedule_id)
    return RedirectResponse(f"/schedules?year={year}&month={month}",
                            status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{schedule_id}/export.xlsx", include_in_schema=False)
def export_xlsx(request: Request, user: CurrentUserDep, schedule_id: int,
                year: Annotated[int, Query()],
                month: Annotated[int, Query()]) -> Response:
    """勤務形態一覧表を Excel で出力する。実地指導への提出資料。"""
    office, schedule, staff_rows, patterns, rules, reqs, avg = _load(
        user.office_id, year, month, None)
    if schedule is None or schedule["schedule_id"] != schedule_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "シフト表が見つかりません。")

    prob, mapping = service.build_problem(
        office, staff_rows, patterns, rules, reqs, year, month, avg)
    sol = _restore(prob, mapping, schedule)

    audit.record_user(request, user, audit.SCHEDULE_EXPORT,
                      f"{year}年{month}月の勤務形態一覧表を出力した",
                      target_type="schedule", target_id=schedule_id)

    wb = build_workbook(prob, sol, year=year, month=month,
                        office_name=office["name"], avg_users=avg)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    name = f"kinmu_keitai_{year}{month:02d}.xlsx"
    return StreamingResponse(
        buf, media_type=XLSX_MEDIA,
        headers={"Content-Disposition": f'attachment; filename="{name}"'})
