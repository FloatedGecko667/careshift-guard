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


def test_希望シフトは同一日で上書きされる():
    """職員が入力し直したときに重複行を作らない。"""
    sql = repo.all_sql()["SQL_UPSERT_SHIFT_REQUEST"]
    assert "ON CONFLICT (staff_id, target_date)" in sql
    assert "DO UPDATE" in sql
