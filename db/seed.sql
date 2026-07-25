-- =====================================================================
-- CareShift Guard  初期データ
--   1) staffing_rules … 法令由来の全テナント共通マスタ
--   2) デモ用の事業所・雇用区分・勤務区分
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 人員配置基準（通所介護 / 令和6年度介護報酬改定 以降）
--   介護職員 : 利用者15人までは常勤換算1以上、
--              15人を超える場合は超過人数5人ごとに1を加えた数以上
--   その他   : 常勤換算1以上かつ実人数1以上
-- ---------------------------------------------------------------------
INSERT INTO staffing_rules
    (service_type, job_type, formula_type, base_fte,
     threshold_users, step_users, step_fte, min_headcount, valid_from, note)
VALUES
    ('day_service', 'care_worker', 'per_users_step', 1.00,
     15, 5, 1.00, 1, DATE '2024-04-01',
     '利用者15人まで1以上。15人超は超過5人ごとに1を加算'),
    ('day_service', 'nurse',       'constant', 1.00,
     NULL, NULL, NULL, 1, DATE '2024-04-01', 'サービス提供日ごとに専従1以上'),
    ('day_service', 'counselor',   'constant', 1.00,
     NULL, NULL, NULL, 1, DATE '2024-04-01', 'サービス提供時間帯に応じ専従1以上'),
    ('day_service', 'trainer',     'constant', 1.00,
     NULL, NULL, NULL, 1, DATE '2024-04-01', '専従1以上');


-- ---------------------------------------------------------------------
-- デモ事業所
-- ---------------------------------------------------------------------
INSERT INTO offices
    (name, service_type, designation_number, capacity,
     fulltime_day_minutes, fulltime_week_minutes,
     max_weekly_minutes, max_consecutive_days, min_rest_days,
     min_interval_minutes, closed_weekdays)
VALUES
    ('デイサービスさくら', 'day_service', '1570000001', 35,
     480, 2400, 2400, 5, 8, 540, '{6}');


-- ---------------------------------------------------------------------
-- 雇用区分
-- ---------------------------------------------------------------------
INSERT INTO employment_types (office_id, name, is_fulltime, weekly_minutes)
SELECT o.office_id, v.name, v.ft, v.wm
FROM offices o,
     (VALUES ('常勤',   true,  2400),
             ('時短',   true,  1800),
             ('非常勤', false, 1440),
             ('登録',   false,   960)) AS v(name, ft, wm)
WHERE o.name = 'デイサービスさくら';


-- ---------------------------------------------------------------------
-- 勤務区分  ※ 公休は必ず1件（部分ユニークインデックスで担保）
-- ---------------------------------------------------------------------
INSERT INTO shift_patterns
    (office_id, code, name, start_minute, end_minute, break_minutes,
     is_rest, display_order)
SELECT o.office_id, v.code, v.name, v.s, v.e, v.b, v.rest, v.ord
FROM offices o,
     (VALUES ('休', '公休',   0,    0,   0, true,  0),
             ('早', '早番', 420,  960,  60, false, 1),
             ('日', '日勤', 510, 1050,  60, false, 2),
             ('遅', '遅番', 600, 1140,  60, false, 3),
             ('半', '半日', 510,  750,   0, false, 4))
         AS v(code, name, s, e, b, rest, ord)
WHERE o.name = 'デイサービスさくら';


-- ---------------------------------------------------------------------
-- 管理者アカウント
--   password_hash はデモ用のプレースホルダ。
--   実際には argon2-cffi の PasswordHasher().hash() で生成する。
-- ---------------------------------------------------------------------
INSERT INTO users (office_id, email, password_hash, role)
SELECT o.office_id, 'admin@example.jp', '$argon2id$PLACEHOLDER', 'admin'
FROM offices o
WHERE o.name = 'デイサービスさくら';

COMMIT;


-- =====================================================================
-- 検算用クエリ
-- =====================================================================

-- 勤務区分の実働時間が正しく導出されているか
--   早番 420→960 休憩60 なら 480分（8時間）
SELECT code, name, start_minute, end_minute, break_minutes, work_minutes
FROM shift_patterns
ORDER BY display_order;

-- 利用者数から必要常勤換算数を算定する
--
--   【重要】端数は切り上げない。
--   「15を超える部分の数を5で除して得た数に1を加えた数以上」であり、
--   除して得た数の小数部はそのまま常勤換算値として扱う。
--   例) 利用者22名 → 1 + (22-15)/5 = 2.4（2.4人分の常勤換算が必要）
--       利用者25名 → 1 + (25-15)/5 = 3.0
--   ここを切り上げ実装にすると過剰配置を要求することになり、
--   逆に切り捨てると基準未達を見逃す。どちらも製品として致命的である。
SELECT
    r.job_type,
    CASE r.formula_type
        WHEN 'constant' THEN r.base_fte
        WHEN 'per_users_step' THEN
            round(r.base_fte
                  + GREATEST(0, u.users - r.threshold_users)::numeric
                    / r.step_users * r.step_fte, 2)
    END AS required_fte,
    r.min_headcount
FROM staffing_rules r
CROSS JOIN (VALUES (22)) AS u(users)
WHERE r.service_type = 'day_service'
  AND r.valid_from <= CURRENT_DATE
  AND (r.valid_to IS NULL OR r.valid_to > CURRENT_DATE)
ORDER BY r.job_type;
