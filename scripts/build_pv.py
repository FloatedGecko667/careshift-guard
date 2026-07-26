"""紹介動画（MP4）を組み立てる。

docs/images/ のスクリーンショットと、その場で描く説明カードをつないで
3分以内の紹介動画にする。音声は使わず、字幕で説明する。

画面収録ではなく静止画の連結にしている理由
  ・字幕の分量と表示時間を制御できるため、内容が確実に読める
  ・同じ入力から常に同じ動画が出るので、撮り直しがない
  ・静止画中心なので H.264 の圧縮が効き、pptx へ埋め込める大きさに収まる

符号化の要点
  静止画を動画にするとき `-loop 1 -i x.png` だけを指定すると、
  ffmpeg は入力を既定の25fpsで生成し、出力側の -r で間引く。
  つまり必要なフレームの6倍を無駄に処理する。
  入力側に `-framerate` を指定すると必要な枚数だけ生成され、
  実測で 6.9秒 → 0.9秒 になった。

使い方:
    python3 -m scripts.build_pv                 # 全部作る
    python3 -m scripts.build_pv --segments 0 6  # 区間だけ符号化
    python3 -m scripts.build_pv --concat        # 区間を連結して仕上げる
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

IMG = Path("docs/images")
WORK = Path("docs/images/.pv")
OUT_DEFAULT = Path("docs/紹介動画_CareShift_Guard.mp4")

W, H = 1920, 1080
# 静止画の連結なので低フレームレートで足りる。
# 見た目は変わらず、符号化の量とファイル容量だけが下がる。
FPS = 4

# 配色はスライドと揃える
DARK = (11, 60, 73)
MINT = (2, 195, 154)
ALERT = (192, 57, 43)
WHITE = (255, 255, 255)
PALE = (175, 199, 206)
BG = (242, 247, 248)

CJK_BOLD = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
]
CJK_REG = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
]


def font(size: int, bold: bool = False):
    for p in (CJK_BOLD if bold else CJK_REG):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def wrap(d: ImageDraw.ImageDraw, text: str, f, max_w: int) -> list[str]:
    """日本語は単語境界が無いため文字単位で折り返す。"""
    out, cur = [], ""
    for ch in text:
        if ch == "\n":
            out.append(cur)
            cur = ""
        elif d.textlength(cur + ch, font=f) > max_w and cur:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------- 場面の描画
def title_card(path: Path, head: str, lines: list[str], accent=MINT) -> None:
    img = Image.new("RGB", (W, H), DARK)
    d = ImageDraw.Draw(img)
    fh, fb = font(72, True), font(38)

    head_lines = wrap(d, head, fh, W - 320)
    body_h = sum(58 + 12 for _ln in lines) or 0
    y = max(200, (H - len(head_lines) * 96 - body_h) // 2 - 40)

    for ln in head_lines:
        d.text((160, y), ln, font=fh, fill=WHITE)
        y += 96
    y += 36
    for ln in lines:
        for w2 in wrap(d, ln, fb, W - 320):
            d.text((160, y), w2, font=fb,
                   fill=accent if ln.startswith("▶") else PALE)
            y += 56
        y += 10

    d.text((160, H - 88), "CareShift Guard", font=font(28), fill=(120, 150, 160))
    img.save(path)


def shot_card(path: Path, src: Path, head: str, note: str) -> bool:
    """スクリーンショットを枠に収め、上に見出し、下に字幕を置く。"""
    if not src.exists() or src.stat().st_size == 0:
        return False
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W, 112], fill=DARK)
    d.text((70, 30), head, font=font(44, True), fill=WHITE)

    sub_h = 152
    box = (70, 142, W - 70, H - sub_h - 26)
    bw, bh = box[2] - box[0], box[3] - box[1]

    s = Image.open(src).convert("RGB")
    s.thumbnail((bw, bh), Image.LANCZOS)
    ox = box[0] + (bw - s.width) // 2
    oy = box[1] + (bh - s.height) // 2
    d.rectangle([ox - 3, oy - 3, ox + s.width + 3, oy + s.height + 3],
                outline=(185, 200, 206), width=3)
    img.paste(s, (ox, oy))

    d.rectangle([0, H - sub_h, W, H], fill=DARK)
    fb = font(34)
    ly = H - sub_h + 24
    for ln in wrap(d, note, fb, W - 150)[:3]:
        d.text((70, ly), ln, font=fb, fill=WHITE)
        ly += 44
    img.save(path)
    return True


# ---------------------------------------------------------------- 構成
# (種別, 引数...) 種別は "card" または "shot"
PLAN: list[tuple] = [
    ("card", "CareShift Guard",
     ["介護事業所向け",
      "人員配置基準チェック内蔵 シフト自動作成クラウド",
      "",
      "▶ シフト作成を楽にする道具ではありません"], 7, MINT),

    ("card", "人員基準欠如減算",
     ["人員配置基準を満たさないと",
      "介護報酬は基本報酬の30パーセントが減算される",
      "",
      "▶ しかも利用者全員に、解消されるまで続く"], 8, ALERT),

    ("card", "月商800万円の事業所で2か月続けば",
     ["逸失収益は480万円",
      "",
      "この多くは、シフトを組む段階で気づけていれば防げる"], 7, ALERT),

    ("card", "なぜ現場は気づけないのか",
     ["勤務形態一覧表は月ごとの集計である",
      "",
      "▶ 月次では全職種が基準を満たして見える",
      "▶ 同じ月を日別に見ると違反が出る"], 8, MINT),

    ("shot", "09_masters.png", "マスタ管理",
     "職員と勤務区分を登録します。管理者が生活相談員を50パーセント兼務する"
     "といった実際の形態にも対応します。", 9),

    ("shot", "08_requests_mobile.png", "希望シフト入力（職員向け）",
     "職員はスマートフォンから希望休を登録します。"
     "htmx により、登録した日の行だけが更新されます。", 9),

    ("shot", "06_schedule_ok.png", "シフト自動生成",
     "平均利用者数を入れて生成すると、人員配置基準・労働基準法・就業規則・"
     "職員の希望をすべて同時に満たす組合せを数秒で探索します。", 11),

    ("card", "生成AIは使っていません",
     ["シフト作成は組合せ最適化問題である",
      "",
      "▶ 制約充足を決定論的に保証できるソルバーで解く",
      "▶ Google OR-Tools の CP-SAT を採用",
      "",
      "確率的に出力するモデルは制約違反の解をもっともらしく出しうる。",
      "減算に直結する判定には使わない。"], 12, MINT),

    ("shot", "07_schedule_violation.png", "違反の可視化",
     "基準を下回る日は赤で表示します。"
     "下の一覧に日付・職種・不足数と対応の目安が出ます。", 11),

    ("shot", "16_publish_disabled.png", "確定できないようにする",
     "違反が1件でもあると確定ボタンは押せません。判定はデータベース側の"
     "条件として書いてあり、経路が増えても抜けません。", 10),

    ("shot", "10_kinmu_keitai.png", "勤務形態一覧表の出力",
     "実地指導に提出する様式をExcelで出力します。兼務者は職種ごとに行を分けて"
     "按分し、合計と常勤換算は数式なのでその場で検算できます。", 11),

    ("shot", "11_daily_check.png", "日別の基準判定",
     "月次では適合に見えても、日別では違反が出ます。"
     "この乖離を可視化することが本ソリューションの中核です。", 10),

    ("shot", "00_architecture.png", "システム構成",
     "nginx がリバースプロキシ、web が FastAPI と OR-Tools、"
     "db が PostgreSQL 18。3つのコンテナだけで構成しています。", 9),

    ("shot", "03_docker_compose_ps.png", "稼働構成",
     "nginx / web / db の3コンテナを Docker Compose で動かしています。", 8),

    ("shot", "04_env.png", "実行環境",
     "Arm64 上で動作します。Oracle Cloud Infrastructure の Ampere A1 と"
     "同一の命令セットであり、同じイメージがそのまま動きます。", 9),

    ("shot", "12_make_test.png", "検証",
     "テスト150件。制約はソルバーとは独立に実装した監査関数で再検査し、"
     "Excelの数式はLibreOfficeで実際に評価して突合しています。", 9),

    ("card", "提供するのは「シフト表」ではありません",
     ["人員配置基準に適合していることを",
      "継続的に証明できる状態です",
      "",
      "▶ 導入費 148,000円 ／ 月額 16,500円（職員数無制限）",
      "▶ 損益分岐点 4事業所。補助金も無料枠も前提としません"], 11, MINT),

    ("card", "CareShift Guard",
     ["github.com/FloatedGecko667/careshift-guard",
      "",
      "クラウドプラットフォーム実習Ⅱ　最終レポート",
      "学籍番号 20122049　曽我 幸太郎"], 8, MINT),
]


def draw_all() -> list[tuple[Path, float]]:
    """カードを描画し、(画像, 秒数) の並びを返す。未撮影の場面は飛ばす。"""
    WORK.mkdir(parents=True, exist_ok=True)
    seq: list[tuple[Path, float]] = []
    skipped: list[str] = []
    for i, item in enumerate(PLAN):
        p = WORK / f"card_{i:03d}.png"
        if item[0] == "card":
            _, head, lines, sec, accent = item
            title_card(p, head, lines, accent)
            seq.append((p, float(sec)))
        else:
            _, file, head, note, sec = item
            if shot_card(p, IMG / file, head, note):
                seq.append((p, float(sec)))
            else:
                skipped.append(file)
    if skipped:
        print("  未撮影のため省略: " + ", ".join(skipped))
    return seq


def tool(name: str) -> str:
    """実行ファイルの絶対パスを解決する。

    引数に相対名を渡すと PATH の内容次第で別物が動きうる。
    ここで一度だけ解決し、以降は絶対パスだけを使う。
    """
    p = shutil.which(name)
    if p is None:
        msg = f"{name} が見つかりません。"
        raise RuntimeError(msg)
    return p


def encode_segment(png: Path, sec: float, dst: Path) -> None:
    subprocess.run(  # noqa: S603
        [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
         # 入力側に framerate を指定するのが要点。無いと25fps分を作って捨てる。
         "-loop", "1", "-framerate", str(FPS), "-i", str(png),
         "-t", f"{sec}", "-c:v", "libx264", "-preset", "ultrafast",
         "-crf", "26", "-pix_fmt", "yuv420p", "-tune", "stillimage",
         str(dst)], check=True)


def main() -> int:
    args = sys.argv[1:]
    if shutil.which("ffmpeg") is None:
        print("ffmpeg が見つかりません。")
        return 1

    # --concat: 既に符号化した区間を連結して仕上げる
    if "--concat" in args:
        segs = sorted(WORK.glob("seg_*.mp4"))
        if not segs:
            print("区間がありません。先に符号化してください。")
            return 1
        lst = WORK / "segs.txt"
        lst.write_text("".join(f"file '{s.name}'\n" for s in segs),
                       encoding="utf-8")
        out = OUT_DEFAULT
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(  # noqa: S603
            [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
             "-f", "concat", "-safe", "0", "-i", "segs.txt",
             "-c", "copy", "-movflags", "+faststart",
             str(Path("../../..") / out)],
            cwd=WORK, check=True)
        info = subprocess.run(  # noqa: S603
            [tool("ffprobe"), "-v", "error", "-show_entries",
             "format=duration,size:stream=width,height,codec_name,r_frame_rate",
             "-of", "default=nw=1", str(out)],
            capture_output=True, text=True)
        print(f"出力: {out}")
        print(f"  区間 {len(segs)} 本を無再圧縮で連結")
        for ln in info.stdout.strip().splitlines():
            print(f"  {ln}")
        return 0

    seq = draw_all()
    total = sum(s for _, s in seq)
    print(f"カード {len(seq)} 枚 / 合計 {total:.0f} 秒"
          f"（{int(total // 60)}分{int(total % 60)}秒）")
    if total > 180:
        print("  ! 3分を超えています。表示秒数を見直してください。")

    # --segments a b: 区間 [a, b) だけ符号化する。
    # 実行時間の上限がある環境で分割して進めるため。
    lo, hi = 0, len(seq)
    if "--segments" in args:
        k = args.index("--segments")
        lo, hi = int(args[k + 1]), min(int(args[k + 2]), len(seq))

    for i in range(lo, hi):
        png, sec = seq[i]
        dst = WORK / f"seg_{i:03d}.mp4"
        encode_segment(png, sec, dst)
        print(f"  seg_{i:03d}  {sec:>4.0f}秒  {dst.stat().st_size / 1024:>7.0f} KB")

    print(f"区間 {lo}〜{hi - 1} を符号化しました。")
    if hi >= len(seq):
        print("すべて揃いました。--concat で仕上げてください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
