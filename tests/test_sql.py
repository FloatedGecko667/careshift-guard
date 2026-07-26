"""repository の全SQLを PostgreSQL 本体のパーサで構文検証する。

サンドボックスや CI に PostgreSQL サーバを立てなくても、
libpg_query（PostgreSQL 18 のパーサ実体）で DDL / DML の
構文妥当性を確認できる。タイプミスや構文誤りはここで落ちる。

pglast が入っていない環境では skip する。
"""
from __future__ import annotations

import re

import pytest

from app import repository as repo

pglast = pytest.importorskip("pglast", reason="pglast が無いため構文検証を省略")

# SQLAlchemy の名前付きバインドパラメータ（:name）は PostgreSQL の
# 構文ではないため、検証時は $1 形式のプレースホルダに置き換える。
BIND = re.compile(r":([a-zA-Z_][a-zA-Z0-9_]*)")


def _normalize(sql: str) -> str:
    return BIND.sub("$1", sql)


def test_SQL定数が十分な数ある():
    """集約漏れの検知。ここが極端に減ったら定数化を忘れている。"""
    assert len(repo.all_sql()) >= 20


@pytest.mark.parametrize("name", sorted(repo.all_sql()))
def test_SQLの構文が正しい(name):
    sql = _normalize(repo.all_sql()[name])
    try:
        stmts = pglast.parse_sql(sql)
    except Exception as e:                       # noqa: BLE001
        pytest.fail(f"{name} の構文が不正です: {e}")
    assert len(stmts) == 1, f"{name} は1文であるべき（{len(stmts)}文ある）"


def test_業務クエリはoffice_idで絞り込む():
    """テナント分離の担保。

    office_id を条件に持たないクエリは、他事業所のデータへ
    到達しうる。schedule_id / user_id を鍵にするものは、
    その ID 自体が office_id 経由で取得されるため除外する。
    """
    exempt = {
        # 認証。email が一意でありテナント跨ぎが起きない
        "SQL_FIND_USER_BY_EMAIL",
        "SQL_UPDATE_PASSWORD_HASH",
        "SQL_TOUCH_LAST_LOGIN",
        # user_id 経由。user_id は署名済みセッションから得る
        "SQL_GET_SESSION_STATE",
        "SQL_SET_PASSWORD",
        # token_id / user_id 経由。トークンは発行時に office_id で
        # 絞って作っており（SQL_INSERT_RESET_TOKEN）、
        # 引く側はハッシュ一致という一意条件で特定する
        "SQL_INVALIDATE_RESET_TOKENS",
        "SQL_FIND_VALID_RESET_TOKEN",
        "SQL_CONSUME_RESET_TOKEN",
        # 法令由来の全テナント共通マスタ
        "SQL_LIST_STAFFING_RULES",
        # schedule_id 経由。schedule_id は office_id で絞って取得する
        "SQL_DELETE_ENTRIES",
        "SQL_INSERT_ENTRY",
        "SQL_UPDATE_ENTRY",
        "SQL_LIST_ENTRIES",
        "SQL_DELETE_VIOLATIONS",
        "SQL_INSERT_VIOLATION",
        "SQL_LIST_VIOLATIONS",
        "SQL_DAILY_FTE",
    }
    missing = [name for name, sql in repo.all_sql().items()
               if name not in exempt and "office_id" not in sql]
    assert missing == [], f"office_id で絞り込んでいないクエリ: {missing}"


def test_確定は違反ゼロをSQL側で保証する():
    """アプリ側のチェックだけに頼らない。経路が増えたときに漏れるため。"""
    sql = repo.all_sql()["SQL_PUBLISH_SCHEDULE"]
    assert "NOT EXISTS" in sql
    assert "violations" in sql


def test_監査ログに更新と削除のSQLを持たせない():
    """追記専用であることをアプリ側でも担保する。

    データベース側はトリガで拒否しているが、そもそも
    更新や削除の SQL を用意しないことで、書ける経路を作らない。
    """
    for name, sql in repo.all_sql().items():
        if "audit_logs" not in sql:
            continue
        head = sql.strip().split(None, 1)[0].upper()
        assert head in {"INSERT", "SELECT"}, \
            f"{name} が audit_logs を {head} している"


def test_パスワード再設定トークンは平文で扱わない():
    """SQL 上に現れるのは token_hash だけであること。"""
    for name in ("SQL_INSERT_RESET_TOKEN", "SQL_FIND_VALID_RESET_TOKEN"):
        sql = repo.all_sql()[name]
        assert "token_hash" in sql
        assert "token_plain" not in sql
        assert ":token\n" not in sql and ":token " not in sql


def test_パスワード変更は必ず世代を進める():
    """署名Cookieを失効させる唯一の手段であるため、取り違えを防ぐ。"""
    sql = repo.all_sql()["SQL_SET_PASSWORD"]
    assert "session_epoch = session_epoch + 1" in sql


def test_無効化は世代を進め有効化では進めない():
    """有効化で進めると、無関係な利用者を無用にログアウトさせてしまう。"""
    sql = repo.all_sql()["SQL_SET_USER_ACTIVE"]
    assert "CASE WHEN :is_active THEN 0 ELSE 1 END" in sql


def test_希望シフトは同一日で上書きされる():
    """職員が入力し直したときに重複行を作らない。"""
    sql = repo.all_sql()["SQL_UPSERT_SHIFT_REQUEST"]
    assert "ON CONFLICT (staff_id, target_date)" in sql
    assert "DO UPDATE" in sql
