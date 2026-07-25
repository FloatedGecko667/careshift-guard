/**
 * 発表スライドを生成する。
 *
 * docs/images/ に画像があれば埋め込み、無ければ撮影指示つきの枠を描く。
 * スクリーンショットを撮ったあとに再実行すれば完成版になる。
 *
 * 使い方:
 *   node scripts/build_slides.js [出力先.pptx]
 */
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");

const OUT = process.argv[2] || "docs/発表スライド_CareShift_Guard.pptx";
const IMG = "docs/images";

// ---------------------------------------------------------------- 配色
// 介護と「守る」という主題に合わせ、深い青緑を基調にする。
const C = {
  dark:  "0B3C49",   // 表題・結論スライドの地
  deep:  "028090",   // 主色
  mint:  "02C39A",   // 強調
  alert: "C0392B",   // 減算・違反
  ink:   "1F2937",   // 本文
  muted: "6B7280",   // 補足
  line:  "D7DCE3",
  bg:    "F2F7F8",   // 明るい地
  white: "FFFFFF",
};

const F = { head: "Cambria", body: "Calibri" };

// リポジトリと公開先。未確定のものは「準備中」と出す。
const LINKS = {
  repo:  "https://github.com/FloatedGecko667/careshift-guard",
  video: process.env.PV_URL || "（紹介動画URL：撮影後に差し替え）",
  demo:  process.env.DEMO_URL || "（デモ環境URL：デプロイ後に差し替え）",
};

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";           // 13.3 x 7.5 インチ
pres.author = "曽我 幸太郎";
pres.title = "CareShift Guard";

const W = 13.3, H = 7.5, M = 0.6;      // 幅・高さ・余白

// ---------------------------------------------------------------- 部品
function titleBar(slide, text, sub) {
  slide.addText(text, {
    x: M, y: 0.42, w: W - M * 2, h: 0.7,
    fontFace: F.head, fontSize: 32, bold: true, color: C.dark, margin: 0,
  });
  if (sub) {
    slide.addText(sub, {
      x: M, y: 1.12, w: W - M * 2, h: 0.34,
      fontFace: F.body, fontSize: 14, color: C.muted, margin: 0,
    });
  }
}

function card(slide, o) {
  slide.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: 0.08,
    fill: { color: o.fill || C.white },
    line: { color: o.lineColor || C.line, width: 1 },
    shadow: { type: "outer", angle: 90, blur: 6, offset: 0.03,
              color: "000000", opacity: 0.08 },
  });
}

function numberBadge(slide, n, x, y, d = 0.42, fill = C.deep) {
  slide.addShape(pres.ShapeType.ellipse, {
    x, y, w: d, h: d, fill: { color: fill }, line: { color: fill, width: 0 },
  });
  slide.addText(String(n), {
    x, y, w: d, h: d, align: "center", valign: "middle",
    fontFace: F.body, fontSize: 15, bold: true, color: C.white, margin: 0,
  });
}

function stat(slide, o) {
  slide.addText(o.value, {
    x: o.x, y: o.y, w: o.w, h: o.vh || 1.0,
    fontFace: F.head, fontSize: o.size || 54, bold: true,
    color: o.color || C.alert, align: o.align || "left", margin: 0,
  });
  slide.addText(o.label, {
    x: o.x, y: o.y + (o.vh || 1.0) - 0.06, w: o.w, h: 0.5,
    fontFace: F.body, fontSize: 12, color: C.muted,
    align: o.align || "left", margin: 0,
  });
}

/** 画像があれば貼り、無ければ撮影指示つきの枠を描く。 */
function shot(slide, file, o) {
  const p = path.join(IMG, file);
  if (fs.existsSync(p) && fs.statSync(p).size > 0) {
    slide.addImage({ path: p, x: o.x, y: o.y, w: o.w, h: o.h,
                     sizing: { type: "contain", w: o.w, h: o.h } });
    return true;
  }
  slide.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: 0.06,
    fill: { color: C.bg },
    line: { color: C.deep, width: 1.5, dashType: "dash" },
  });
  // 配列で渡す場合、揃えは run ごとに指定しないと反映されない
  const ac = { align: "center" };
  slide.addText(
    [{ text: "未撮影", options: { ...ac, fontSize: 13, bold: true, color: C.deep, breakLine: true } },
     { text: `${IMG}/${file}`, options: { ...ac, fontSize: 10, color: C.muted, breakLine: true } },
     { text: o.hint || "", options: { ...ac, fontSize: 10, color: C.muted } }],
    { x: o.x + 0.1, y: o.y, w: o.w - 0.2, h: o.h,
      align: "center", valign: "middle", fontFace: F.body, margin: 0 });
  return false;
}

const missing = [];
function shotTracked(slide, file, o) {
  if (!shot(slide, file, o)) missing.push(`${file}  — ${o.hint || ""}`);
}

// ================================================================ 1 表題
{
  const s = pres.addSlide();
  s.background = { color: C.dark };
  s.addText("CareShift Guard", {
    x: M, y: 2.0, w: W - M * 2, h: 1.0,
    fontFace: F.head, fontSize: 54, bold: true, color: C.white, margin: 0,
  });
  s.addText("介護事業所向け　人員配置基準チェック内蔵 シフト自動作成クラウド", {
    x: M, y: 3.05, w: W - M * 2, h: 0.5,
    fontFace: F.body, fontSize: 19, color: C.mint, margin: 0,
  });
  s.addText("シフト作成を楽にする道具ではありません。" +
            "人員基準欠如減算という経営リスクを未然に防ぐ仕組みです。", {
    x: M, y: 3.62, w: W - M * 2, h: 0.5,
    fontFace: F.body, fontSize: 13, color: "AFC7CE", margin: 0,
  });

  card(s, { x: M, y: 4.7, w: W - M * 2, h: 1.5, fill: "0F4C5C",
            lineColor: "0F4C5C" });
  s.addText([
    { text: "GitHub リポジトリ　", options: { bold: true, color: C.mint } },
    { text: LINKS.repo, options: { color: C.white } },
    { text: "\n紹介動画　　　　　　", options: { bold: true, color: C.mint } },
    { text: LINKS.video, options: { color: C.white } },
    { text: "\nデモ環境　　　　　　", options: { bold: true, color: C.mint } },
    { text: LINKS.demo, options: { color: C.white } },
  ], { x: M + 0.3, y: 4.85, w: W - M * 2 - 0.6, h: 1.2,
       fontFace: F.body, fontSize: 12, margin: 0, lineSpacingMultiple: 1.2 });

  s.addText("クラウドプラットフォーム実習Ⅱ　最終レポート　2026年度　" +
            "学籍番号 20122049　曽我 幸太郎", {
    x: M, y: 6.5, w: W - M * 2, h: 0.4,
    fontFace: F.body, fontSize: 11, color: "8FAAB3", margin: 0,
  });
  s.addNotes("30秒。名乗りと一行の価値提案のみ。" +
             "『シフト作成ツールではなく、減算を防ぐ仕組み』と言い切る。");
}

// ================================================================ 2 課題
{
  const s = pres.addSlide();
  titleBar(s, "解決する課題", "人員基準欠如減算は、事故ではなく運用で防げる損失です");

  card(s, { x: M, y: 1.7, w: 6.0, h: 2.3 });
  stat(s, { x: M + 0.35, y: 1.95, w: 5.3, value: "▲30%", size: 60,
            label: "人員基準を満たさないと、基本報酬が30パーセント減算される" });

  card(s, { x: M + 6.4, y: 1.7, w: W - M * 2 - 6.4, h: 2.3 });
  stat(s, { x: M + 6.75, y: 1.95, w: W - M * 2 - 7.1, value: "480万円", size: 54,
            label: "月商800万円の事業所で2か月継続した場合の逸失収益" });

  const rows = [
    ["減算は利用者全員に適用される", "一部ではなく事業所の全報酬が対象になる"],
    ["欠如の翌月または翌々月から解消月まで続く", "気づくのが遅れるほど損失が積み上がる"],
    ["原因の多くは急な退職や欠勤への対応の遅れ", "シフトを組む段階で監視できていれば防げる"],
  ];
  let y = 4.3;
  rows.forEach(([a, b], i) => {
    numberBadge(s, i + 1, M, y);
    s.addText(a, { x: M + 0.58, y: y - 0.02, w: 5.2, h: 0.42,
                   fontFace: F.body, fontSize: 14, bold: true, color: C.ink,
                   margin: 0, valign: "middle" });
    s.addText(b, { x: M + 5.9, y: y - 0.02, w: W - M * 2 - 5.9, h: 0.42,
                   fontFace: F.body, fontSize: 13, color: C.muted,
                   margin: 0, valign: "middle" });
    y += 0.62;
  });
  s.addNotes("40秒。30%と480万円の2つの数字だけを言う。" +
             "『事故ではなく、シフトの組み方で防げる損失』が要点。");
}

// ================================================================ 3 なぜ気づけないか
{
  const s = pres.addSlide();
  titleBar(s, "なぜ現場は気づけないのか",
           "月次の集計では「適合」に見える。違反は日単位で起きています");

  card(s, { x: M, y: 1.75, w: 5.9, h: 3.4 });
  s.addText("月次の常勤換算だけを見た場合", {
    x: M + 0.35, y: 1.98, w: 5.2, h: 0.4,
    fontFace: F.body, fontSize: 14, bold: true, color: C.muted, margin: 0 });
  const okRows = [["生活相談員", "1.2 / 1.0"], ["看護職員", "1.4 / 1.0"],
                  ["介護職員", "8.7 / 2.4"], ["機能訓練指導員", "1.4 / 1.0"]];
  let oy = 2.5;
  okRows.forEach(([j, v]) => {
    s.addText(j, { x: M + 0.35, y: oy, w: 2.7, h: 0.36, fontFace: F.body,
                   fontSize: 13, color: C.ink, margin: 0, valign: "middle" });
    s.addText(v, { x: M + 3.1, y: oy, w: 1.3, h: 0.36, fontFace: F.body,
                   fontSize: 13, color: C.ink, margin: 0, valign: "middle",
                   align: "right" });
    s.addText("適合", { x: M + 4.5, y: oy, w: 1.0, h: 0.36, fontFace: F.body,
                        fontSize: 12, bold: true, color: "1E7E46", margin: 0,
                        valign: "middle", align: "center" });
    oy += 0.42;
  });
  s.addText("全職種が基準を満たしている", {
    x: M + 0.35, y: 4.45, w: 5.2, h: 0.5, fontFace: F.body, fontSize: 13,
    bold: true, color: "1E7E46", margin: 0 });

  card(s, { x: M + 6.3, y: 1.75, w: W - M * 2 - 6.3, h: 3.4,
            fill: "FDECEA", lineColor: C.alert });
  s.addText("同じ月を日別に見た場合", {
    x: M + 6.65, y: 1.98, w: 5.2, h: 0.4,
    fontFace: F.body, fontSize: 14, bold: true, color: C.alert, margin: 0 });
  stat(s, { x: M + 6.65, y: 2.5, w: 5.0, value: "8件", size: 46,
            label: "人員配置基準の違反を検出（同一月・同一データ）" });
  s.addText("機能訓練指導員が2名しかいないため、" +
            "両名が休む日に配置数が0になる。", {
    x: M + 6.65, y: 4.05, w: 5.0, h: 0.9, fontFace: F.body, fontSize: 13,
    color: C.ink, margin: 0 });

  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 5.45, w: W - M * 2, h: 1.05, rectRadius: 0.08,
    fill: { color: C.dark }, line: { color: C.dark, width: 0 } });
  s.addText("この乖離の可視化が本ソリューションの中核です。既存ツールは月次集計しか示しません。", {
    x: M + 0.35, y: 5.45, w: W - M * 2 - 0.7, h: 1.05,
    fontFace: F.body, fontSize: 15, bold: true, color: C.white,
    margin: 0, valign: "middle" });
  s.addNotes("40秒。この1枚が差別化の核心。" +
             "『月次では適合、日別では8件違反。同じデータです』と言う。");
}

// ================================================================ 4 提供する機能
{
  const s = pres.addSlide();
  titleBar(s, "提供する機能", "価値の連鎖が1本通ることを最優先に、初版は5画面に絞りました");

  const steps = [
    ["職員が希望を入力", "スマートフォンから希望休と勤務区分を登録"],
    ["制約を満たす解を自動生成", "人員配置基準・労働基準法・就業規則・希望を同時に充足"],
    ["違反を即座に可視化", "常勤換算をシフト表と同一画面に表示し、下回る日を赤で警告"],
    ["実地指導用の帳票を出力", "勤務形態一覧表を算定根拠つきでExcel出力"],
  ];
  const cw = (W - M * 2 - 0.45 * 3) / 4;
  steps.forEach(([t, d], i) => {
    const x = M + (cw + 0.45) * i;
    card(s, { x, y: 1.8, w: cw, h: 2.5 });
    numberBadge(s, i + 1, x + 0.3, 2.05, 0.46);
    s.addText(t, { x: x + 0.3, y: 2.62, w: cw - 0.6, h: 0.7,
                   fontFace: F.body, fontSize: 15, bold: true, color: C.dark,
                   margin: 0 });
    s.addText(d, { x: x + 0.3, y: 3.3, w: cw - 0.6, h: 0.85,
                   fontFace: F.body, fontSize: 11.5, color: C.muted, margin: 0 });
  });

  card(s, { x: M, y: 4.6, w: W - M * 2, h: 1.9, fill: C.bg, lineColor: C.line });
  s.addText([
    { text: "違反が1件でもあると、確定ボタンは押せません。\n",
      options: { bold: true, fontSize: 15, color: C.alert } },
    { text: "判定は画面ではなくデータベース側の条件として書いています。" +
            "アプリの分岐に頼ると、経路が増えたときに漏れます。" +
            "減算リスクを抱えたシフトを職員に配ってしまう事故を、構造で防ぎます。",
      options: { fontSize: 13, color: C.ink } },
  ], { x: M + 0.35, y: 4.75, w: W - M * 2 - 0.7, h: 1.6,
       fontFace: F.body, margin: 0 });
  s.addNotes("30秒。4ステップを指で追いながら。" +
             "最後の『確定できない』は設計判断として強調する。");
}

// ================================================================ 5 デモ シフト表
{
  const s = pres.addSlide();
  titleBar(s, "稼働画面　シフト表", "常勤換算と基準判定を同一画面に置いています");
  shotTracked(s, "07_schedule_violation.png", {
    x: M, y: 1.75, w: W - M * 2, h: 4.5,
    hint: "赤いセル・違反一覧・無効化された確定ボタンが写るように撮る" });
  s.addText("横31日分をスクロールしても、職員名と日付は固定表示されます。" +
            "最下段が職種別の常勤換算（実際／必要）です。", {
    x: M, y: 6.35, w: W - M * 2, h: 0.5,
    fontFace: F.body, fontSize: 12, color: C.muted, margin: 0 });
  s.addNotes("30秒。赤いセルを指して『この日は生活相談員が0.5、基準1.0に対して不足』" +
             "と具体的に言う。");
}

// ================================================================ 6 デモ 帳票
{
  const s = pres.addSlide();
  titleBar(s, "稼働画面　勤務形態一覧表",
           "実地指導の提出様式をExcelで出力。合計と常勤換算は数式で書いています");

  shotTracked(s, "10_kinmu_keitai.png", {
    x: M, y: 1.75, w: 8.0, h: 4.3,
    hint: "兼務者が職種ごとに2行に分かれ、按分されていることが写るように" });

  const notes = [
    ["管理者は常勤換算の対象外", "介護保険法上の規定。職種集合から除いて自動的に除外"],
    ["兼務者は職種ごとに行を分けて按分", "管理者が生活相談員を50％兼務なら、勤務時間の半分を算入"],
    ["合計と常勤換算は Excel の数式", "運営指導の場で担当者がセルを開いて検算できる"],
    ["端数は切り上げない", "利用者22名なら必要常勤換算は2.4。切り上げも切り捨ても誤り"],
  ];
  let ny = 1.75;
  notes.forEach(([t, d]) => {
    card(s, { x: M + 8.4, y: ny, w: W - M * 2 - 8.4, h: 1.0 });
    s.addText(t, { x: M + 8.6, y: ny + 0.1, w: W - M * 2 - 8.8, h: 0.34,
                   fontFace: F.body, fontSize: 12, bold: true, color: C.dark,
                   margin: 0 });
    s.addText(d, { x: M + 8.6, y: ny + 0.42, w: W - M * 2 - 8.8, h: 0.5,
                   fontFace: F.body, fontSize: 10, color: C.muted, margin: 0 });
    ny += 1.11;
  });
  s.addNotes("30秒。『数式で書いてあるので担当者がその場で検算できる』が要点。");
}

// ================================================================ 7 技術構成
{
  const s = pres.addSlide();
  titleBar(s, "技術構成", "Docker Compose 3コンテナ／Oracle Cloud Infrastructure");
  shot(s, "00_architecture.png", { x: M, y: 1.6, w: 8.3, h: 4.7 });

  const items = [
    ["コンテナ技術", "Docker Compose で nginx / web / db の3コンテナ", true],
    ["IaaS", "Oracle Cloud Infrastructure 大阪リージョン Ampere A1（Arm）", true],
    ["AI", "採用しない（次のスライドで理由を述べます）", false],
    ["PaaS", "採用しない（IaaS上のコンテナ構成を選択）", false],
  ];
  let iy = 1.75;
  items.forEach(([t, d, ok]) => {
    card(s, { x: M + 8.6, y: iy, w: W - M * 2 - 8.6, h: 1.05,
              fill: ok ? C.white : C.bg });
    s.addText(ok ? "充足" : "—", {
      x: M + 8.75, y: iy + 0.12, w: 0.7, h: 0.32, fontFace: F.body,
      fontSize: 11, bold: true, color: ok ? C.mint : C.muted,
      align: "center", margin: 0 });
    s.addText(t, { x: M + 9.5, y: iy + 0.1, w: 2.5, h: 0.34, fontFace: F.body,
                   fontSize: 13, bold: true, color: C.dark, margin: 0 });
    s.addText(d, { x: M + 8.75, y: iy + 0.46, w: W - M * 2 - 8.9, h: 0.52,
                   fontFace: F.body, fontSize: 10, color: C.muted, margin: 0 });
    iy += 1.16;
  });
  s.addNotes("25秒。指定要件は『いずれか1つ以上』。2項目を充足していると述べる。");
}

// ================================================================ 8 生成AIを使わない理由
{
  const s = pres.addSlide();
  s.background = { color: C.dark };
  s.addText("生成AIを製品に組み込まない理由", {
    x: M, y: 0.6, w: W - M * 2, h: 0.8,
    fontFace: F.head, fontSize: 32, bold: true, color: C.white, margin: 0 });
  s.addText("これは機能を省いたのではなく、設計判断です", {
    x: M, y: 1.35, w: W - M * 2, h: 0.4,
    fontFace: F.body, fontSize: 14, color: C.mint, margin: 0 });

  const cmp = [
    ["大規模言語モデル", "確率的に出力を生成する",
     "制約に違反した解を、もっともらしく出力しうる", "AFC7CE"],
    ["OR-Tools CP-SAT", "制約充足を決定論的に保証する",
     "制約を満たさない解は、定義上そもそも解ではない", C.mint],
  ];
  cmp.forEach(([t, a, b, col], i) => {
    const x = M + (6.2 + 0.5) * i;
    card(s, { x, y: 2.0, w: 6.2, h: 2.35, fill: "0F4C5C", lineColor: "1A5F72" });
    s.addText(t, { x: x + 0.35, y: 2.2, w: 5.5, h: 0.45, fontFace: F.body,
                   fontSize: 17, bold: true, color: col, margin: 0 });
    s.addText(a, { x: x + 0.35, y: 2.72, w: 5.5, h: 0.4, fontFace: F.body,
                   fontSize: 13, color: C.white, margin: 0 });
    s.addText(b, { x: x + 0.35, y: 3.2, w: 5.5, h: 1.0, fontFace: F.body,
                   fontSize: 12.5, color: "AFC7CE", margin: 0 });
  });

  s.addText("「AIが作ったシフトが人員基準を満たしていなかった」という事故は、" +
            "本ソリューションの存在意義そのものを否定します。", {
    x: M, y: 4.65, w: W - M * 2, h: 0.6,
    fontFace: F.body, fontSize: 16, bold: true, color: C.white, margin: 0 });
  s.addText("減算という金銭的損失に直結する法令適合性の判定に、" +
            "確率的な出力を介在させない。技術選定の段階でその可能性を構造的に排除しました。\n" +
            "機械学習も学習データも使いません。新規の事業所でも初日から使えます。", {
    x: M, y: 5.3, w: W - M * 2, h: 1.0,
    fontFace: F.body, fontSize: 13, color: "AFC7CE", margin: 0,
    lineSpacingMultiple: 1.25 });
  s.addNotes("35秒。ここは自信を持って言い切る。" +
             "『流行だから使う』ではなく『使わない理由を説明できる』ことを示す。");
}

// ================================================================ 9 費用と収益
{
  const s = pres.addSlide();
  titleBar(s, "費用と収益", "補助金も無料枠も前提としません。1米ドル165円で算定");

  const price = [
    ["導入費", "148,000円", "契約時一括／1事業所", "14.0時間の初期作業"],
    ["月額利用料", "16,500円", "1事業所・月", "登録職員数は無制限"],
    ["年間保守費", "29,600円", "契約2年目以降", "導入費の20パーセント"],
  ];
  const pw = (W - M * 2 - 0.4 * 2) / 3;
  price.forEach(([t, v, u, d], i) => {
    const x = M + (pw + 0.4) * i;
    card(s, { x, y: 1.75, w: pw, h: 2.0 });
    s.addText(t, { x: x + 0.3, y: 1.92, w: pw - 0.6, h: 0.34, fontFace: F.body,
                   fontSize: 13, color: C.muted, margin: 0 });
    s.addText(v, { x: x + 0.3, y: 2.24, w: pw - 0.6, h: 0.65, fontFace: F.head,
                   fontSize: 34, bold: true, color: C.deep, margin: 0 });
    s.addText(u, { x: x + 0.3, y: 2.9, w: pw - 0.6, h: 0.3, fontFace: F.body,
                   fontSize: 11, color: C.ink, margin: 0 });
    s.addText(d, { x: x + 0.3, y: 3.2, w: pw - 0.6, h: 0.4, fontFace: F.body,
                   fontSize: 10.5, color: C.muted, margin: 0 });
  });

  const tbl = [
    [{ text: "項目", options: { bold: true } },
     { text: "金額", options: { bold: true, align: "right" } },
     { text: "根拠", options: { bold: true } }],
    ["インフラ原価（1事業所あたり）", { text: "220円/月", options: { align: "right" } },
     "共通基盤 10,999円 ÷ 50テナント"],
    ["月次の粗利", { text: "12,280円", options: { align: "right" } },
     "粗利率 74.4パーセント"],
    ["初期開発費", { text: "816,000円", options: { align: "right" } },
     "136時間 × 6,000円"],
    [{ text: "損益分岐点", options: { bold: true } },
     { text: "4事業所", options: { bold: true, align: "right", color: C.alert } },
     { text: "4事業所と契約した時点で初年度から黒字", options: { bold: true } }],
  ];
  s.addTable(tbl, {
    x: M, y: 4.05, w: W - M * 2, colW: [4.6, 2.2, 5.3],
    fontFace: F.body, fontSize: 12, color: C.ink, border: { color: C.line, pt: 1 },
    fill: { color: C.white }, rowH: 0.42, valign: "middle",
  });

  s.addText("インフラ原価は月額利用料の1.3パーセントに収まります。" +
            "GPUを使わない設計にしたことが、この価格を成立させています。", {
    x: M, y: 6.35, w: W - M * 2, h: 0.5,
    fontFace: F.body, fontSize: 12, color: C.muted, margin: 0 });
  s.addNotes("40秒。損益分岐4事業所を強調。" +
             "『補助金も無料枠も前提にしていない』と明言する。");
}

// ================================================================ 10 経営者への投資対効果
{
  const s = pres.addSlide();
  titleBar(s, "経営者から見た投資対効果",
           "工数削減だけで回収できます。減算回避の価値は含めていません");

  const roi = [
    ["227,600円", "年間の負担額", C.ink, "月額16,500円×12＋保守29,600円"],
    ["420,000円", "管理者工数の削減効果", C.deep, "月14時間×2,500円×12"],
    ["＋192,400円", "差引の純便益", C.mint, "工数削減のみで算定"],
    ["6.5か月", "投資回収期間", C.alert, "減算回避の効果は別途"],
  ];
  const rw = (W - M * 2 - 0.35 * 3) / 4;
  roi.forEach(([v, l, col, d], i) => {
    const x = M + (rw + 0.35) * i;
    card(s, { x, y: 1.75, w: rw, h: 2.15 });
    s.addText(v, { x: x + 0.25, y: 1.95, w: rw - 0.5, h: 0.72, fontFace: F.head,
                   fontSize: 27, bold: true, color: col, margin: 0 });
    s.addText(l, { x: x + 0.25, y: 2.68, w: rw - 0.5, h: 0.4, fontFace: F.body,
                   fontSize: 12.5, bold: true, color: C.ink, margin: 0 });
    s.addText(d, { x: x + 0.25, y: 3.08, w: rw - 0.5, h: 0.6, fontFace: F.body,
                   fontSize: 10, color: C.muted, margin: 0 });
  });

  card(s, { x: M, y: 4.2, w: 6.1, h: 2.25 });
  s.addText("職員数が増えても値上がりしません", {
    x: M + 0.3, y: 4.4, w: 5.5, h: 0.4, fontFace: F.body, fontSize: 14,
    bold: true, color: C.dark, margin: 0 });
  const cmpTbl = [
    [{ text: "職員数", options: { bold: true } },
     { text: "本製品", options: { bold: true, align: "right" } },
     { text: "他社（1人400円）", options: { bold: true, align: "right" } }],
    ["20名", { text: "16,500円", options: { align: "right" } },
     { text: "8,000円", options: { align: "right" } }],
    ["41名", { text: "16,500円", options: { align: "right" } },
     { text: "16,400円", options: { align: "right" } }],
    ["60名", { text: "16,500円", options: { align: "right" } },
     { text: "24,000円", options: { align: "right" } }],
    ["100名", { text: "16,500円", options: { align: "right", bold: true } },
     { text: "40,000円", options: { align: "right" } }],
  ];
  s.addTable(cmpTbl, { x: M + 0.3, y: 4.85, w: 5.5, colW: [1.7, 1.9, 1.9],
    fontFace: F.body, fontSize: 11.5, color: C.ink,
    border: { color: C.line, pt: 1 }, rowH: 0.28, valign: "middle" });

  card(s, { x: M + 6.5, y: 4.2, w: W - M * 2 - 6.5, h: 2.25,
            fill: C.bg, lineColor: C.line });
  s.addText("営業の方針", {
    x: M + 6.8, y: 4.4, w: 5.2, h: 0.4, fontFace: F.body, fontSize: 14,
    bold: true, color: C.dark, margin: 0 });
  s.addText("職員41名を境に価格優位へ転じます。" +
            "小規模事業所には価格では勝てませんが、" +
            "1名の欠員が基準充足に直結するため減算リスクは小規模ほど高い。\n\n" +
            "価格で勝てる領域と、価値で勝てる領域を分けて提案します。", {
    x: M + 6.8, y: 4.85, w: 5.2, h: 1.45, fontFace: F.body, fontSize: 12,
    color: C.ink, margin: 0, lineSpacingMultiple: 1.2 });
  s.addNotes("35秒。『導入しない理由を説明するほうが難しい水準』と締める。");
}

// ================================================================ 11 稼働の証拠
{
  const s = pres.addSlide();
  titleBar(s, "クラウド上で稼働している証拠",
           "Oracle Cloud Infrastructure の Ampere A1（Arm）上で動作しています");

  shotTracked(s, "01_oci_instances.png", {
    x: M, y: 1.7, w: 6.0, h: 2.3,
    hint: "Shape=VM.Standard.A1.Flex・OCPU数・Running が写るように" });
  shotTracked(s, "03_docker_compose_ps.png", {
    x: M + 6.4, y: 1.7, w: W - M * 2 - 6.4, h: 2.3,
    hint: "nginx / web / db の3コンテナが healthy であること" });
  shotTracked(s, "12_make_test.png", {
    x: M, y: 4.15, w: 6.0, h: 2.3,
    hint: "150 passed が読めるように" });
  shotTracked(s, "06_schedule_ok.png", {
    x: M + 6.4, y: 4.15, w: W - M * 2 - 6.4, h: 2.3,
    hint: "アドレスバーにパブリックIPが写るように" });

  s.addText([
    { text: "テスト150件　", options: { bold: true, color: C.deep } },
    { text: "／　制約は独立実装の監査関数で再検査　" +
            "／　Excelの数式はLibreOfficeで実評価　" +
            "／　全SQLをPostgreSQL 18のパーサで検証",
      options: { color: C.muted } },
  ], { x: M, y: 6.55, w: W - M * 2, h: 0.45,
       fontFace: F.body, fontSize: 11, margin: 0 });
  s.addNotes("30秒。『ローカルで動かしただけではない』ことを示す。" +
             "アドレスバーのIPを指す。");
}

// ================================================================ 12 まとめ
{
  const s = pres.addSlide();
  s.background = { color: C.dark };
  s.addText("CareShift Guard", {
    x: M, y: 0.75, w: W - M * 2, h: 0.85,
    fontFace: F.head, fontSize: 40, bold: true, color: C.white, margin: 0 });
  s.addText("提供するのは「シフト表」ではなく、" +
            "人員配置基準に適合していることを継続的に証明できる状態です。", {
    x: M, y: 1.6, w: W - M * 2, h: 0.5,
    fontFace: F.body, fontSize: 16, color: C.mint, margin: 0 });

  const sum = [
    ["守りの成長", "減算リスクを構造的に排除し、収益の下振れ要因を取り除く"],
    ["人的資源の再配分", "管理者の月14時間を、加算算定や職員育成へ振り向ける"],
    ["事業拡大の基盤", "運用ルールが定義として残り、管理者の力量に依存しない運営へ"],
  ];
  const sw = (W - M * 2 - 0.4 * 2) / 3;
  sum.forEach(([t, d], i) => {
    const x = M + (sw + 0.4) * i;
    card(s, { x, y: 2.3, w: sw, h: 1.85, fill: "0F4C5C", lineColor: "1A5F72" });
    numberBadge(s, i + 1, x + 0.28, 2.52, 0.42, C.mint);
    s.addText(t, { x: x + 0.85, y: 2.5, w: sw - 1.1, h: 0.45, fontFace: F.body,
                   fontSize: 15, bold: true, color: C.white, margin: 0,
                   valign: "middle" });
    s.addText(d, { x: x + 0.28, y: 3.1, w: sw - 0.56, h: 0.9, fontFace: F.body,
                   fontSize: 12, color: "AFC7CE", margin: 0 });
  });

  card(s, { x: M, y: 4.45, w: W - M * 2, h: 1.7, fill: "0F4C5C",
            lineColor: C.mint });
  s.addText([
    { text: "GitHub リポジトリ　", options: { bold: true, color: C.mint } },
    { text: LINKS.repo, options: { color: C.white } },
    { text: "\n紹介動画　　　　　　", options: { bold: true, color: C.mint } },
    { text: LINKS.video, options: { color: C.white } },
    { text: "\nデモ環境　　　　　　", options: { bold: true, color: C.mint } },
    { text: LINKS.demo, options: { color: C.white } },
  ], { x: M + 0.35, y: 4.6, w: W - M * 2 - 0.7, h: 1.4,
       fontFace: F.body, fontSize: 13, margin: 0, lineSpacingMultiple: 1.25 });

  s.addText("ご清聴ありがとうございました。　学籍番号 20122049　曽我 幸太郎", {
    x: M, y: 6.45, w: W - M * 2, h: 0.4,
    fontFace: F.body, fontSize: 12, color: "8FAAB3", margin: 0 });
  s.addNotes("25秒。3段階の成長を一言ずつ。URLを示して終わる。");
}

// ---------------------------------------------------------------- 出力
pres.writeFile({ fileName: OUT }).then(() => {
  console.log(`出力: ${OUT}（${pres.slides ? pres.slides.length : 12}枚）`);
  if (missing.length) {
    console.log(`\n未撮影のスクリーンショット ${missing.length} 件：`);
    missing.forEach((m) => console.log("  " + m));
    console.log("\n撮影後 docs/images/ に置いて再実行すると差し替わります。");
    console.log("撮影手順は docs/デプロイ手順.md の第7節を参照。");
  } else {
    console.log("すべてのスクリーンショットが埋め込まれました。");
  }
});
