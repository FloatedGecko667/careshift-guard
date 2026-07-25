"""テストおよびデモ用の問題インスタンス生成と、制約の独立監査。

ここで生成するデータは実データではなく、
人員配置基準の判定ロジックを検証するための合成データである。
"""
from __future__ import annotations

import datetime as dt
import random

from app.solver import REST, Problem, ShiftPattern, Staff

JOBS = ["介護職員", "看護職員", "生活相談員", "機能訓練指導員"]

QUAL = {
    "介護職員": ["介護福祉士", "実務者研修修了", "初任者研修修了"],
    "看護職員": ["看護師", "准看護師"],
    "生活相談員": ["社会福祉士", "介護福祉士"],
    "機能訓練指導員": ["理学療法士", "作業療法士", "柔道整復師"],
    "管理者": ["介護福祉士"],
}


def standard_patterns() -> list[ShiftPattern]:
    """公休を先頭（インデックス0）に置く。solver の前提。"""
    return [
        ShiftPattern("公休", 0, 0),
        ShiftPattern("早番", 7 * 60, 16 * 60, 60),            # 実働 8.0h
        ShiftPattern("日勤", 8 * 60 + 30, 17 * 60 + 30, 60),  # 実働 8.0h
        ShiftPattern("遅番", 10 * 60, 19 * 60, 60),           # 実働 8.0h
        ShiftPattern("半日", 8 * 60 + 30, 12 * 60 + 30, 0),   # 実働 4.0h
    ]


def care_worker_required_fte(avg_users: float) -> float:
    """介護職員の必要常勤換算数。

    利用者15人までは1以上、15人を超える場合は
    「15を超える部分の数を5で除して得た数に1を加えた数」以上。
    除して得た数の端数は切り上げない（例: 利用者22名 → 2.4）。
    """
    if avg_users <= 15:
        return 1.0
    return round(1.0 + (avg_users - 15) / 5.0, 2)


def _requirements(nd: int, first_weekday: int, avg_users: float):
    required_fte = {"介護職員": [care_worker_required_fte(avg_users)] * nd}
    for j in ("看護職員", "生活相談員", "機能訓練指導員"):
        required_fte[j] = [1.0] * nd
    required_head = {j: [1] * nd for j in JOBS}
    closed = [(first_weekday + d) % 7 == 6 for d in range(nd)]   # 日曜は休業
    for d in range(nd):
        if closed[d]:
            for j in JOBS:
                required_fte[j][d] = 0.0
                required_head[j][d] = 0
    return required_fte, required_head, closed


def _wishes(rnd: random.Random, n_staff: int, nd: int, n_off=4, n_pat=2):
    pref_off, pref_pat = {}, {}
    for i in range(n_staff):
        for d in rnd.sample(range(nd), n_off):
            pref_off[i, d] = True
        for d in rnd.sample(range(nd), n_pat):
            if (i, d) not in pref_off:
                pref_pat[i, d] = rnd.choice([1, 2, 3])
    return pref_off, pref_pat


def build(num_staff: int, num_days: int, avg_users: float, seed: int = 42,
          first_weekday: int = 0) -> Problem:
    """規模を指定して合成データを作る。求解性能の検証に使う。

    職種構成は介護職員を厚めに、他職種は各2名とする。
    """
    rnd = random.Random(seed)
    n_care = max(1, num_staff - 6)
    comp = (["介護職員"] * n_care + ["看護職員"] * 2
            + ["生活相談員"] * 2 + ["機能訓練指導員"] * 2)[:num_staff]

    staff: list[Staff] = []
    for k, job in enumerate(comp):
        ft = rnd.random() < 0.6
        staff.append(Staff(
            name=f"職員{k + 1:02d}", job=job, is_fulltime=ft,
            weekly_minutes=2400 if ft else 1440,   # 常勤40h / 非常勤24h
            available=[rnd.random() > 0.03 for _ in range(num_days)],
            qualifications=[rnd.choice(QUAL.get(job, ["—"]))]))

    required_fte, required_head, closed = _requirements(
        num_days, first_weekday, avg_users)
    pref_off, pref_pat = _wishes(rnd, len(staff), num_days)

    return Problem(
        num_days=num_days, first_weekday=first_weekday,
        patterns=standard_patterns(), staff=staff,
        required_fte=required_fte, required_head=required_head,
        pref_off=pref_off, pref_pattern=pref_pat,
        min_rest_days=max(8, num_days // 4), closed=closed)


def build_demo(year=2026, month=8, avg_users=22.0, seed=11) -> Problem:
    """勤務形態一覧表の出力デモ用。兼務と資格保有を含む現実的な構成。

    管理者が生活相談員を50％兼務、機能訓練指導員が介護職員を30％兼務する。
    小規模な通所介護事業所では一般的な形態である。
    """
    rnd = random.Random(seed)
    first = dt.date(year, month, 1)
    nd = (dt.date(year + (month == 12), month % 12 + 1, 1) - first).days
    fw = first.weekday()

    staff: list[Staff] = []

    def add(name, job, ft, wm, *, sec=None, ratio=0.0, quals=None):
        staff.append(Staff(
            name=name, job=job, is_fulltime=ft, weekly_minutes=wm,
            available=[rnd.random() > 0.03 for _ in range(nd)],
            qualifications=quals if quals is not None
            else [rnd.choice(QUAL.get(job, ["—"]))],
            secondary_job=sec, secondary_ratio=ratio))

    add("佐藤 一郎", "管理者", True, 2400, sec="生活相談員", ratio=0.5,
        quals=["介護福祉士", "認知症介護実践者研修修了"])
    add("鈴木 花子", "生活相談員", True, 2400, quals=["社会福祉士"])
    add("高橋 美咲", "看護職員", True, 2400, quals=["看護師"])
    add("田中 良子", "看護職員", False, 1440, quals=["准看護師"])
    add("伊藤 健", "機能訓練指導員", True, 2400, sec="介護職員", ratio=0.3,
        quals=["理学療法士"])
    add("渡辺 直美", "機能訓練指導員", False, 1440, quals=["柔道整復師"])
    for k in range(12):
        ft = k < 7
        add(f"介護 {k + 1:02d}", "介護職員", ft, 2400 if ft else 1440)

    required_fte, required_head, closed = _requirements(nd, fw, avg_users)
    pref_off, pref_pat = _wishes(rnd, len(staff), nd)

    return Problem(
        num_days=nd, first_weekday=fw, patterns=standard_patterns(), staff=staff,
        required_fte=required_fte, required_head=required_head,
        pref_off=pref_off, pref_pattern=pref_pat, min_rest_days=8, closed=closed)


def audit(prob: Problem, sol) -> list[str]:
    """解がハード制約を守っているかを、ソルバーとは独立に再検証する。

    ソルバーの実装を信用せず、生成された割当そのものを検査する。
    ここで違反が出るなら制約の書き方が誤っている。
    """
    errs: list[str] = []
    h = [p.work_minutes for p in prob.patterns]
    closed = prob.closed if prob.closed else [False] * prob.num_days

    for i in range(len(prob.staff)):
        for d in range(prob.num_days):
            # C2 勤務不可日・休業日
            if closed[d] and prob.force_rest_on_closed and sol.assign[i, d] != REST:
                errs.append(f"C2違反: 職員{i} 日{d} 休業日に出勤")
            elif not prob.staff[i].available[d] and sol.assign[i, d] != REST:
                errs.append(f"C2違反: 職員{i} 日{d} 勤務不可日に出勤")

        # C3 週の労働時間上限
        for ws in range(0, prob.num_days, 7):
            days = range(ws, min(ws + 7, prob.num_days))
            tot = sum(h[sol.assign[i, d]] for d in days)
            cap = min(prob.max_weekly_minutes, prob.staff[i].weekly_minutes)
            if tot > cap:
                errs.append(f"C3違反: 職員{i} 週{ws // 7} {tot}分 > 上限{cap}分")

        # C4 連続勤務日数
        run = 0
        for d in range(prob.num_days):
            run = run + 1 if sol.assign[i, d] != REST else 0
            if run > prob.max_consecutive_days:
                errs.append(f"C4違反: 職員{i} 日{d} 連続{run}日")
                break

        # C5 月間の最低公休日数
        rest = sum(1 for d in range(prob.num_days) if sol.assign[i, d] == REST)
        if rest < prob.min_rest_days:
            errs.append(f"C5違反: 職員{i} 公休{rest}日 < 最低{prob.min_rest_days}日")

        # C6 勤務間インターバル
        for d in range(prob.num_days - 1):
            p, q = sol.assign[i, d], sol.assign[i, d + 1]
            if p == REST or q == REST:
                continue
            gap = (24 * 60 - prob.patterns[p].end) + prob.patterns[q].start
            if gap < prob.min_interval_minutes:
                errs.append(f"C6違反: 職員{i} 日{d}→{d + 1} インターバル{gap}分")

    return errs
