#!/usr/bin/env bash
# =====================================================================
# ローカルの Docker で CareShift Guard を起動し、
# 発表資料に載せる証跡をファイルへ書き出す。
#
#   bash scripts/capture_demo.sh
#
# 実行後、docs/images/raw/ に環境情報とテスト結果が保存される。
# ブラウザの画面は別途取得する。
# =====================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
RAW="docs/images/raw"
mkdir -p "$RAW"

step() { printf '\n\033[36m▶ %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m  ✓ %s\033[0m\n' "$1"; }
die()  { printf '\033[31m  ✗ %s\033[0m\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------- 前提確認
step "前提の確認"
command -v docker >/dev/null || die "docker が見つかりません。OrbStack か Docker Desktop を起動してください。"
docker info >/dev/null 2>&1 || die "docker デーモンに接続できません。OrbStack を起動してください。"
docker compose version >/dev/null 2>&1 || die "docker compose が使えません。"
ok "docker は利用可能です"

# ---------------------------------------------------------------- 環境情報
step "環境情報を記録"
{
  echo "=== ホスト環境 ==="
  echo "日時          : $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "OS            : $(uname -s) $(uname -r)"
  echo "アーキテクチャ: $(uname -m)"
  if [ "$(uname -m)" = "arm64" ] || [ "$(uname -m)" = "aarch64" ]; then
    echo "               → Arm64。Oracle Cloud の Ampere A1 と同一の命令セット"
  fi
  echo
  echo "=== Docker ==="
  docker --version
  docker compose version
  echo
  echo "=== コンテナのアーキテクチャ ==="
  docker info --format 'Server Architecture: {{.Architecture}}' 2>/dev/null || true
} | tee "$RAW/env.txt"
ok "$RAW/env.txt"

# ---------------------------------------------------------------- .env
step ".env の用意"
if [ ! -f .env ]; then
  cp .env.example .env
  # デモ用に強い値を生成して差し替える
  PW=$(openssl rand -hex 16)
  SK=$(openssl rand -hex 32)
  if sed --version >/dev/null 2>&1; then SEDI=(-i); else SEDI=(-i ''); fi
  sed "${SEDI[@]}" "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PW}|" .env
  sed "${SEDI[@]}" "s|^SECRET_KEY=.*|SECRET_KEY=${SK}|" .env
  sed "${SEDI[@]}" "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+pg8000://careshift:${PW}@db:5432/careshift|" .env
  ok ".env を作成し、パスワードと鍵を生成しました"
else
  ok ".env は既に存在します（変更しません）"
fi

# ---------------------------------------------------------------- htmx
step "htmx の取得（任意依存）"
if [ ! -s app/static/htmx.min.js ]; then
  curl -fsSL -o app/static/htmx.min.js \
    https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js && ok "取得しました" \
    || printf '  ! 取得に失敗しました。htmx は任意依存なので処理は続行します\n'
else
  ok "既に配置済みです"
fi

# ---------------------------------------------------------------- 起動
step "コンテナのビルドと起動（初回は数分かかります）"
INSTALL_DEV=true docker compose up -d --build
ok "起動を指示しました"

step "コンテナが healthy になるのを待機"
for i in $(seq 1 60); do
  if curl -fsS http://localhost:8080/healthz >/dev/null 2>&1; then
    ok "疎通しました（${i}回目の確認）"; break
  fi
  [ "$i" = 60 ] && die "起動しませんでした。docker compose logs web を確認してください。"
  sleep 5
done

# ---------------------------------------------------------------- 初期化
step "スキーマの適用"
docker compose exec -T web alembic upgrade head
ok "db/schema.sql を適用しました"

step "初期データの投入"
docker compose exec -T db psql -U careshift -d careshift -q < db/seed.sql >/dev/null
ok "人員配置基準ルールと事業所を登録しました"

step "デモ用の職員と勤務希望の投入"
docker compose exec -T web python3 -m scripts.seed_demo | tee "$RAW/seed.txt"
ok "$RAW/seed.txt"

# ---------------------------------------------------------------- 証跡
step "コンテナの状態を記録"
{
  echo "=== docker compose ps ==="
  docker compose ps
  echo
  echo "=== 各コンテナのイメージとアーキテクチャ ==="
  for c in $(docker compose ps -q); do
    docker inspect "$c" --format \
      '{{.Name}}  image={{.Config.Image}}  arch={{.Platform}}  status={{.State.Health.Status}}'
  done
} | tee "$RAW/compose_ps.txt"
ok "$RAW/compose_ps.txt"

step "データベースの状態を記録"
{
  echo "=== テーブル一覧 ==="
  docker compose exec -T db psql -U careshift -d careshift -c '\dt'
  echo "=== ビュー一覧 ==="
  docker compose exec -T db psql -U careshift -d careshift -c '\dv'
  echo "=== PostgreSQL のバージョン ==="
  docker compose exec -T db psql -U careshift -d careshift -tAc 'SELECT version()'
  echo "=== 人員配置基準ルール ==="
  docker compose exec -T db psql -U careshift -d careshift -c \
    "SELECT job_type, formula_type, base_fte, threshold_users, step_users, step_fte, min_headcount FROM staffing_rules WHERE service_type='day_service' ORDER BY job_type"
  echo "=== 勤務区分（work_minutes は生成列） ==="
  docker compose exec -T db psql -U careshift -d careshift -c \
    "SELECT code, name, start_minute, end_minute, break_minutes, work_minutes FROM shift_patterns ORDER BY display_order"
} | tee "$RAW/database.txt"
ok "$RAW/database.txt"

step "テストの実行（コンテナ内）"
docker compose exec -T web python3 -m pytest -q 2>&1 | tee "$RAW/pytest.txt" || true
ok "$RAW/pytest.txt"

step "スモークテストの実行"
docker compose exec -T web python3 -m scripts.smoke_test 2>&1 | tee "$RAW/smoke.txt" || true
ok "$RAW/smoke.txt"

step "ヘルスチェック"
curl -s http://localhost:8080/healthz | tee "$RAW/healthz.txt"; echo
ok "$RAW/healthz.txt"

# ---------------------------------------------------------------- 完了
cat <<'MSG'

=====================================================================
起動しました。ブラウザで次を開けます。

    http://localhost:8080/

  メールアドレス: admin@example.jp
  パスワード    : CareShift2026!

証跡は docs/images/raw/ に保存しました。
このあとの画面取得とスライドへの埋め込みは Claude が行います。

停止するとき: docker compose down
=====================================================================
MSG
