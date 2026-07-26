"""勤務形態一覧表の xlsx をスライド用の PNG に変換する。

Excel を開いて画面を撮る方法では、撮る人の環境で見た目が変わり、
再現できない。LibreOffice で PDF に変換してから画像化することで、
同じ入力からいつでも同じ画像が得られる。

数式は LibreOffice が実際に評価するため、
出力された数値はテストで検証しているものと同一である。

必要なもの:
    soffice（LibreOffice）, pdftoppm（poppler-utils）

使い方:
    python3 -m scripts.render_xlsx [入力.xlsx]
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

SRC_DEFAULT = Path("docs/samples/勤務形態一覧表_2026年8月.xlsx")
OUT = Path("docs/images")

# PDF のページ番号 → 出力名。xlsx のシート順と対応する。
PAGES = {
    1: ("10_kinmu_keitai.png", "勤務形態一覧表"),
    2: ("11_daily_check.png", "日別の基準判定"),
}

DPI = 130
MARGIN = 24          # 余白として残す画素数
WHITE_THRESHOLD = 246  # これ以上明るい画素は「白紙」とみなす


def tool(name: str) -> str:
    p = shutil.which(name)
    if p is None:
        msg = f"{name} が見つかりません。"
        raise RuntimeError(msg)
    return p


def trim(img: Image.Image) -> Image.Image:
    """周囲の白紙を切り落とす。

    A3 横向きの用紙全体を貼るとスライド上で内容が小さくなりすぎる。
    表の外周だけを残す。
    """
    grey = img.convert("L")
    # 白より暗い画素の範囲を求める
    mask = grey.point(lambda v: 0 if v >= WHITE_THRESHOLD else 255)
    box = mask.getbbox()
    if box is None:
        return img
    left, top, right, bottom = box
    return img.crop((
        max(left - MARGIN, 0),
        max(top - MARGIN, 0),
        min(right + MARGIN, img.width),
        min(bottom + MARGIN, img.height),
    ))


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC_DEFAULT
    if not src.exists():
        print(f"{src} がありません。先に make sample を実行してください。")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        # 日本語のファイル名を経由するとフィルタ判定で失敗する環境があるため
        # 作業用の名前に写してから変換する。
        book = work / "book.xlsx"
        book.write_bytes(src.read_bytes())

        subprocess.run(  # noqa: S603
            [tool("soffice"), "--headless", "--convert-to", "pdf",
             str(book), "--outdir", str(work)],
            check=True, capture_output=True, timeout=180)
        pdf = work / "book.pdf"
        if not pdf.exists():
            print("PDF への変換に失敗しました。")
            return 1

        subprocess.run(  # noqa: S603
            [tool("pdftoppm"), "-r", str(DPI), "-png", str(pdf),
             str(work / "page")],
            check=True, capture_output=True, timeout=180)

        print(f"{src.name} を画像化します（{DPI}dpi）")
        for page, (name, label) in PAGES.items():
            png = work / f"page-{page}.png"
            if not png.exists():
                print(f"  ページ {page} がありません: {label}")
                continue
            img = trim(Image.open(png))
            dst = OUT / name
            img.save(dst)
            print(f"  {dst}  ({img.width}x{img.height})  {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
