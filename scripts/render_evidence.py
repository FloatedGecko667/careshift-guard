"""docs/images/raw/*.txt をターミナル風の PNG に描画する。

capture_demo.sh が書き出した実際の出力を、
スライドに貼れる画像に変換する。生の画面キャプチャより
文字が読みやすく、再現性もある。

内容は加工しない。ANSI のエスケープ列だけ取り除く。

使い方:
    python3 -m scripts.render_evidence
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RAW = Path("docs/images/raw")
OUT = Path("docs/images")

# ターミナル風の配色
BG = (18, 26, 32)
FG = (222, 232, 236)
DIM = (130, 150, 160)
ACC = (2, 195, 154)
ERR = (232, 110, 100)
BAR = (32, 44, 52)

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# 描画対象。(入力, 出力, 見出し, 最大行数)
TARGETS = [
    ("env.txt", "04_env.png", "ホスト環境と Docker", 40),
    ("compose_ps.txt", "03_docker_compose_ps.png", "docker compose ps", 40),
    ("database.txt", "14_database.png", "PostgreSQL 18 のスキーマ", 60),
    ("pytest.txt", "12_make_test.png", "コンテナ内でのテスト実行", 40),
    ("pytest_excel.txt", "17_excel_formula.png",
     "Excel の数式を LibreOffice で実評価", 20),
    ("smoke.txt", "13_smoke.png", "スモークテスト（HTTP 経由）", 40),
    ("seed.txt", "15_seed.png", "デモデータの投入", 30),
]

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansMonoCJKjp-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]
CJK_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansMonoCJKjp-Regular.otf",
]


def load_font(size: int, cjk: bool = False):
    for p in (CJK_CANDIDATES if cjk else FONT_CANDIDATES):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def has_cjk(s: str) -> bool:
    return any("　" <= c <= "鿿" or "＀" <= c <= "￯" for c in s)


def line_color(s: str):
    low = s.lower()
    if any(k in low for k in ("error", "failed", "traceback", " ng ", "✗")):
        return ERR
    if any(k in s for k in ("passed", "OK", "✓", "healthy", "全項目")):
        return ACC
    if s.startswith("===") or s.startswith("---"):
        return DIM
    return FG


def render(src: Path, dst: Path, title: str, max_lines: int) -> bool:
    if not src.exists():
        print(f"  なし: {src}")
        return False

    text = ANSI.sub("", src.read_text(encoding="utf-8", errors="replace"))
    lines = [ln.rstrip() for ln in text.splitlines()]
    # 空行の連続を1行に詰める
    packed: list[str] = []
    for ln in lines:
        if not ln and packed and not packed[-1]:
            continue
        packed.append(ln)
    if len(packed) > max_lines:
        keep = max_lines - 2
        packed = packed[:keep] + ["", f"… 以下略（全 {len(lines)} 行）"]

    fs = 15
    mono = load_font(fs)
    mono_cjk = load_font(fs, cjk=True)
    title_font = load_font(19, cjk=True)

    pad, lh, bar_h = 22, int(fs * 1.55), 42
    # 幅を内容から決める。固定幅にすると docker compose ps のように
    # 横に長い出力で右端が切れ、証拠として読めなくなる。
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    longest = max(
        (probe.textlength(ln, font=mono_cjk if has_cjk(ln) else mono)
         for ln in packed), default=0)
    width = max(1500, int(longest) + pad * 2 + 8)
    height = bar_h + pad + lh * max(len(packed), 3) + pad

    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)

    # 見出しの帯
    d.rectangle([0, 0, width, bar_h], fill=BAR)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        cx = 20 + i * 20
        d.ellipse([cx, bar_h // 2 - 6, cx + 12, bar_h // 2 + 6], fill=c)
    d.text((92, bar_h // 2 - 11), title, font=title_font, fill=FG)

    y = bar_h + pad
    for ln in packed:
        f = mono_cjk if has_cjk(ln) else mono
        d.text((pad, y), ln, font=f, fill=line_color(ln))
        y += lh

    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst)
    print(f"  {dst}  ({width}x{height}, {len(packed)}行)")
    return True


def main() -> int:
    if not RAW.exists():
        print(f"{RAW} がありません。先に scripts/capture_demo.sh を実行してください。")
        return 1
    print("証跡を画像化します")
    n = sum(render(RAW / s, OUT / o, t, m) for s, o, t, m in TARGETS)
    print(f"\n{n} 件を出力しました。")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
