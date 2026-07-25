"""Alembic の実行環境。

接続文字列は環境変数 DATABASE_URL から読む。
alembic.ini に平文で書くとリポジトリに秘密情報が入るため。
"""
from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

url = os.environ.get("DATABASE_URL")
if not url:
    raise RuntimeError(
        "環境変数 DATABASE_URL が設定されていません。"
        ".env.example を参考に .env を作成してください。")
config.set_main_option("sqlalchemy.url", url)

# スキーマは db/schema.sql を正とするため、
# SQLAlchemy の MetaData による自動生成（autogenerate）は使わない。
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata,
                      literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
