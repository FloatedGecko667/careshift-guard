"""設定。値はすべて環境変数から読む。

秘密情報をソースコードに書かないこと。
既定値は開発用であり、本番では必ず環境変数で上書きする。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

# 本番で使ってはいけない既定値。起動時に検出して失敗させる。
INSECURE_DEFAULTS = frozenset({
    "change_me_in_production",
    "",
})


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret_key: str
    app_env: str
    solver_time_limit: float
    solver_workers: int

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings(
        database_url=os.environ.get(
            "DATABASE_URL",
            "postgresql+pg8000://careshift:careshift@localhost:5432/careshift"),
        secret_key=os.environ.get("SECRET_KEY", "dev-only-insecure-key"),
        app_env=os.environ.get("APP_ENV", "development"),
        solver_time_limit=float(os.environ.get("SOLVER_TIME_LIMIT", "10")),
        # 求解は CPU を集中的に使う。割り当て OCPU 数を超えて増やしても速くならない。
        solver_workers=int(os.environ.get("SOLVER_WORKERS", "2")),
    )

    if s.is_production and s.secret_key in INSECURE_DEFAULTS:
        raise RuntimeError(
            "SECRET_KEY が既定値のままです。本番では必ず変更してください。"
            "  openssl rand -hex 32")
    if s.is_production and "change_me_in_production" in s.database_url:
        raise RuntimeError(
            "DATABASE_URL のパスワードが既定値のままです。本番では必ず変更してください。")
    if not 1 <= s.solver_time_limit <= 300:
        raise RuntimeError("SOLVER_TIME_LIMIT は 1〜300 秒の範囲で指定してください。")
    if not 1 <= s.solver_workers <= 64:
        raise RuntimeError("SOLVER_WORKERS は 1〜64 の範囲で指定してください。")
    return s
