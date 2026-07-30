"""レポート5の記載を実装の実態に一致させる。

課題文が「このレポートと最終レポートで報告したものが一致していること」を
要求しているため、実装済みの機能が「将来のリリース」または未記載になって
いる箇所を実装に合わせて書き換える。

照合で判明した不一致（8件）
  1. 監査ログ閲覧が第3リリース扱い          → 実装済みなので初版へ移す
  2. テーブル一覧が10件（実装は12件）        → 2件を追記
  3. 画面一覧が5件（実装は9件）              → 4件を追記
  4. 「5画面・10テーブル」という記述          → 9画面・12テーブルへ
  5. Tailwind CSS を使用ソフトに記載          → 未使用なので削除
  6. 「VM1・VM2のホストOS」（1台構成と矛盾）  → 1台構成の表記へ
  7. SQLAlchemy を「O/Rマッパー」と記載       → Core 利用の表記へ
  8. pg8000 / itsdangerous / python-multipart → 実依存なので追記

使い方:
    python3 -m scripts.fix_report5_consistency
"""
from __future__ import annotations

import re
import shutil
import sys
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path


def _find_doc() -> Path:
    """レポート5の docx を探す。

    macOS 版 Word は保存時にファイル名を NFD で書き戻すため、
    NFC のパターンで glob すると一致しない。正規化して比較する。
    """
    want = unicodedata.normalize("NFC", "レポート5")
    for p in Path("docs").glob("*.docx"):
        if ".bak." in p.name:
            continue
        if want in unicodedata.normalize("NFC", p.name):
            return p
    raise FileNotFoundError("レポート5の docx が docs/ に見つからない")


DOC = _find_doc()
TARGET = "word/document.xml"


# ---------------------------------------------------------------- 低レベル操作
def cell_text(tc: str) -> str:
    return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", tc)).strip()


def split_cells(tr: str) -> list[str]:
    return re.findall(r"<w:tc>.*?</w:tc>", tr, re.S)


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def set_cell(tc: str, text: str) -> str:
    """セル内の段落を1つにまとめ、指定した文字列だけを入れる。

    書式を失わないよう、セルの属性（tcPr）と最初の段落・実行の
    書式（pPr / rPr）はそのまま流用する。
    """
    tcpr = re.search(r"<w:tcPr>.*?</w:tcPr>", tc, re.S)
    ppr = re.search(r"<w:pPr>.*?</w:pPr>", tc, re.S)
    rpr = re.search(r"<w:rPr>.*?</w:rPr>", tc, re.S)
    body = (
        "<w:p>"
        + (ppr.group(0) if ppr else "")
        + "<w:r>"
        + (rpr.group(0) if rpr else "")
        + f'<w:t xml:space="preserve">{esc(text)}</w:t>'
        + "</w:r></w:p>"
    )
    return "<w:tc>" + (tcpr.group(0) if tcpr else "") + body + "</w:tc>"


def make_row(template: str, values: list[str]) -> str:
    """テンプレート行の書式を流用して1行を組み立てる。"""
    cells = split_cells(template)
    if len(cells) != len(values):
        raise ValueError(f"列数が合わない: 雛形{len(cells)} 指定{len(values)}")
    out = template
    for tc, v in zip(cells, values, strict=True):
        out = out.replace(tc, set_cell(tc, v), 1)
    return out


class Doc:
    def __init__(self, xml: str) -> None:
        self.xml = xml
        self.log: list[str] = []

    # ---- 表の取得
    def table(self, header: list[str]) -> str:
        """先頭行の文字列が一致する表を返す。"""
        for t in re.findall(r"<w:tbl>.*?</w:tbl>", self.xml, re.S):
            rows = re.findall(r"<w:tr[ >].*?</w:tr>", t, re.S)
            if rows and [cell_text(c) for c in split_cells(rows[0])] == header:
                return t
        raise LookupError(f"表が見つからない: {header}")

    def add_rows(self, header: list[str], rows: list[list[str]]) -> None:
        """表の末尾に行を足す。最終行を書式の雛形として使う。"""
        tbl = self.table(header)
        trs = re.findall(r"<w:tr[ >].*?</w:tr>", tbl, re.S)
        template = trs[-1]
        added = "".join(make_row(template, r) for r in rows)
        new = tbl.replace(template, template + added, 1)
        self._swap(tbl, new)
        self.log.append(f"表{header[:2]} に {len(rows)} 行追加")

    def drop_row(self, header: list[str], match: str) -> None:
        """指定した文字列を含む行を削除する。"""
        tbl = self.table(header)
        for tr in re.findall(r"<w:tr[ >].*?</w:tr>", tbl, re.S):
            if match in "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", tr)):
                self._swap(tbl, tbl.replace(tr, "", 1))
                self.log.append(f"表{header[:2]} から「{match}」の行を削除")
                return
        raise LookupError(f"行が見つからない: {match}")

    def renumber(self, header: list[str]) -> None:
        """No 列を1から振り直す。行の増減後に必ず呼ぶ。"""
        tbl = self.table(header)
        new, n = tbl, 0
        for tr in re.findall(r"<w:tr[ >].*?</w:tr>", tbl, re.S)[1:]:
            first = split_cells(tr)[0]
            if not cell_text(first).isdigit():
                continue
            n += 1
            new = new.replace(tr, tr.replace(first, set_cell(first, str(n)), 1), 1)
        self._swap(tbl, new)
        self.log.append(f"表{header[:2]} の No を1〜{n}に振り直し")

    # ---- 文字列の置換
    def replace(self, old: str, new: str, *, count: int = 1) -> None:
        """本文の文字列を置換する。

        Word は1つの文と複数の <w:t> に分割することがあるため、
        まず素の置換を試し、失敗したら分割を吸収して再試行する。
        """
        if self.xml.count(old) >= count:
            self.xml = self.xml.replace(old, esc_keep(new), count)
            self.log.append(f"置換: {old[:34]}… → {new[:34]}…")
            return
        # <w:t>…</w:t> をまたぐ場合に備え、タグを挟んだ形も許すパターンを作る
        pat = "".join(
            re.escape(ch) + r"(?:</w:t>\s*</w:r>\s*<w:r[^>]*>(?:<w:rPr>.*?</w:rPr>)?"
            r"<w:t[^>]*>)?"
            for ch in old
        )
        m = re.search(pat, self.xml, re.S)
        if not m:
            raise LookupError(f"置換対象が見つからない: {old[:48]}")
        self.xml = self.xml[: m.start()] + esc_keep(new) + self.xml[m.end():]
        self.log.append(f"置換(分割吸収): {old[:30]}… → {new[:30]}…")

    def _swap(self, old: str, new: str) -> None:
        assert old in self.xml, "対象の表が本文に無い"
        self.xml = self.xml.replace(old, new, 1)


def esc_keep(s: str) -> str:
    """置換後の文字列。タグを含めないため実体参照だけ処理する。"""
    return s.replace("&", "&amp;")


# ---------------------------------------------------------------- 修正の本体
SOFT = ["No", "ソフトウェア", "バージョン", "用途", "ライセンス", "商用利用"]
SCREEN = ["No", "画面名", "概要", "主な利用者"]
TABLE = ["No", "テーブル名（物理）", "論理名", "主な保持項目", "備考"]
ROADMAP = ["リリース", "追加する機能", "狙い"]


def apply(d: Doc) -> None:
    # --- 5. Tailwind CSS を削除し、8. 実依存を追記する
    d.drop_row(SOFT, "Tailwind CSS")
    d.add_rows(SOFT, [
        ["0", "pg8000", "1.31系",
         "PostgreSQL 接続ドライバ（純Python実装）",
         "3条項BSDライセンス", "可"],
        ["0", "itsdangerous", "2.2系",
         "セッションCookieの署名（改ざん検知）",
         "3条項BSDライセンス", "可"],
        ["0", "python-multipart", "0.0.20系",
         "HTMLフォーム送信の解析",
         "Apache License 2.0", "可"],
    ])
    d.renumber(SOFT)

    # --- 6. 1台構成と矛盾する記述を直す
    d.replace("VM1・VM2のホストOS", "ホストOS（本番は1台構成）")

    # --- 7. SQLAlchemy の用途を実態に合わせる
    d.replace("O/Rマッパー",
              "データベース接続とSQL実行（O/Rマッパーは使わず"
              "SQLAlchemy Core として利用）")

    # --- 3. 実装済みの画面を初版へ追記する
    d.add_rows(SCREEN, [
        ["0", "アカウント管理",
         "管理者が職員用のログインアカウントを発行し、職員マスタと紐付ける。"
         "権限区分の変更、退職者アカウントの無効化、パスワード再設定リンクの"
         "発行を行う。最後の管理者を無効化できないなどの安全確認を備える。",
         "管理者"],
        ["0", "監査ログ閲覧",
         "ログイン、シフトの生成・修正・確定、アカウント操作の記録を"
         "追記専用で保持し、日時・操作者・対象・IPアドレスとともに一覧する。"
         "実地指導での説明責任と、不正アクセスの追跡に用いる。",
         "管理者"],
        ["0", "パスワード変更",
         "利用者が自身のパスワードを変更する。現在のパスワードの確認を求め、"
         "変更後は他端末の既存セッションを失効させる。",
         "全利用者"],
        ["0", "パスワード再設定",
         "管理者が発行した単回限り・有効期限つきのリンクから、職員が自分で"
         "パスワードを設定する。トークンは平文で保存せずハッシュで照合する。",
         "全利用者"],
    ])
    d.renumber(SCREEN)

    # --- 2. 実装済みのテーブルを初版へ追記する
    d.add_rows(TABLE, [
        ["0", "password_reset_tokens", "パスワード再設定トークン",
         "トークンID、ユーザーID、トークンのハッシュ値、有効期限、使用日時、発行者",
         "単回限り。平文は保存しない"],
        ["0", "audit_logs", "監査ログ",
         "監査ID、事業所ID、操作者、操作種別、対象、要約、IPアドレス、記録日時",
         "追記専用。更新・削除・切り詰めをデータベース側で拒否する"],
    ])
    d.renumber(TABLE)

    # --- 1. 監査ログ閲覧を第3リリースから外す
    d.replace(
        "複数事業所の横断ダッシュボード、勤務負荷分析（夜勤・土日勤務の偏りの"
        "可視化）、監査ログ閲覧",
        "複数事業所の横断ダッシュボード、勤務負荷分析"
        "（夜勤・土日勤務の偏りの可視化）")
    d.replace(
        "第2リリース以降に追加を予定している画面（ダッシュボード、実績勤務入力、"
        "勤務負荷分析、監査ログ閲覧、複数事業所管理、通知設定など）",
        "第2リリース以降に追加を予定している画面（ダッシュボード、実績勤務入力、"
        "勤務負荷分析、複数事業所管理、通知設定など）")

    # --- 4. 画面数・テーブル数の記述を実態に合わせる
    d.replace("画面数を5に絞り込んでいる",
              "画面数を9に抑えている")
    d.replace("初版は5画面・10テーブルの最小構成で提供し",
              "初版は9画面・12テーブルの構成で提供し")
    # 収益性の節にも画面数の記述がある。損益分岐点の算出はインフラ原価と
    # 価格設定から導いており、画面数には依存しないため数値は変えない。
    d.replace("機能を初版5画面に絞り込み",
              "機能を運用に必要な9画面へ絞り込み")


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = DOC.with_suffix(f".bak.{stamp}.docx")
    shutil.copy2(DOC, backup)
    print(f"バックアップ: {backup.name}")

    with zipfile.ZipFile(DOC) as z:
        items = {n: z.read(n) for n in z.namelist()}

    d = Doc(items[TARGET].decode("utf-8"))
    apply(d)
    items[TARGET] = d.xml.encode("utf-8")

    # 同名で書き戻す。圧縮方式は元と同じ deflate を使う。
    tmp = DOC.with_suffix(".tmp.docx")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in items.items():
            z.writestr(n, data)
    tmp.replace(DOC)

    for line in d.log:
        print(f"  {line}")
    print(f"完了: {DOC.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
