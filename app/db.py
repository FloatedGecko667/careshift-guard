"""データベース接続。

SQLAlchemy は ORM ではなく Core として使う。
スキーマ定義は db/schema.sql を唯一の正とし、
Python 側にモデルを二重定義しない方針である。
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection

from app.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    s = get_settings()
    return create_engine(
        s.database_url,
        # 事業所数が増えても接続数は抑える。求解が長い処理は接続を保持しない設計。
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,      # 切断済み接続を掴まないようにする
        pool_recycle=1800,
        future=True,
    )


@contextmanager
def connection() -> Iterator[Connection]:
    """トランザクション境界つきの接続を返す。

    with connection() as conn:  の中で例外が出れば自動でロールバックする。
    """
    engine = get_engine()
    with engine.begin() as conn:
        yield conn


def ping() -> bool:
    """接続確認。ヘルスチェックから呼ぶ。"""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
