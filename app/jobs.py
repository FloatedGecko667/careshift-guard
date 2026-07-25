"""職種の定義。表示名と判定対象を一箇所に集める。

データベースには英字コードで持ち、画面と帳票には日本語で出す。
この対応表が複数箇所に散ると、どこかで表記が食い違う。
"""
from __future__ import annotations

# データベースのコード → 表示名
JOB_LABEL: dict[str, str] = {
    "manager": "管理者",
    "counselor": "生活相談員",
    "nurse": "看護職員",
    "care_worker": "介護職員",
    "trainer": "機能訓練指導員",
}

JOB_CODE: dict[str, str] = {v: k for k, v in JOB_LABEL.items()}

# 帳票・画面での表示順
JOB_ORDER: list[str] = ["管理者", "生活相談員", "看護職員", "介護職員", "機能訓練指導員"]

# 常勤換算による人員配置基準の判定対象。
# 管理者は介護保険法上、常勤換算の対象外であるため含めない。
FTE_TARGET_JOBS: list[str] = ["生活相談員", "看護職員", "介護職員", "機能訓練指導員"]


def label(code: str) -> str:
    """未知のコードでも落ちないようにする。"""
    return JOB_LABEL.get(code, code)
