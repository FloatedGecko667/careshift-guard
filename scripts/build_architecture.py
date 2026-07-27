"""システム構成図を描く。

レポートとスライドに貼る図を、手作業ではなくコードから作る。
構成を変えたときに図だけ古いまま残ることを防ぐためである。

構成の考え方はこの図がそのまま示す。
  ・サーバーは1台。nginx・web・db を同一インスタンスで動かす。
    シフト作成は月次の業務であり、停止が直ちに介護サービスを
    止めるものではない。確定したシフトは Excel と印刷で手元に残る。
    冗長化に月額を倍払う合理性がないため、単一構成を選ぶ。
  ・ロードバランサを置かない。TLS は nginx で終端する。
    構成要素が1つ減り、障害点も1つ減る。
    契約数が増えて2台目を並べる段階で導入する。

使い方:
    python3 -m scripts.build_architecture
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path("docs/images/00_architecture.png")

# 3倍で描いて縮小する。斜線と文字の縁が滑らかになる。
SCALE = 3
W, H = 1500, 972

# 配色。レポート本文の図表と揃える。
BG = (255, 255, 255)
INK = (31, 41, 55)
LINE = (110, 128, 145)
CLIENT = (232, 240, 252)
CLOUD_BG = (238, 247, 241)
CLOUD_LINE = (150, 196, 170)
VM_LINE = (176, 190, 205)
NGINX = (219, 234, 254)
WEB = (253, 230, 205)
DB = (222, 231, 244)
STORE = (226, 232, 243)
MON = (238, 228, 245)
CI = (253, 240, 210)

CJK_REG = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
]
CJK_BOLD = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
]


def font(size: int, bold: bool = False):
    for p in (CJK_BOLD if bold else CJK_REG):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size * SCALE)
            except OSError:
                continue
    return ImageFont.load_default()


class Canvas:
    """SCALE 倍の座標系で描き、最後に縮小する。"""

    def __init__(self, w: int, h: int) -> None:
        self.img = Image.new("RGB", (w * SCALE, h * SCALE), BG)
        self.d = ImageDraw.Draw(self.img)

    def box(self, xy, fill, outline=LINE, radius=10, width=1.5, dash=False):
        x1, y1, x2, y2 = (v * SCALE for v in xy)
        if dash:
            # 破線の枠は「論理的なまとまり」を示すのに使う
            self.d.rounded_rectangle([x1, y1, x2, y2], radius=radius * SCALE,
                                     fill=fill, outline=None)
            self._dashed_rect(x1, y1, x2, y2, outline, width)
        else:
            self.d.rounded_rectangle([x1, y1, x2, y2], radius=radius * SCALE,
                                     fill=fill, outline=outline,
                                     width=int(width * SCALE))

    def _dashed_rect(self, x1, y1, x2, y2, color, width):
        step, on = 9 * SCALE, 5 * SCALE
        w = int(width * SCALE)
        for x in range(int(x1), int(x2), step):
            self.d.line([x, y1, min(x + on, x2), y1], fill=color, width=w)
            self.d.line([x, y2, min(x + on, x2), y2], fill=color, width=w)
        for y in range(int(y1), int(y2), step):
            self.d.line([x1, y, x1, min(y + on, y2)], fill=color, width=w)
            self.d.line([x2, y, x2, min(y + on, y2)], fill=color, width=w)

    def cylinder(self, xy, fill, outline=LINE):
        """ストレージは円柱で描く。箱と区別が付く。"""
        x1, y1, x2, y2 = (v * SCALE for v in xy)
        ry = 9 * SCALE
        self.d.rectangle([x1, y1 + ry, x2, y2 - ry], fill=fill,
                         outline=None)
        self.d.ellipse([x1, y2 - ry * 2, x2, y2], fill=fill,
                       outline=outline, width=int(1.5 * SCALE))
        self.d.ellipse([x1, y1, x2, y1 + ry * 2], fill=fill,
                       outline=outline, width=int(1.5 * SCALE))
        for x in (x1, x2):
            self.d.line([x, y1 + ry, x, y2 - ry], fill=outline,
                        width=int(1.5 * SCALE))

    def text(self, xy, lines, size=13, bold=False, color=INK,
             align="center", lh=1.45):
        if isinstance(lines, str):
            lines = [lines]
        f = font(size, bold)
        x, y = (v * SCALE for v in xy)
        step = size * lh * SCALE
        for i, ln in enumerate(lines):
            w = self.d.textlength(ln, font=f)
            px = x - w / 2 if align == "center" else x
            self.d.text((px, y + i * step), ln, font=f, fill=color)

    def arrow(self, p1, p2, color=LINE, width=1.6, dash=False, label=None,
              label_dx=0, label_dy=-9):
        x1, y1 = (v * SCALE for v in p1)
        x2, y2 = (v * SCALE for v in p2)
        w = int(width * SCALE)
        if dash:
            self._dashed_line(x1, y1, x2, y2, color, w)
        else:
            self.d.line([x1, y1, x2, y2], fill=color, width=w)
        # 矢じり
        import math
        ang = math.atan2(y2 - y1, x2 - x1)
        size = 7 * SCALE
        for s in (0.42, -0.42):
            self.d.line([x2, y2,
                         x2 - size * math.cos(ang - s),
                         y2 - size * math.sin(ang - s)],
                        fill=color, width=w)
        if label:
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            f = font(11)
            tw = self.d.textlength(label, font=f)
            self.d.text((mx - tw / 2 + label_dx * SCALE,
                         my + label_dy * SCALE), label, font=f, fill=(90, 105, 120))

    def _dashed_line(self, x1, y1, x2, y2, color, w):
        import math
        dist = math.hypot(x2 - x1, y2 - y1)
        step = 11 * SCALE
        n = max(int(dist / step), 1)
        for i in range(n):
            t1, t2 = i / n, min((i + 0.55) / n, 1.0)
            self.d.line([x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1,
                         x1 + (x2 - x1) * t2, y1 + (y2 - y1) * t2],
                        fill=color, width=w)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.img.resize((self.img.width // SCALE, self.img.height // SCALE),
                        Image.LANCZOS).save(path)


def build() -> Path:
    c = Canvas(W, H)

    # ------------------------------------------------ 利用者環境
    c.box((470, 20, 1030, 132), CLIENT, outline=(178, 200, 232), radius=12)
    c.text((750, 32), "利用者環境（クライアント）", 14, bold=True)
    c.box((500, 62, 736, 120), (255, 255, 255), outline=(178, 200, 232))
    c.text((618, 72), ["施設管理者 PC", "Chrome / Edge"], 12)
    c.box((764, 62, 1000, 120), (255, 255, 255), outline=(178, 200, 232))
    c.text((882, 72), ["介護職員 スマートフォン", "Safari / Chrome"], 12)

    # ------------------------------------------------ OCI 全体
    c.box((30, 176, 1470, 980), CLOUD_BG, outline=CLOUD_LINE, radius=14)
    c.text((750, 190), "Oracle Cloud Infrastructure（大阪リージョン / IaaS）",
           15, bold=True)

    # ------------------------------------------------ VM（1台）
    c.box((300, 236, 1200, 690), (255, 255, 255), outline=VM_LINE, radius=12)
    c.text((750, 250),
           "VM.Standard.A1.Flex　2 OCPU / 8GB　Ubuntu Server 26.04 LTS（aarch64）",
           13, bold=True)
    c.text((750, 272), "Docker Compose（3コンテナ）", 12, color=(105, 118, 132))

    c.box((470, 304, 1030, 372), NGINX, outline=(150, 180, 225))
    c.text((750, 316), ["① nginx　リバースプロキシ / 静的配信",
                        "TLS 1.3 終端（Let's Encrypt・Certbot で自動更新）"], 12)

    c.box((400, 402, 1100, 512), WEB, outline=(224, 178, 120))
    c.text((750, 414), ["② web　FastAPI + Jinja2 + htmx（uvicorn ワーカー2）",
                        "OR-Tools CP-SAT（シフト最適化・同期実行・上限10秒）",
                        "常勤換算算定 / 基準適合判定 / 勤務形態一覧表の出力",
                        "監査ログの記録（追記専用）"], 12)

    c.box((470, 546, 1030, 638), DB, outline=(160, 178, 205))
    c.text((750, 558), ["③ db　PostgreSQL 18",
                        "テナント分離（office_id によるスコープ制御）",
                        "12テーブル / 追記専用の監査ログ"], 12)

    # 見出しと重ならない位置に注記を置く。
    # 既定の中点だと「Oracle Cloud Infrastructure」の行に被る。
    c.arrow((618, 120), (700, 304), label="HTTPS", label_dx=-34, label_dy=14)
    c.arrow((882, 120), (800, 304), label="HTTPS", label_dx=34, label_dy=14)
    c.arrow((750, 372), (750, 402))
    c.arrow((750, 512), (750, 546), label="SQL", label_dx=22, label_dy=-8)

    # ------------------------------------------------ ストレージ
    c.cylinder((330, 730, 660, 830), STORE)
    c.text((495, 752), ["ブートボリューム 50GB", "（Balanced / 日次自動バックアップ）"], 12)
    c.cylinder((840, 730, 1170, 830), STORE)
    c.text((1005, 752), ["Object Storage 20GB", "（世代バックアップ・東京へ複製）"], 12)

    c.arrow((620, 638), (520, 726), label="永続化", label_dx=-26, label_dy=-4)
    c.arrow((880, 638), (980, 726), label="日次取得", label_dx=30, label_dy=-4)

    # ------------------------------------------------ 監視
    c.box((60, 380, 268, 460), MON, outline=(196, 176, 216))
    c.text((164, 394), ["OCI Monitoring / Alarms", "（標準機能・追加費用なし）",
                        "通知の確認は営業時間内"], 11)
    c.arrow((268, 430), (398, 452), dash=True)

    # ------------------------------------------------ 業務継続
    c.box((60, 700, 268, 830), (255, 251, 235), outline=(224, 196, 120))
    c.text((164, 714), ["停止時の代替手段", "", "確定済みシフトは",
                        "Excel 出力と印刷で", "手元に残るため、", "現場は勤務を継続できる"], 11)

    # ------------------------------------------------ CI
    c.box((1240, 300, 1440, 392), CI, outline=(224, 196, 120))
    c.text((1340, 314), ["GitHub Actions", "CI / コンテナイメージ配信",
                         "（当社の開発環境。顧客の", "稼働経路には含まれない）"], 11)
    c.arrow((1240, 346), (1032, 338), dash=True, label="デプロイ",
            label_dx=0, label_dy=-20)

    # ------------------------------------------------ 注記
    c.text((750, 892),
           "ロードバランサと待機系は置かない。月次のシフト作成を支援する用途であり、"
           "冗長化に月額を倍払う合理性がないため。", 12,
           color=(95, 110, 125))
    c.text((750, 916),
           "契約が30事業所を超えた段階で、メモリの増強、"
           "次いでロードバランサ配下へのインスタンス追加で対応する。", 12,
           color=(95, 110, 125))
    c.save(OUT)
    return OUT


def main() -> int:
    p = build()
    im = Image.open(p)
    print(f"出力: {p}  ({im.width}x{im.height})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
