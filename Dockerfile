# =====================================================================
# CareShift Guard  web コンテナ
#
#   Python 3.13 に固定する。OR-Tools の aarch64 向けビルド済み
#   パッケージの提供は最新の Python に追随するまで時間差があるため、
#   実績のある版に固定して環境構築時の失敗を避ける。
#   ホスト OS の Python 版はコンテナ内に影響しない。
# =====================================================================
FROM python:3.13-slim-bookworm

# PYTHONPATH でソースツリーを優先させる。
# パッケージを pip install もするが、import が site-packages 側に
# 解決されるとテンプレートの探索先が変わって事故りやすい。
# 常に /app/app を読ませることで挙動を一意にする。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# 開発用の依存を含めるか。既定は含めない。
#   デモや検証で「コンテナ自身にテストを実行させたい」ときだけ true にする。
#   pglast は GPLv3 相当のため、顧客へ提供するイメージには含めない。
#     docker compose build --build-arg INSTALL_DEV=true
ARG INSTALL_DEV=false

# pyproject の packages に app と app.routers を明示しているため、
# ビルド時にはそれらのディレクトリが揃っている必要がある。
# 依存だけ先に入れてキャッシュを効かせる書き方もできるが、
# パッケージ検出の失敗を招きやすいので確実性を取る。
COPY pyproject.toml README.md ./
COPY app/ ./app/
RUN pip install --upgrade pip \
    && if [ "$INSTALL_DEV" = "true" ]; then \
         pip install --no-cache-dir '.[dev]'; \
       else \
         pip install --no-cache-dir '.[prod]'; \
       fi

COPY db/ ./db/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/
COPY alembic.ini ./
# テストは INSTALL_DEV=true のときだけ意味を持つが、
# COPY を条件分岐できないため常に含める（プレーンテキストのみで軽量）。
COPY tests/ ./tests/

# root で動かさない
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# ワーカー数は 1 とする。シフト最適化が CPU を集中的に使うため、
# 多重化しても割り当て OCPU 以上には速くならない。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
