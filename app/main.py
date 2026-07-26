"""CareShift Guard  FastAPI エントリポイント。

サーバサイドレンダリング方式。画面は Jinja2 で組み、
部分更新は htmx で行う。JavaScript のビルド工程を持たない。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import db
from app.deps import LoginRequired, login_redirect
from app.templating import render

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="CareShift Guard",
    description="介護事業所向け 人員配置基準チェック内蔵 シフト自動作成クラウド",
    version="0.1.0",
    # 業務システムのため API ドキュメントは既定で公開しない
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/healthz", include_in_schema=False)
def healthz() -> JSONResponse:
    """コンテナのヘルスチェック用。

    DB 未接続でもプロセス自体は生きているため 200 を返し、
    DB の状態は本文で示す。ロードバランサから外すかは別途判断する。
    """
    return JSONResponse({"status": "ok", "database": "up" if db.ping() else "down"})


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request) -> HTMLResponse:
    return render(request, "index.html")


@app.exception_handler(LoginRequired)
def on_login_required(request: Request, exc: LoginRequired) -> Response:
    """未ログインでの保護経路アクセスはログイン画面へ誘導する。

    401 を返して終わるのではなく、利用者が次に取る行動を示す。
    """
    return login_redirect(request)


def register_routers() -> None:
    """ルーターを登録する。

    段階的に実装するため、未実装のものは import に失敗しうる。
    その場合はアプリ全体を落とさず、当該経路のみ無効にする。
    """
    from importlib import import_module

    for name in ("auth", "accounts", "masters", "requests", "schedules"):
        try:
            module = import_module(f"app.routers.{name}")
        except ModuleNotFoundError:
            continue
        router = getattr(module, "router", None)
        if router is not None:
            app.include_router(router)


register_routers()
