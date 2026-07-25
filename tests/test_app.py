"""アプリケーションの骨組みのテスト。

データベースには接続できない前提で確認する（サンドボックスに
PostgreSQL サーバを立てられないため）。DB を必要としない経路と、
設定の検証ロジックを対象とする。
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from app import config


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "test-key-not-for-production")
    config.get_settings.cache_clear()
    from app import main
    importlib.reload(main)
    return TestClient(main.app)


# --------------------------------------------------------------- 疎通
def test_healthzは常に200を返す(client):
    """DB が落ちていてもプロセスは生きているため 200。

    DB の状態は本文で示す。ロードバランサから外すかは別途判断する。
    """
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] in ("up", "down")


def test_トップページが描画される(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "CareShift Guard" in r.text
    assert "人員基準欠如減算" in r.text


def test_APIドキュメントは公開しない(client):
    """業務システムのため既定で無効化している。"""
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_静的ファイルの経路が有効(client):
    """存在しないファイルは404だが、経路自体は解決される。"""
    r = client.get("/static/does-not-exist.js")
    assert r.status_code == 404


# --------------------------------------------------------------- 設定の検証
def test_本番でSECRET_KEYが既定値なら起動を止める(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "change_me_in_production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+pg8000://u:p@h:5432/d")
    config.get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        config.get_settings()
    config.get_settings.cache_clear()


def test_本番でDB接続文字列が既定値なら起動を止める(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "a" * 64)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+pg8000://careshift:change_me_in_production@db:5432/careshift")
    config.get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        config.get_settings()
    config.get_settings.cache_clear()


@pytest.mark.parametrize("value", ["0", "0.5", "301", "-1"])
def test_求解上限が範囲外なら起動を止める(monkeypatch, value):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SOLVER_TIME_LIMIT", value)
    config.get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="SOLVER_TIME_LIMIT"):
        config.get_settings()
    config.get_settings.cache_clear()


def test_開発環境では既定値でも起動できる(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("SOLVER_TIME_LIMIT", raising=False)
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert not s.is_production
    assert s.solver_time_limit == 10
    config.get_settings.cache_clear()
