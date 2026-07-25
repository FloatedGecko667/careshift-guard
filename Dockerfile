# =====================================================================
# CareShift Guard  web コンテナ
#
#   Python 3.13 に固定する。OR-Tools の aarch64 向けビルド済み
#   パッケージの提供は最新の Python に追随するまで時間差があるため、
#   実績のある版に固定して環境構築時の失敗を避ける。
#   ホスト OS の Python 版はコンテナ内に影響しない。
# =====================================================================
FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依存を先に入れる。ソース変更でレイヤーキャッシュが無効化されないようにする
COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && pip install --no-cache-dir '.[prod]'

COPY app/ ./app/
COPY db/ ./db/
COPY migrations/ ./migrations/
COPY alembic.ini ./

# root で動かさない
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# ワーカー数は 1 とする。シフト最適化が CPU を集中的に使うため、
# 多重化しても割り当て OCPU 以上には速くならない。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
