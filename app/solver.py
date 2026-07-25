"""
CareShift Guard — シフト最適化エンジン（リファレンス実装）

Google OR-Tools CP-SAT による、介護事業所向けシフト自動生成。
人員配置基準・労働基準法・就業規則・職員の勤務希望を制約条件として同時に扱う。

設計上の重要な性質:
  人員配置基準はソフト制約（スラック変数）として扱う。
  ハード制約群は「全員が公休」で必ず充足できるため、本モデルは構造的に
  INFEASIBLE にならない。必ず解が返り、基準を満たせない日は
  「どの職種が何名分不足しているか」として出力される。

License: MIT
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

# ---------------------------------------------------------------- 定数
REST = 0  # 勤務区分インデックス0は必ず「公休」

# 目的関数の重み（辞書式順序に近い階層をつくる）
W_VIOLATION_COUNT = 1_000_000  # 基準違反の発生件数
W_VIOLATION_SIZE = 100         # 基準違反の大きさ（分）
W_PREF_OFF = 500               # 希望休の未反映
W_PREF_PATTERN = 50            # 希望勤務区分の未反映
W_FAIRNESS = 1                 # 勤務時間の偏り（分）


# ---------------------------------------------------------------- 入力データ
@dataclass
class ShiftPattern:
    """勤務区分。start/end は 0時からの経過分。公休は work_minutes=0。"""
    name: str
    start: int
    end: int
    break_minutes: int = 0

    @property
    def work_minutes(self) -> int:
        if self.end <= self.start:
            return 0
        return self.end - self.start - self.break_minutes


@dataclass
class Staff:
    name: str
    job: str                    # 主たる職種
    is_fulltime: bool
    weekly_minutes: int         # 週の所定労働時間（分）
    available: list[bool] = field(default_factory=list)   # 日ごとの勤務可否
    qualifications: list[str] = field(default_factory=list)
    # 兼務。secondary_job に職種、secondary_ratio に従事割合（0.0〜1.0）を入れる。
    # 主たる職種への配分は 1.0 - secondary_ratio となる。
    # 例) 管理者50％・生活相談員50％ → job="管理者", secondary_job="生活相談員",
    #     secondary_ratio=0.5
    secondary_job: str | None = None
    secondary_ratio: float = 0.0

    def weight_for(self, job: str) -> float:
        """職種 job に対するこの職員の従事割合を返す。"""
        if self.secondary_job is not None and self.secondary_job == job:
            return self.secondary_ratio
        if self.job == job:
            return 1.0 - (self.secondary_ratio if self.secondary_job else 0.0)
        return 0.0

    @property
    def work_form_code(self) -> str:
        """勤務形態区分 A=常勤専従 / B=常勤兼務 / C=常勤以外で専従 / D=常勤以外で兼務"""
        kenmu = self.secondary_job is not None
        if self.is_fulltime:
            return "B" if kenmu else "A"
        return "D" if kenmu else "C"


@dataclass
class Problem:
    num_days: int
    first_weekday: int                      # 0=月曜
    patterns: list[ShiftPattern]
    staff: list[Staff]
    required_fte: dict[str, list[float]]    # 職種 -> 日ごとの必要常勤換算数
    required_head: dict[str, list[int]]     # 職種 -> 日ごとの必要実人数
    pref_off: dict[tuple[int, int], bool] = field(default_factory=dict)   # (i,d) -> 希望休
    pref_pattern: dict[tuple[int, int], int] = field(default_factory=dict)  # (i,d) -> 勤務区分
    # 休業日（サービスを提供しない日）。指定すると全職員を公休に固定する。
    # 変数が減るため求解も軽くなる。
    closed: list[bool] = field(default_factory=list)
    force_rest_on_closed: bool = True

    # 常勤職員が勤務すべき時間
    fulltime_day_minutes: int = 480         # 1日8時間
    fulltime_week_minutes: int = 2400       # 週40時間（法令上の下限は週32時間）
    fulltime_month_minutes: int = 9600      # 暦月160時間（勤務形態一覧表の分母）

    # 労務制約
    max_weekly_minutes: int = 2400          # 週40時間
    max_consecutive_days: int = 5           # 連続勤務日数の上限
    min_rest_days: int = 8                  # 月間の最低公休日数
    min_interval_minutes: int = 540         # 勤務間インターバル 9時間


# ---------------------------------------------------------------- 出力データ
@dataclass
class Violation:
    day: int
    job: str
    kind: str            # "fte" | "headcount"
    required: float
    actual: float

    @property
    def shortage(self) -> float:
        return round(self.required - self.actual, 2)


@dataclass
class Solution:
    status: str
    solve_seconds: float
    assign: dict[tuple[int, int], int]      # (i,d) -> 勤務区分
    violations: list[Violation]
    fte: dict[tuple[str, int], float]       # (職種,日) -> 常勤換算数
    pref_off_broken: int
    pref_pattern_broken: int
    staff_minutes: list[int]
    objective: int


# ---------------------------------------------------------------- ソルバー
def solve(prob: Problem, time_limit: float = 10.0, workers: int = 4,
          random_seed: int = 0, deterministic: bool = False) -> Solution:
    """シフト最適化を実行する。

    deterministic=True にすると壁時計ではなく決定的時間で打ち切り、
    同じ入力に対して常に同じ解を返す。回帰テストや監査再現に使う。
    実運用では応答時間を保証したいため既定の壁時計制限を使う。
    """
    m = cp_model.CpModel()

    STAFF = range(len(prob.staff))
    D = range(prob.num_days)
    P = range(len(prob.patterns))
    WORK_P = [p for p in P if p != REST]
    JOBS = sorted(prob.required_fte.keys())
    h = [pt.work_minutes for pt in prob.patterns]

    # ---- 決定変数 -------------------------------------------------
    # x[i,d,p] = 1  ⇔  職員 i が 日 d に 勤務区分 p に就く
    x = {(i, d, p): m.NewBoolVar(f"x_{i}_{d}_{p}") for i in STAFF for d in D for p in P}
    # work[i,d] = 1 ⇔ 職員 i が 日 d に出勤する（公休でない）
    work = {(i, d): m.NewBoolVar(f"w_{i}_{d}") for i in STAFF for d in D}

    # ---- C1: 各職員は各日ちょうど1つの勤務区分に就く ----------------
    for i in STAFF:
        for d in D:
            m.AddExactlyOne(x[i, d, p] for p in P)
            m.Add(work[i, d] == sum(x[i, d, p] for p in WORK_P))

    # ---- C2: 勤務不可日・休業日は公休に固定 --------------------------
    closed = prob.closed if prob.closed else [False] * prob.num_days
    for i in STAFF:
        for d in D:
            if prob.force_rest_on_closed and d < len(closed) and closed[d]:
                m.Add(x[i, d, REST] == 1)
                continue
            if (i < len(prob.staff) and d < len(prob.staff[i].available)
                    and not prob.staff[i].available[d]):
                m.Add(x[i, d, REST] == 1)

    # ---- C3: 週の労働時間上限 --------------------------------------
    for i in STAFF:
        for w_start in range(0, prob.num_days, 7):
            days = list(range(w_start, min(w_start + 7, prob.num_days)))
            cap = min(prob.max_weekly_minutes, prob.staff[i].weekly_minutes)
            m.Add(sum(h[p] * x[i, d, p] for d in days for p in P) <= cap)

    # ---- C4: 連続勤務日数の上限 ------------------------------------
    L = prob.max_consecutive_days
    for i in STAFF:
        for d in range(prob.num_days - L):
            m.Add(sum(work[i, dd] for dd in range(d, d + L + 1)) <= L)

    # ---- C5: 月間の最低公休日数 ------------------------------------
    for i in STAFF:
        m.Add(sum(x[i, d, REST] for d in D) >= prob.min_rest_days)

    # ---- C6: 勤務間インターバル ------------------------------------
    # 前日 p の終業から翌日 q の始業までが規定を下回る組合せを禁止する
    forbidden: list[tuple[int, int]] = []
    for p in WORK_P:
        for q in WORK_P:
            gap = (24 * 60 - prob.patterns[p].end) + prob.patterns[q].start
            if gap < prob.min_interval_minutes:
                forbidden.append((p, q))
    for i in STAFF:
        for d in range(prob.num_days - 1):
            for (p, q) in forbidden:
                m.Add(x[i, d, p] + x[i, d + 1, q] <= 1)

    # ---- S1: 人員配置基準（ソフト制約） ----------------------------
    # 常勤換算数 = 職種の勤務延べ時間 ÷ 常勤職員が1日に勤務すべき時間
    # 両辺に fulltime_day_minutes を掛けて整数のまま扱う
    short_fte: dict[tuple[str, int], cp_model.IntVar] = {}
    short_head: dict[tuple[str, int], cp_model.IntVar] = {}
    viol_flags: list[cp_model.IntVar] = []

    # 職種ごとの従事割合。兼務者は複数職種に按分される。
    # 管理者は人員配置基準の常勤換算対象外なので、JOBS に含めなければ自動的に除外される。
    SCALE = 100   # 割合を整数係数として扱うための倍率
    for j in JOBS:
        wt = {i: prob.staff[i].weight_for(j) for i in STAFF}
        members = [i for i in STAFF if wt[i] > 0]
        for d in D:
            need_min = math.ceil(prob.required_fte[j][d] * prob.fulltime_day_minutes * SCALE)
            s = m.NewIntVar(0, max(need_min, 1), f"short_fte_{j}_{d}")
            short_fte[j, d] = s
            m.Add(sum(round(h[p] * wt[i] * SCALE) * x[i, d, p]
                      for i in members for p in P) + s >= need_min)

            need_head = prob.required_head[j][d]
            sh = m.NewIntVar(0, max(need_head, 1), f"short_head_{j}_{d}")
            short_head[j, d] = sh
            m.Add(sum(work[i, d] for i in members) + sh >= need_head)

            # 「その日その職種で違反が発生したか」の指示変数
            v = m.NewBoolVar(f"viol_{j}_{d}")
            m.Add(s + sh > 0).OnlyEnforceIf(v)
            m.Add(s + sh == 0).OnlyEnforceIf(v.Not())
            viol_flags.append(v)

    # ---- S2/S3: 職員の勤務希望 -------------------------------------
    pref_off_terms = [work[i, d] for (i, d), on in prob.pref_off.items() if on]
    pref_pat_terms = [x[i, d, p].Not() for (i, d), p in prob.pref_pattern.items()]

    # ---- S4: 勤務時間の公平性 --------------------------------------
    total = []
    for i in STAFF:
        t = m.NewIntVar(0, prob.num_days * max(h), f"total_{i}")
        m.Add(t == sum(h[p] * x[i, d, p] for d in D for p in P))
        total.append(t)
    tmax = m.NewIntVar(0, prob.num_days * max(h), "tmax")
    tmin = m.NewIntVar(0, prob.num_days * max(h), "tmin")
    m.AddMaxEquality(tmax, total)
    m.AddMinEquality(tmin, total)
    spread = m.NewIntVar(0, prob.num_days * max(h), "spread")
    m.Add(spread == tmax - tmin)

    # ---- 目的関数 --------------------------------------------------
    m.Minimize(
        W_VIOLATION_COUNT * sum(viol_flags)
        + W_VIOLATION_SIZE * (sum(short_fte.values()) + sum(short_head.values()))
        + W_PREF_OFF * sum(pref_off_terms)
        + W_PREF_PATTERN * sum(pref_pat_terms)
        + W_FAIRNESS * spread
    )

    # ---- 求解 ------------------------------------------------------
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = random_seed
    if deterministic:
        # 壁時計に依存しないため、同じ入力なら常に同じ解になる
        solver.parameters.max_deterministic_time = time_limit
        solver.parameters.num_search_workers = 1
    else:
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.num_search_workers = workers
    t0 = time.perf_counter()
    status = solver.Solve(m)
    elapsed = time.perf_counter() - t0

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # ハード制約は全員公休で常に充足するため、通常ここには到達しない
        return Solution(solver.StatusName(status), elapsed, {}, [], {}, 0, 0, [], -1)

    # ---- 解の取り出し ----------------------------------------------
    assign = {}
    for i in STAFF:
        for d in D:
            for p in P:
                if solver.Value(x[i, d, p]):
                    assign[i, d] = p
                    break

    fte = {}
    violations = []
    for j in JOBS:
        wt = {i: prob.staff[i].weight_for(j) for i in STAFF}
        members = [i for i in STAFF if wt[i] > 0]
        for d in D:
            mins = sum(h[assign[i, d]] * wt[i] for i in members)
            f = mins / prob.fulltime_day_minutes
            fte[j, d] = round(f, 2)
            if solver.Value(short_fte[j, d]) > 0:
                violations.append(Violation(d, j, "fte", prob.required_fte[j][d], round(f, 2)))
            if solver.Value(short_head[j, d]) > 0:
                head = sum(1 for i in members if assign[i, d] != REST)
                violations.append(Violation(d, j, "headcount",
                                            float(prob.required_head[j][d]), float(head)))

    return Solution(
        status=solver.StatusName(status),
        solve_seconds=elapsed,
        assign=assign,
        violations=violations,
        fte=fte,
        pref_off_broken=sum(solver.Value(t) for t in pref_off_terms),
        pref_pattern_broken=sum(1 for (i, d), p in prob.pref_pattern.items()
                                if not solver.Value(x[i, d, p])),
        staff_minutes=[solver.Value(t) for t in total],
        objective=int(solver.ObjectiveValue()),
    )
