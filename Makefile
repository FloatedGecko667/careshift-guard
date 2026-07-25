.DEFAULT_GOAL := help
.PHONY: help init vendor up down logs migrate seed shell test lint fmt sample slides smoke clean

help:  ## 使えるターゲットを一覧表示する
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

init:  ## .env を作成する（初回のみ）
	@test -f .env || (cp .env.example .env && echo ".env を作成しました。値を設定してください")
	@test -f .env && echo ".env は既に存在します"

vendor:  ## htmx を取得して自前配信する（外部CDNに依存しない）
	@mkdir -p app/static
	curl -fsSL -o app/static/htmx.min.js \
	  https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js
	@echo "app/static/htmx.min.js を配置しました"
	@echo "※ htmx は任意依存です。無くても全操作はフォーム送信で完結します。"

up: init  ## コンテナを起動する
	docker compose up -d --build
	@echo "起動しました → http://localhost:8080"

down:  ## コンテナを停止する（データは保持）
	docker compose down

logs:  ## ログを追う
	docker compose logs -f web

migrate:  ## スキーマを適用する（db/schema.sql を実行）
	docker compose exec web alembic upgrade head

seed:  ## 初期データを投入する
	docker compose exec -T db psql -U "$${POSTGRES_USER:-careshift}" \
	  -d "$${POSTGRES_DB:-careshift}" < db/seed.sql

shell:  ## psql に入る
	docker compose exec db psql -U "$${POSTGRES_USER:-careshift}" \
	  -d "$${POSTGRES_DB:-careshift}"

test:  ## テストを実行する
	python3 -m pytest -v

lint:  ## 静的解析
	python3 -m ruff check .

fmt:  ## 整形
	python3 -m ruff format .
	python3 -m ruff check --fix .

sample:  ## サンプルの勤務形態一覧表を生成する
	python3 -m scripts.export_sample
	python3 -m scripts.render_preview docs/samples/preview_基準充足.html 24 22 42
	python3 -m scripts.render_preview docs/samples/preview_基準違反あり.html 13 38 7

slides:  ## 発表スライドを生成する（docs/images の画像を埋め込む）
	@test -d node_modules || npm install pptxgenjs
	node scripts/build_slides.js
	@echo "PV_URL / DEMO_URL を指定すると、スライドのURL欄に反映されます"

smoke:  ## 実サーバを起動して HTTP 経由で疎通を確認する
	python3 -m scripts.smoke_test

clean:  ## 生成物を削除する（docs/samples は残す）
	rm -rf .pytest_cache .ruff_cache __pycache__ */__pycache__
	find . -name '*.pyc' -delete
