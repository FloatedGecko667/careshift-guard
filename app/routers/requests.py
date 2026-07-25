"""希望シフト入力。職員がスマートフォンから登録する。

htmx が読み込まれている場合は該当の1日分だけを差し替える。
読み込まれていない場合は通常のフォーム送信となり、
全画面が再描画されるだけで機能は失われない（段階的強化）。
"""
from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import repository as repo
from app import service
from app.deps import CurrentUserDep
from app.templating import render

router = APIRouter(prefix="/requests", tags=["requests"])

VALID_TYPES = {"off", "pattern", "unavailable"}
WD = "月火水木金土日"


def _resolve_staff(user, staff_id: int | None) -> int:
    """対象職員を決める。

    職員は自分の希望しか登録できない。管理者は代理入力できる。
    引数の staff_id を無条件に信用すると、他人の希望を書き換えられる。
    """
    if user.is_admin and staff_id is not None:
        return staff_id
    if user.staff_id is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "この利用者には職員情報が紐づいていないため希望を登録できません。")
    return user.staff_id


def _validate_month(year: int, month: int) -> None:
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "対象年月が不正です。")


def _parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "日付の形式が不正です。") from e


def _day_list(office: dict[str, Any] | None, user, target: int | None,
              year: int, month: int) -> list[dict[str, Any]]:
    first, nd = service.month_range(year, month)
    last = first + dt.timedelta(days=nd - 1)

    rows = repo.list_shift_requests(user.office_id, first.isoformat(),
                                   last.isoformat())
    mine = {service._as_date(r["target_date"]).isoformat(): r
            for r in rows if target and int(r["staff_id"]) == target}

    closed = service.closed_days(office, first, nd) if office else [False] * nd
    out = []
    for d in range(nd):
        date = first + dt.timedelta(days=d)
        iso = date.isoformat()
        out.append({"date": iso, "n": date.day, "weekday": WD[date.weekday()],
                    "closed": closed[d], "request": mine.get(iso)})
    return out


def _one_day(user, target: int, date: dt.date) -> dict[str, Any]:
    office = repo.get_office(user.office_id)
    days = _day_list(office, user, target, date.year, date.month)
    for d in days:
        if d["date"] == date.isoformat():
            return d
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "対象日が範囲外です。")


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def show(request: Request, user: CurrentUserDep,
         year: Annotated[int | None, Query()] = None,
         month: Annotated[int | None, Query()] = None,
         staff_id: Annotated[int | None, Query()] = None) -> Response:
    today = dt.date.today()
    if not (year and month):
        # シフトは前月に組むため、既定は翌月とする
        nxt = dt.date(today.year + (today.month == 12), today.month % 12 + 1, 1)
        year, month = nxt.year, nxt.month
    _validate_month(year, month)

    office = repo.get_office(user.office_id)
    target = staff_id if user.is_admin else user.staff_id
    first, nd = service.month_range(year, month)

    return render(request, "requests.html", {
        "office": office, "year": year, "month": month,
        "days": _day_list(office, user, target, year, month),
        "patterns": [p for p in repo.list_shift_patterns(user.office_id)
                     if not p["is_rest"]],
        "target_staff_id": target,
        "is_admin": user.is_admin,
        "staff": repo.list_staff(
            user.office_id, first.isoformat(),
            (first + dt.timedelta(days=nd - 1)).isoformat())
        if user.is_admin else [],
    })


def _respond(request: Request, user, target: int, date: dt.date) -> Response:
    """htmx なら1日分の断片、通常要求なら画面全体へ戻す。"""
    if request.headers.get("HX-Request") == "true":
        return render(request, "partials/request_row.html", {
            "d": _one_day(user, target, date),
            "is_admin": user.is_admin, "target_staff_id": target})
    query = f"?year={date.year}&month={date.month}"
    if user.is_admin:
        query += f"&staff_id={target}"
    return RedirectResponse("/requests" + query,
                            status_code=status.HTTP_303_SEE_OTHER)


@router.post("", include_in_schema=False)
def upsert(request: Request, user: CurrentUserDep,
           target_date: Annotated[str, Form()],
           request_type: Annotated[str, Form()],
           shift_pattern_id: Annotated[int | None, Form()] = None,
           note: Annotated[str, Form()] = "",
           staff_id: Annotated[int | None, Form()] = None) -> Response:
    if request_type not in VALID_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "希望の種別が不正です。")
    if (request_type == "pattern") != (shift_pattern_id is not None):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "勤務区分の希望には区分の指定が必要です。それ以外では指定できません。")

    date = _parse_date(target_date)
    sid = _resolve_staff(user, staff_id)
    repo.upsert_shift_request(user.office_id, sid, date.isoformat(),
                              request_type, shift_pattern_id,
                              note.strip() or None)
    return _respond(request, user, sid, date)


@router.post("/delete", include_in_schema=False)
def delete(request: Request, user: CurrentUserDep,
           target_date: Annotated[str, Form()],
           staff_id: Annotated[int | None, Form()] = None) -> Response:
    date = _parse_date(target_date)
    sid = _resolve_staff(user, staff_id)
    repo.delete_shift_request(user.office_id, sid, date.isoformat())
    return _respond(request, user, sid, date)
