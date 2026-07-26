"""マスタ管理。職員と勤務区分を1画面のタブで扱う。"""
from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import audit
from app import repository as repo
from app.deps import AdminDep
from app.jobs import JOB_CODE, JOB_LABEL
from app.templating import render

router = APIRouter(prefix="/masters", tags=["masters"])


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def show(request: Request, user: AdminDep) -> Response:
    today = dt.date.today().isoformat()
    return render(request, "masters.html", {
        "office": repo.get_office(user.office_id),
        "staff": repo.list_staff(user.office_id, today, today),
        "employment_types": repo.list_employment_types(user.office_id),
        "patterns": repo.list_shift_patterns(user.office_id),
        "job_labels": JOB_LABEL,
        "job_codes": JOB_CODE,
    })


@router.post("/staff", include_in_schema=False)
def add_staff(request: Request, user: AdminDep,
              name: Annotated[str, Form()],
              job_type: Annotated[str, Form()],
              employment_type_id: Annotated[int, Form()],
              hired_on: Annotated[str, Form()],
              qualifications: Annotated[str, Form()] = "",
              secondary_job_type: Annotated[str, Form()] = "",
              secondary_ratio: Annotated[float, Form()] = 0.0) -> Response:
    name = name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "氏名を入力してください。")
    if job_type not in JOB_LABEL:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "職種が不正です。")

    sec = secondary_job_type.strip() or None
    if sec is not None:
        if sec not in JOB_LABEL:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "兼務先の職種が不正です。")
        if sec == job_type:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "兼務先には主たる職種と別の職種を指定してください。")
        if not 0 < secondary_ratio < 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "兼務の従事割合は0より大きく1より小さい値で指定してください。")
    else:
        secondary_ratio = 0.0

    try:
        hired = dt.date.fromisoformat(hired_on)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "入職日の形式が不正です。") from e

    quals = [q.strip() for q in qualifications.replace("、", ",").split(",")
             if q.strip()]

    staff_id = repo.insert_staff(user.office_id, name=name, job_type=job_type,
                                 employment_type_id=employment_type_id,
                                 qualifications=quals, secondary_job_type=sec,
                                 secondary_ratio=secondary_ratio,
                                 hired_on=hired.isoformat())
    audit.record_user(
        request, user, audit.STAFF_ADD,
        f"{name} を職種 {job_type} で追加した"
        + (f"（{sec} を {secondary_ratio:.2f} で兼務）" if sec else ""),
        target_type="staff", target_id=staff_id)
    return RedirectResponse("/masters", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/staff/{staff_id}/retire", include_in_schema=False)
def retire(request: Request, user: AdminDep, staff_id: int,
           retired_on: Annotated[str, Form()]) -> Response:
    """退職日を設定する。行は削除しない。

    過去のシフト表と勤務形態一覧表を再現できなくなるため、
    実地指導では過去分の適合性が問われる。
    """
    try:
        date = dt.date.fromisoformat(retired_on)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "退職日の形式が不正です。") from e
    repo.retire_staff(user.office_id, staff_id, date.isoformat())
    audit.record_user(request, user, audit.STAFF_RETIRE,
                      f"職員ID {staff_id} の退職日を {date.isoformat()} に設定した",
                      target_type="staff", target_id=staff_id)
    return RedirectResponse("/masters", status_code=status.HTTP_303_SEE_OTHER)
