"""テンプレート描画の共通処理。

Jinja2Templates を各ルーターで個別に作らず、ここに集約する。
ログイン利用者の情報を毎回コンテキストへ入れる手間も省く。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.security import SESSION_COOKIE, read_session

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def render(request: Request, name: str,
           context: dict[str, Any] | None = None,
           status_code: int = 200) -> HTMLResponse:
    ctx: dict[str, Any] = {
        "app_env": get_settings().app_env,
        "current_user": read_session(request.cookies.get(SESSION_COOKIE)),
    }
    if context:
        ctx.update(context)
    return templates.TemplateResponse(
        request=request, name=name, context=ctx, status_code=status_code)
