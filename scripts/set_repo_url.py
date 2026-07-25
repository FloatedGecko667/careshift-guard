"""リポジトリのURLを実際の値に置き換える。

README.md と pyproject.toml に置いた <your-account> を一括で置換する。
手で直すと片方だけ直して食い違うため、スクリプトにしておく。

使い方:
    python3 -m scripts.set_repo_url <アカウント名> [リポジトリ名]

例:
    python3 -m scripts.set_repo_url kolinz careshift-guard
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PLACEHOLDER = "<your-account>"
DEFAULT_REPO = "careshift-guard"
TARGETS = ["README.md", "pyproject.toml"]

# GitHub のアカウント名は英数字とハイフンのみ、先頭末尾はハイフン不可、39文字以内
ACCOUNT_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def validate(account: str, repo: str) -> None:
    if not ACCOUNT_RE.match(account):
        raise SystemExit(
            f"アカウント名が不正です: {account!r}\n"
            "英数字とハイフンのみ、先頭と末尾はハイフン不可、39文字以内です。")
    if not REPO_RE.match(repo):
        raise SystemExit(f"リポジトリ名が不正です: {repo!r}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    account = sys.argv[1].strip().lstrip("@")
    repo = (sys.argv[2].strip() if len(sys.argv) > 2 else DEFAULT_REPO)
    validate(account, repo)

    url = f"https://github.com/{account}/{repo}"
    changed = 0
    for name in TARGETS:
        path = Path(name)
        if not path.exists():
            print(f"見つかりません: {name}")
            continue
        text = path.read_text(encoding="utf-8")
        before = text
        text = text.replace(f"https://github.com/{PLACEHOLDER}/{DEFAULT_REPO}", url)
        text = text.replace(PLACEHOLDER, account)
        if text != before:
            path.write_text(text, encoding="utf-8")
            changed += 1
            print(f"置換: {name}")
        else:
            print(f"置換対象なし: {name}")

    remaining = [n for n in TARGETS if Path(n).exists()
                 and PLACEHOLDER in Path(n).read_text(encoding="utf-8")]
    if remaining:
        print(f"\n未置換が残っています: {remaining}")
        return 1

    print(f"\nリポジトリURL: {url}")
    print(f"{changed} ファイルを更新しました。")
    print("\n次の手順:")
    print("  git init && git add -A")
    print("  git commit -m 'feat: 初版（人員配置基準チェック内蔵シフト自動作成）'")
    print("  git branch -M main")
    print(f"  git remote add origin {url}.git")
    print("  git push -u origin main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
