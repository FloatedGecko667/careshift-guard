# -*- coding: utf-8 -*-
"""初期スキーマ。db/schema.sql をそのまま適用する。

スキーマ定義を SQLAlchemy のモデルと SQL の2箇所に書くと、
必ずどちらかが古くなる。db/schema.sql を唯一の正とし、
Alembic はその適用と履歴管理だけを担う。

以降の改定（介護報酬改定によるルール追加など）は
通常のリビジョンとして ALTER 文を書き足していく。

Revision ID: 0001
Revises: None
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

# migrations/versions/ から見たリポジトリルート
ROOT = Path(__file__).resolve().parents[2]


def _sql(name: str) -> str:
    return (ROOT / "db" / name).read_text(encoding="utf-8")


def upgrade() -> None:
    # schema.sql は自身で BEGIN / COMMIT を含むため、
    # Alembic のトランザクション内で実行できるよう取り除く。
    body = _sql("schema.sql")
    body = body.replace("BEGIN;", "").replace("COMMIT;", "")
    op.execute(body)


def downgrade() -> None:
    # 依存関係の逆順に落とす
    for obj, kind in (
        ("v_daily_fte", "VIEW"),
        ("violations", "TABLE"),
        ("schedule_entries", "TABLE"),
        ("schedules", "TABLE"),
        ("shift_requests", "TABLE"),
        ("staffing_rules", "TABLE"),
        ("shift_patterns", "TABLE"),
        ("staff", "TABLE"),
        ("employment_types", "TABLE"),
        ("users", "TABLE"),
        ("offices", "TABLE"),
    ):
        op.execute(f"DROP {kind} IF EXISTS {obj} CASCADE")
