"""実サーバを起動して HTTP 経由で疎通を確認する。

pytest の TestClient は ASGI を直接呼ぶため、
uvicorn 経由での実際の待ち受けは検証できない。
デプロイ前の最終確認としてこれを使う。

データベースへ接続できない環境でも実行できる。
その場合 /healthz の database は down となるが、
プロセスが生きていること自体は確認できる。

使い方:
    python3 -m scripts.smoke_test [ポート]
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
BASE = f"http://127.0.0.1:{PORT}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def probe(path: str, headers: dict[str, str] | None = None):
    # 接続先は自身が起動した localhost に固定されており、外部入力は無い
    req = urllib.request.Request(BASE + path, headers=headers or {})  # noqa: S310
    try:
        r = _opener.open(req, timeout=15)
        return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def wait_ready(proc: subprocess.Popen, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            if probe("/healthz")[0] == 200:
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def main() -> int:
    env = {**os.environ,
           "APP_ENV": "development",
           "SECRET_KEY": "smoke-test-key",
           "DATABASE_URL": os.environ.get(
               "DATABASE_URL",
               "postgresql+pg8000://x:y@127.0.0.1:5432/none")}

    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    ok = True
    try:
        if not wait_ready(proc):
            print("起動に失敗しました")
            if proc.stdout:
                print(proc.stdout.read()[:2000])
            return 1
        print(f"サーバ起動: {BASE}\n")

        checks: list[tuple[str, bool, str]] = []

        # --- 疎通 ---
        s, _, body = probe("/healthz")
        checks.append(("/healthz が200", s == 200, f"HTTP {s} {body.strip()}"))

        s, _, body = probe("/")
        checks.append(("トップページが描画される",
                       s == 200 and "人員基準欠如減算" in body,
                       f"HTTP {s} / {len(body)} バイト"))

        s, _, body = probe("/login")
        checks.append(("ログイン画面が描画される",
                       s == 200 and "メールアドレス" in body,
                       f"HTTP {s} / {len(body)} バイト"))

        # --- 未ログインでの保護経路 ---
        for path in ("/schedules", "/masters", "/requests"):
            s, h, _ = probe(path)
            loc = h.get("Location") or h.get("location")
            checks.append((f"{path} が未ログインでログイン画面へ誘導",
                           s == 303 and loc == "/login", f"HTTP {s} -> {loc}"))

        s, h, _ = probe("/schedules", {"HX-Request": "true"})
        hx = h.get("HX-Redirect") or h.get("hx-redirect")
        checks.append(("htmx要求には遷移指示ヘッダを返す",
                       s == 200 and hx == "/login", f"HTTP {s} HX-Redirect={hx}"))

        # --- 公開してはいけないもの ---
        for path in ("/docs", "/redoc", "/openapi.json"):
            s, _, _ = probe(path)
            checks.append((f"{path} を公開していない", s == 404, f"HTTP {s}"))

        # --- 認証の失敗 ---
        s, _, _ = probe("/schedules/generate")
        checks.append(("POST専用の経路にGETできない",
                       s in (303, 405), f"HTTP {s}"))

        print(f"{'項目':<48}{'結果':<6}詳細")
        print("-" * 92)
        for name, passed, detail in checks:
            ok &= passed
            print(f"{name:<48}{'OK' if passed else 'NG':<6}{detail}")
        print("-" * 92)
        print("総合:", "全項目OK" if ok else "NG あり")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
