"""紹介動画（MP4）を組み立てる。

内容は「実際に構築したソリューションを操作して使い方を説明する」もの。
ローカル Docker で稼働中の CareShift Guard を Chrome で操作し、
その各段階の画面をそのまま並べて手順書として見せる。3分以内に収める。

音声は使わず、画面の下に説明を焼き込む。

画面収録ではなく実操作の画面を静止画で並べている理由
  ・説明文の分量と表示時間を確実に制御できる（読み切れる速さになる）
  ・同じ入力から常に同じ動画が出るので、撮り直しが要らない
  ・静止画中心なので H.264 がよく効き、pptx へ埋め込める容量に収まる

符号化の要点
  静止画を動画にするとき `-loop 1 -i x.png` だけを指定すると、
  ffmpeg は入力を既定の25fpsで生成し、出力側の -r で間引く。
  つまり必要なフレームの6倍を無駄に処理する。
  入力側に `-framerate` を指定すると必要な枚数だけ生成され、
  実測で 6.9秒 → 0.9秒 になった。

使い方:
    python3 -m scripts.build_pv                 # 全区間を符号化
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
SRT_DEFAULT = Path("docs/紹介動画_CareShift_Guard.srt")

W, H = 1920, 1080
# 静止画の連結なので低フレームレートで足りる。
# 見た目は変わらず、符号化の量とファイル容量だけが下がる。
FPS = 4

# 配色はスライドと揃える
DARK = (11, 60, 73)
DEEP = (2, 128, 144)
MINT = (2, 195, 154)
ALERT = (192, 57, 43)
WHITE = (255, 255, 255)
PALE = (175, 199, 206)
BAND = (6, 42, 52)

# 画面を置ける領域
SHOT_BOX = (110, 150, W - 110, 930)     # left, top, right, bottom
CAP_TOP = 946                            # 説明帯の上端

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


# ------------------------------------------------------------------ 場面の定義
#   ("card", 見出し, 本文の行, 秒, 色)
#   ("step", 番号, 画像, 見出し, 説明, 秒, 強調枠)
#     強調枠は元画像の座標 (l, t, r, b)。None なら描かない。
PLAN: list[tuple] = [
    ("card", "CareShift Guard", [
        "介護事業所向け　人員配置基準チェック内蔵",
        "シフト自動作成クラウド",
        "",
        "これから実際の画面で使い方を説明します（約2分50秒）",
    ], 6, MINT),

    ("step", 1, "05_login.png", "ログインする",
     "事業所ごとのアカウントでログインします。"
     "管理者と職員で見える範囲が変わります。", 8, None),

    ("step", 2, "09_masters.png", "職員と兼務を登録する",
     "職種・雇用区分・資格を登録します。兼務は「兼務先の職種」と"
     "「従事割合」を入れると、常勤換算が自動で按分されます。", 15,
     (944, 0, 1172, 462)),

    ("step", 3, "08_requests_mobile.png", "職員が希望休を入力する",
     "職員は自分のスマートフォンから希望休を入れます。"
     "管理者が代理入力することもできます。", 12, None),

    ("step", 4, "20_generate_form.png", "平均利用者数を入れて生成する",
     "平均利用者数から介護職員の必要常勤換算数を算定します。"
     "利用者15人までは1以上、超過分は5で除した数を加えます（端数は切り上げません）。",
     14, (20, 262, 320, 332)),

    ("step", 5, "06_schedule_ok.png", "数秒でシフト表ができる",
     "求解 OPTIMAL・0.18秒。希望休は72件すべて反映され、"
     "職員間の勤務時間差は0.0時間。全日で人員配置基準を満たしています。",
     15, (0, 180, 1553, 245)),

    ("step", 6, "21_schedule_ok_fte.png", "職種別の常勤換算を日別に見る",
     "最下段が職種ごとの「実際／必要」です。"
     "月次の集計では見えない日単位の充足状況をここで確認します。", 14,
     (20, 370, 1235, 545)),

    ("step", 7, "07_schedule_violation.png", "利用者が増えると足りなくなる",
     "平均利用者数を60名にして再生成すると、介護職員の必要数が10.0に上がり、"
     "20日分が不足しました。この状態を先に知ることが目的です。", 15,
     (16, 185, 1553, 250)),

    ("step", 8, "18_violation_fte.png", "不足している職種と日が赤く出る",
     "介護職員の行が赤くなっています。9.8／10.0 のように"
     "「実際／必要」で表示されるため、あと何人分足りないかがすぐ分かります。",
     15, (20, 210, 1410, 255)),

    ("step", 9, "19_violation_list.png", "違反の一覧で対応を決める",
     "日付・職種・必要・実際・不足・対応の目安が並びます。"
     "応援職員の手配か、当日の利用者受入数の調整かを、月が始まる前に判断できます。",
     15, (30, 280, 1540, 480)),

    ("step", 10, "16_publish_disabled.png", "違反があるあいだは確定できない",
     "「確定して公開」は違反が0件のときだけ押せます。"
     "画面の表示だけでなくデータベース側でも同じ条件を課しています。", 10, None),

    ("step", 11, "10_kinmu_keitai.png", "勤務形態一覧表を出力する",
     "実地指導に提出する様式をそのままExcelで出力します。"
     "合計と常勤換算はセルに数式で書いてあるため、その場で検算できます。", 15,
     None),

    ("step", 12, "11_daily_check.png", "日別の基準判定シートで確かめる",
     "同じブックの2枚目です。月次では全職種が基準以上でも、"
     "日別に見ると×の日が残ることがあります。これが減算の原因になります。",
     15, None),

    ("card", "CareShift Guard", [
        "提供するのは「シフト表」ではありません",
        "人員配置基準に適合していることを継続的に証明できる状態です",
        "",
        "github.com/FloatedGecko667/careshift-guard",
        "学籍番号 20122049　曽我 幸太郎",
    ], 8, MINT),
]


# ------------------------------------------------------------------ 描画
def base_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), DARK)
    return img, ImageDraw.Draw(img)


def title_card(path: Path, head: str, lines: list[str], accent=MINT) -> None:
    img, d = base_canvas()
    fh, fb = font(76, True), font(38)
    d.rectangle([0, 0, 16, H], fill=accent)
    y = 300
    d.text((150, y), head, font=fh, fill=WHITE)
    y += 130
    for ln in lines:
        if not ln:
            y += 26
            continue
        for part in wrap(d, ln, fb, W - 320):
            d.text((152, y), part, font=fb, fill=PALE)
            y += 58
    d.text((150, H - 90), "CareShift Guard", font=font(26), fill=(90, 130, 140))
    img.save(path)


def step_frame(path: Path, num: int, shot: Path, head: str, note: str,
               box: tuple[int, int, int, int] | None) -> bool:
    """操作1手順ぶんのコマを描く。画像が無ければ False。"""
    if not shot.exists() or shot.stat().st_size == 0:
        return False

    img, d = base_canvas()
    src = Image.open(shot).convert("RGB")

    # 画面を領域に収める（拡大はしない。文字がぼやけるため）
    bl, bt, br, bb = SHOT_BOX
    max_w, max_h = br - bl, bb - bt
    scale = min(max_w / src.width, max_h / src.height, 1.0)
    sw, sh = int(src.width * scale), int(src.height * scale)
    if scale < 1.0:
        src = src.resize((sw, sh), Image.LANCZOS)
    ox = bl + (max_w - sw) // 2
    oy = bt + (max_h - sh) // 2

    # 白い画面が地に溶けないよう枠を敷く
    d.rectangle([ox - 4, oy - 4, ox + sw + 4, oy + sh + 4], fill=(230, 238, 240))
    img.paste(src, (ox, oy))

    # 注目してほしい範囲を囲む
    if box is not None:
        x1, y1, x2, y2 = (int(v * scale) for v in box)
        d.rectangle([ox + x1, oy + y1, ox + x2, oy + y2],
                    outline=ALERT, width=5)

    # 手順番号と見出し
    fnum, fhead = font(34, True), font(44, True)
    chip = f"手順 {num}"
    cw = int(d.textlength(chip, font=fnum)) + 44
    d.rounded_rectangle([110, 52, 110 + cw, 116], radius=32, fill=DEEP)
    d.text((110 + 22, 63), chip, font=fnum, fill=WHITE)
    d.text((110 + cw + 28, 58), head, font=fhead, fill=WHITE)

    # 説明帯
    d.rectangle([0, CAP_TOP, W, H], fill=BAND)
    fn = font(33)
    lines = wrap(d, note, fn, W - 240)[:3]
    y = CAP_TOP + (H - CAP_TOP - len(lines) * 46) // 2
    for ln in lines:
        d.text((120, y), ln, font=fn, fill=(214, 232, 236))
        y += 46

    img.save(path)
    return True


def draw_all() -> list[tuple[Path, float]]:
    WORK.mkdir(parents=True, exist_ok=True)
    seq: list[tuple[Path, float]] = []
    skipped: list[str] = []
    for i, item in enumerate(PLAN):
        p = WORK / f"f{i:03d}.png"
        if item[0] == "card":
            _, head, lines, sec, accent = item
            title_card(p, head, lines, accent)
            seq.append((p, float(sec)))
        else:
            _, num, file, head, note, sec, box = item
            if step_frame(p, num, IMG / file, head, note, box):
                seq.append((p, float(sec)))
            else:
                skipped.append(file)
    if skipped:
        print("  画像が無いため省略: " + ", ".join(skipped))
    return seq


# ------------------------------------------------------------------ 字幕
def srt_time(sec: float) -> str:
    """SRT の時刻表記に直す（HH:MM:SS,mmm）。"""
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(dst: Path = SRT_DEFAULT) -> Path:
    """字幕ファイルを書き出す。

    説明文は動画の各コマに焼き込んであるので、再生環境を問わず読める。
    それとは別に SRT も出しておく理由は次の2点。
      ・PowerPoint は埋め込んだ動画に字幕を紐づけて表示・非表示を
        切り替えられる（映像を隠さずに読みたい場合に使える）
      ・全体の説明文を平文で見直せるため、原稿の校正がしやすい
    """
    lines: list[str] = []
    t = 0.0
    n = 0
    for item in PLAN:
        if item[0] == "card":
            _, head, body, sec, _ = item
            text = head + "\n" + "　".join(x for x in body if x)
        else:
            _, num, _file, head, note, sec, _box = item
            text = f"手順{num}　{head}\n{note}"
        n += 1
        lines.append(str(n))
        lines.append(f"{srt_time(t)} --> {srt_time(t + sec)}")
        lines.append(text)
        lines.append("")
        t += float(sec)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # 多くの再生環境は BOM 付き UTF-8 を前提に文字集合を判定する。
    dst.write_text("\n".join(lines), encoding="utf-8-sig")
    print(f"字幕: {dst}（{n} 件 / {t:.0f}秒）")
    return dst


# ------------------------------------------------------------------ 符号化
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
    write_srt()
    total = sum(s for _, s in seq)
    print(f"場面 {len(seq)} 本 / 合計 {total:.0f}秒")
    if total > 180:
        print(f"  警告: 3分（180秒）を超えています（{total:.0f}秒）")

    lo, hi = 0, len(seq) - 1
    if "--segments" in args:
        k = args.index("--segments")
        lo, hi = int(args[k + 1]), int(args[k + 2])

    for i in range(lo, min(hi, len(seq) - 1) + 1):
        png, sec = seq[i]
        dst = WORK / f"seg_{i:03d}.mp4"
        encode_segment(png, sec, dst)
        kb = dst.stat().st_size // 1024
        print(f"  seg_{i:03d}  {sec:5.0f}秒  {kb:6d} KB")

    print(f"区間 {lo}〜{hi} を符号化しました。")
    done = len(list(WORK.glob("seg_*.mp4")))
    if done == len(seq):
        print("すべて揃いました。--concat で仕上げてください。")
    else:
        print(f"残り {len(seq) - done} 本。--segments で続けてください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
