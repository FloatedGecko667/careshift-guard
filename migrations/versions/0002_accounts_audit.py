# -*- coding: utf-8 -*-
"""アカウント管理・パスワード再設定・監査ログを追加する。

0001 は db/schema.sql をそのまま適用する構成だが、
既に運用に入っている環境へは ALTER で差分を当てる必要がある。
schema.sql（唯一の正）とこのリビジョンの内容は一致させること。

追加するもの
  ・users.session_epoch     … 署名Cookieを世代で失効させるため
  ・users.staff_id の外部キーと一意索引
  ・password_reset_tokens   … 単回使用・期限つき。平文は保存しない
  ・audit_logs              … 追記専用。更新・削除・切り詰めをトリガで拒否

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


UPGRADE = r"""
-- ------------------------------------------------------------------ users
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS session_epoch integer NOT NULL DEFAULT 1;

ALTER TABLE users
    DROP CONSTRAINT IF EXISTS ck_users_epoch;
ALTER TABLE users
    ADD CONSTRAINT ck_users_epoch CHECK (session_epoch > 0);

ALTER TABLE users
    DROP CONSTRAINT IF EXISTS fk_users_staff;
ALTER TABLE users
    ADD CONSTRAINT fk_users_staff FOREIGN KEY (staff_id)
        REFERENCES staff (staff_id) ON DELETE SET NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_users_staff
    ON users (staff_id) WHERE staff_id IS NOT NULL;

-- ------------------------------------------------- password_reset_tokens
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token_id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id           bigint      NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    token_hash        text        NOT NULL,
    expires_at        timestamptz NOT NULL,
    used_at           timestamptz,
    issued_by_user_id bigint      REFERENCES users (user_id) ON DELETE SET NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_prt_hash    CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_prt_expires CHECK (expires_at > created_at)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_prt_hash
    ON password_reset_tokens (token_hash);
CREATE INDEX IF NOT EXISTS ix_prt_user
    ON password_reset_tokens (user_id, used_at);

-- ------------------------------------------------------------- audit_logs
CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    office_id     bigint      NOT NULL REFERENCES offices (office_id) ON DELETE CASCADE,
    actor_user_id bigint      REFERENCES users (user_id) ON DELETE SET NULL,
    actor_email   text        NOT NULL,
    action        text        NOT NULL,
    target_type   text,
    target_id     bigint,
    summary       text        NOT NULL,
    ip            inet,
    user_agent    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_audit_action CHECK (action ~ '^[a-z_]+\.[a-z_]+$'),
    CONSTRAINT ck_audit_target CHECK (
        (target_type IS NULL AND target_id IS NULL)
     OR (target_type IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_audit_office_time
    ON audit_logs (office_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_action
    ON audit_logs (office_id, action, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_target
    ON audit_logs (office_id, target_type, target_id);

CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION
        'audit_logs は追記専用です（試行された操作: %）', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$fn$;

DROP TRIGGER IF EXISTS trg_audit_logs_no_change ON audit_logs;
CREATE TRIGGER trg_audit_logs_no_change
    BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only();

DROP TRIGGER IF EXISTS trg_audit_logs_no_truncate ON audit_logs;
CREATE TRIGGER trg_audit_logs_no_truncate
    BEFORE TRUNCATE ON audit_logs
    FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_append_only();
"""

# 監査ログは戻せない。down で消すと、切り戻しのついでに証跡が
# 失われる。テーブルは残し、追加した列と制約だけを戻す。
DOWNGRADE = """
DROP INDEX IF EXISTS ux_users_staff;
ALTER TABLE users DROP CONSTRAINT IF EXISTS fk_users_staff;
ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_epoch;
ALTER TABLE users DROP COLUMN IF EXISTS session_epoch;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
