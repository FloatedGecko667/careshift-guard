-- =====================================================================
-- CareShift Guard  データベーススキーマ
--   対象: PostgreSQL 18
--   方針: 初版（第1リリース）の10テーブル。すべての業務テーブルに
--         office_id を持たせ、アプリケーション層で必ず絞り込む。
--   注意: PostgreSQL 18 固有の構文は使用していないため、
--         PostgreSQL 14 以降であればそのまま適用できる。
-- =====================================================================

BEGIN;

DROP VIEW  IF EXISTS v_daily_fte           CASCADE;
DROP TABLE IF EXISTS violations            CASCADE;
DROP TABLE IF EXISTS schedule_entries      CASCADE;
DROP TABLE IF EXISTS schedules             CASCADE;
DROP TABLE IF EXISTS shift_requests        CASCADE;
DROP TABLE IF EXISTS staffing_rules        CASCADE;
DROP TABLE IF EXISTS shift_patterns        CASCADE;
DROP TABLE IF EXISTS staff                 CASCADE;
DROP TABLE IF EXISTS employment_types      CASCADE;
DROP TABLE IF EXISTS users                 CASCADE;
DROP TABLE IF EXISTS offices               CASCADE;


-- ---------------------------------------------------------------------
-- 1. offices  事業所（テナントの単位・課金の単位）
-- ---------------------------------------------------------------------
CREATE TABLE offices (
    office_id             bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                  text        NOT NULL,
    service_type          text        NOT NULL DEFAULT 'day_service',
    designation_number    text,
    capacity              integer     NOT NULL,
    -- 常勤換算の分母となる時間（分）
    fulltime_day_minutes  integer     NOT NULL DEFAULT 480,
    fulltime_week_minutes integer     NOT NULL DEFAULT 2400,
    -- 勤務形態一覧表における常勤換算の分母（暦月）。既定は160時間
    fulltime_month_minutes integer    NOT NULL DEFAULT 9600,
    -- 労務制約の既定値
    max_weekly_minutes    integer     NOT NULL DEFAULT 2400,
    max_consecutive_days  integer     NOT NULL DEFAULT 5,
    min_rest_days         integer     NOT NULL DEFAULT 8,
    min_interval_minutes  integer     NOT NULL DEFAULT 540,
    -- 休業曜日（0=月曜 … 6=日曜）
    closed_weekdays       smallint[]  NOT NULL DEFAULT '{6}',
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_offices_capacity   CHECK (capacity > 0),
    CONSTRAINT ck_offices_day_min    CHECK (fulltime_day_minutes BETWEEN 1 AND 1440),
    -- 常勤換算の分母は週32時間（1920分）を下回ってはならない
    CONSTRAINT ck_offices_week_min   CHECK (fulltime_week_minutes >= 1920),
    CONSTRAINT ck_offices_month_min  CHECK (fulltime_month_minutes > 0),
    CONSTRAINT ck_offices_consec     CHECK (max_consecutive_days BETWEEN 1 AND 31),
    CONSTRAINT ck_offices_rest       CHECK (min_rest_days BETWEEN 0 AND 31),
    CONSTRAINT ck_offices_interval   CHECK (min_interval_minutes BETWEEN 0 AND 1440),
    CONSTRAINT ck_offices_service    CHECK (service_type IN
        ('day_service', 'dementia_day_service', 'community_day_service',
         'short_stay', 'group_home'))
);

COMMENT ON TABLE  offices IS '事業所。1事業所＝1テナント＝課金単位';
COMMENT ON COLUMN offices.fulltime_week_minutes IS
    '常勤換算の分母。介護保険法上、週32時間を下回る場合は32時間とする';


-- ---------------------------------------------------------------------
-- 2. users  ログインユーザー
-- ---------------------------------------------------------------------
CREATE TABLE users (
    user_id        bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    office_id      bigint      NOT NULL REFERENCES offices (office_id) ON DELETE CASCADE,
    email          text        NOT NULL,
    password_hash  text        NOT NULL,
    role           text        NOT NULL,
    staff_id       bigint,
    is_active      boolean     NOT NULL DEFAULT true,
    last_login_at  timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_users_email CHECK (email = lower(email)),
    CONSTRAINT ck_users_role  CHECK (role IN ('admin', 'staff'))
);

CREATE UNIQUE INDEX ux_users_email ON users (email);
CREATE INDEX ix_users_office ON users (office_id);

COMMENT ON COLUMN users.password_hash IS 'Argon2id によるハッシュ値。平文は保存しない';


-- ---------------------------------------------------------------------
-- 3. employment_types  雇用区分
-- ---------------------------------------------------------------------
CREATE TABLE employment_types (
    employment_type_id bigint  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    office_id          bigint  NOT NULL REFERENCES offices (office_id) ON DELETE CASCADE,
    name               text    NOT NULL,
    is_fulltime        boolean NOT NULL,
    weekly_minutes     integer NOT NULL,
    CONSTRAINT uq_employment_types UNIQUE (office_id, name),
    CONSTRAINT ck_employment_minutes CHECK (weekly_minutes BETWEEN 0 AND 4200)
);

COMMENT ON COLUMN employment_types.weekly_minutes IS '週の所定労働時間（分）';


-- ---------------------------------------------------------------------
-- 4. staff  職員
-- ---------------------------------------------------------------------
CREATE TABLE staff (
    staff_id           bigint  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    office_id          bigint  NOT NULL REFERENCES offices (office_id) ON DELETE CASCADE,
    name               text    NOT NULL,
    job_type           text    NOT NULL,
    employment_type_id bigint  NOT NULL REFERENCES employment_types (employment_type_id),
    qualifications     text[]  NOT NULL DEFAULT '{}',
    -- 兼務。secondary_ratio は従たる職種への従事割合（0.00〜1.00）。
    -- 主たる職種への配分は 1 - secondary_ratio となる。
    secondary_job_type text,
    secondary_ratio    numeric(3,2) NOT NULL DEFAULT 0,
    hired_on           date    NOT NULL,
    retired_on         date,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_staff_period CHECK (retired_on IS NULL OR retired_on >= hired_on),
    CONSTRAINT ck_staff_job    CHECK (job_type IN
        ('care_worker', 'nurse', 'counselor', 'trainer', 'manager')),
    CONSTRAINT ck_staff_second CHECK (secondary_job_type IS NULL OR secondary_job_type IN
        ('care_worker', 'nurse', 'counselor', 'trainer', 'manager')),
    CONSTRAINT ck_staff_ratio  CHECK (secondary_ratio >= 0 AND secondary_ratio <= 1),
    -- 兼務先が未設定なら按分率は0でなければならない
    CONSTRAINT ck_staff_kenmu  CHECK (
        (secondary_job_type IS NOT NULL) OR (secondary_ratio = 0)),
    CONSTRAINT ck_staff_diff   CHECK (
        secondary_job_type IS NULL OR secondary_job_type <> job_type)
);

CREATE INDEX ix_staff_office_job ON staff (office_id, job_type);

COMMENT ON COLUMN staff.job_type IS
    'care_worker=介護職員 / nurse=看護職員 / counselor=生活相談員 / trainer=機能訓練指導員 / manager=管理者';

ALTER TABLE users
    ADD CONSTRAINT fk_users_staff FOREIGN KEY (staff_id) REFERENCES staff (staff_id);


-- ---------------------------------------------------------------------
-- 5. shift_patterns  勤務区分
-- ---------------------------------------------------------------------
CREATE TABLE shift_patterns (
    shift_pattern_id bigint  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    office_id        bigint  NOT NULL REFERENCES offices (office_id) ON DELETE CASCADE,
    code             text    NOT NULL,
    name             text    NOT NULL,
    start_minute     integer NOT NULL,
    end_minute       integer NOT NULL,
    break_minutes    integer NOT NULL DEFAULT 0,
    is_rest          boolean NOT NULL DEFAULT false,
    is_night         boolean NOT NULL DEFAULT false,
    display_order    integer NOT NULL DEFAULT 0,
    -- 実働時間は導出値。手入力との不整合を構造的に防ぐ
    work_minutes     integer GENERATED ALWAYS AS (
        CASE WHEN end_minute <= start_minute THEN 0
             ELSE end_minute - start_minute - break_minutes END
    ) STORED,
    CONSTRAINT uq_shift_patterns UNIQUE (office_id, code),
    CONSTRAINT ck_pattern_start CHECK (start_minute BETWEEN 0 AND 1440),
    CONSTRAINT ck_pattern_end   CHECK (end_minute   BETWEEN 0 AND 1440),
    CONSTRAINT ck_pattern_break CHECK (break_minutes >= 0),
    CONSTRAINT ck_pattern_rest  CHECK (NOT is_rest OR (start_minute = 0 AND end_minute = 0))
);

-- 「公休」は事業所ごとにちょうど1つ
CREATE UNIQUE INDEX ux_shift_patterns_rest ON shift_patterns (office_id) WHERE is_rest;

COMMENT ON COLUMN shift_patterns.start_minute IS '0時からの経過分。08:30 なら 510';


-- ---------------------------------------------------------------------
-- 6. staffing_rules  人員配置基準ルール
--    法令由来のため事業所には属さない。全テナント共通のマスタとする。
--    介護報酬改定時は valid_to を閉じて新しい版を追加する。
-- ---------------------------------------------------------------------
CREATE TABLE staffing_rules (
    staffing_rule_id bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    service_type     text         NOT NULL,
    job_type         text         NOT NULL,
    formula_type     text         NOT NULL,
    base_fte         numeric(5,2) NOT NULL DEFAULT 0,
    threshold_users  integer,
    step_users       integer,
    step_fte         numeric(5,2),
    min_headcount    integer      NOT NULL DEFAULT 0,
    valid_from       date         NOT NULL,
    valid_to         date,
    note             text,
    CONSTRAINT ck_rules_formula CHECK (formula_type IN ('constant', 'per_users_step')),
    CONSTRAINT ck_rules_period  CHECK (valid_to IS NULL OR valid_to > valid_from),
    CONSTRAINT ck_rules_step    CHECK (
        formula_type <> 'per_users_step'
        OR (threshold_users IS NOT NULL AND step_users > 0 AND step_fte IS NOT NULL)
    )
);

CREATE UNIQUE INDEX ux_staffing_rules
    ON staffing_rules (service_type, job_type, valid_from);

COMMENT ON TABLE staffing_rules IS
    '人員配置基準。判定式をプログラムではなくデータとして保持し、法改正には版の追加で対応する';
COMMENT ON COLUMN staffing_rules.formula_type IS
    'constant=固定値 / per_users_step=利用者数に応じた段階加算';


-- ---------------------------------------------------------------------
-- 7. shift_requests  希望シフト
-- ---------------------------------------------------------------------
CREATE TABLE shift_requests (
    shift_request_id bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    office_id        bigint      NOT NULL REFERENCES offices (office_id) ON DELETE CASCADE,
    staff_id         bigint      NOT NULL REFERENCES staff (staff_id) ON DELETE CASCADE,
    target_date      date        NOT NULL,
    request_type     text        NOT NULL,
    shift_pattern_id bigint      REFERENCES shift_patterns (shift_pattern_id),
    note             text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_shift_requests UNIQUE (staff_id, target_date),
    CONSTRAINT ck_request_type CHECK (request_type IN ('off', 'pattern', 'unavailable')),
    -- 勤務区分の希望なら区分の指定が必須、それ以外なら指定してはならない
    CONSTRAINT ck_request_pattern CHECK (
        (request_type = 'pattern') = (shift_pattern_id IS NOT NULL)
    )
);

CREATE INDEX ix_shift_requests_lookup ON shift_requests (office_id, target_date);

COMMENT ON COLUMN shift_requests.request_type IS
    'off=希望休（ソフト制約） / pattern=希望勤務区分（ソフト制約） / unavailable=勤務不可（ハード制約）';


-- ---------------------------------------------------------------------
-- 8. schedules  シフト表（月次）
-- ---------------------------------------------------------------------
CREATE TABLE schedules (
    schedule_id        bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    office_id          bigint       NOT NULL REFERENCES offices (office_id) ON DELETE CASCADE,
    target_month       date         NOT NULL,
    avg_expected_users numeric(5,1) NOT NULL,
    status             text         NOT NULL DEFAULT 'draft',
    solver_status      text,
    objective_value    bigint,
    solve_seconds      numeric(6,2),
    generated_at       timestamptz,
    published_at       timestamptz,
    created_at         timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT uq_schedules UNIQUE (office_id, target_month),
    CONSTRAINT ck_schedules_status CHECK (status IN ('draft', 'published')),
    CONSTRAINT ck_schedules_users  CHECK (avg_expected_users >= 0),
    -- target_month は必ず月初日
    CONSTRAINT ck_schedules_month  CHECK (extract(day FROM target_month) = 1)
);

COMMENT ON COLUMN schedules.solver_status IS
    'OPTIMAL=最適性を証明済み / FEASIBLE=実行可能解を発見（上限時間に到達）';


-- ---------------------------------------------------------------------
-- 9. schedule_entries  シフト明細
-- ---------------------------------------------------------------------
CREATE TABLE schedule_entries (
    schedule_id      bigint  NOT NULL REFERENCES schedules (schedule_id) ON DELETE CASCADE,
    staff_id         bigint  NOT NULL REFERENCES staff (staff_id) ON DELETE CASCADE,
    target_date      date    NOT NULL,
    shift_pattern_id bigint  NOT NULL REFERENCES shift_patterns (shift_pattern_id),
    is_manual        boolean NOT NULL DEFAULT false,
    PRIMARY KEY (schedule_id, staff_id, target_date)
);

CREATE INDEX ix_entries_date ON schedule_entries (schedule_id, target_date);

COMMENT ON COLUMN schedule_entries.is_manual IS
    '管理者が手動で変更した明細。再生成時に維持するかの判断に使う';


-- ---------------------------------------------------------------------
-- 10. violations  人員配置基準の違反検出結果
-- ---------------------------------------------------------------------
CREATE TABLE violations (
    violation_id bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    schedule_id  bigint       NOT NULL REFERENCES schedules (schedule_id) ON DELETE CASCADE,
    target_date  date         NOT NULL,
    job_type     text         NOT NULL,
    kind         text         NOT NULL,
    required     numeric(5,2) NOT NULL,
    actual       numeric(5,2) NOT NULL,
    severity     text         NOT NULL DEFAULT 'error',
    detected_at  timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT ck_violations_kind     CHECK (kind IN ('fte', 'headcount')),
    CONSTRAINT ck_violations_severity CHECK (severity IN ('error', 'warning')),
    CONSTRAINT ck_violations_shortage CHECK (actual < required)
);

CREATE INDEX ix_violations_lookup ON violations (schedule_id, target_date);


-- ---------------------------------------------------------------------
-- ビュー: 日別・職種別の常勤換算値
--   勤務形態一覧表の出力と、画面上の基準適合表示の双方がこれを参照する。
--   算定式を1か所に集約することで、画面と帳票で値がずれることを防ぐ。
-- ---------------------------------------------------------------------
CREATE VIEW v_daily_fte AS
SELECT
    e.schedule_id,
    e.target_date,
    s.job_type,
    sum(p.work_minutes)                             AS total_work_minutes,
    count(*) FILTER (WHERE NOT p.is_rest)           AS headcount,
    round(sum(p.work_minutes)::numeric
          / nullif(o.fulltime_day_minutes, 0), 2)   AS fte
FROM schedule_entries e
JOIN staff          s ON s.staff_id         = e.staff_id
JOIN shift_patterns p ON p.shift_pattern_id = e.shift_pattern_id
JOIN schedules     sc ON sc.schedule_id     = e.schedule_id
JOIN offices        o ON o.office_id        = sc.office_id
GROUP BY e.schedule_id, e.target_date, s.job_type, o.fulltime_day_minutes;

COMMENT ON VIEW v_daily_fte IS
    '日別・職種別の常勤換算値。常勤換算＝勤務延べ時間÷常勤の1日所定時間';

COMMIT;
