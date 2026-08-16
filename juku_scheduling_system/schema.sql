-- =========================================================
-- 塾スケジューリングシステム DDL (SQLite)
-- 論理ER図 確定版に基づく
-- =========================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------
-- 1. マスタ系
-- ---------------------------------------------------------

CREATE TABLE STUDENTS (
    student_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    external_student_id TEXT UNIQUE,              -- 将来の外部ID連携用。今はNULL
    last_name             TEXT NOT NULL,            -- 姓
    first_name            TEXT NOT NULL,            -- 名
    last_name_kana         TEXT NOT NULL,            -- 姓(ふりがな)
    first_name_kana        TEXT NOT NULL,            -- 名(ふりがな)
    enrollment_year       INTEGER NOT NULL,         -- 入塾年度
    base_grade            INTEGER NOT NULL,         -- 入塾時点の学年
    enrollment_status      TEXT NOT NULL
        CHECK (enrollment_status IN ('在籍', '休会', '卒業', '退会'))
);

CREATE TABLE INSTRUCTORS (
    instructor_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    last_name       TEXT NOT NULL,   -- 姓
    first_name      TEXT NOT NULL,   -- 名
    last_name_kana   TEXT NOT NULL,   -- 姓(ふりがな)
    first_name_kana  TEXT NOT NULL    -- 名(ふりがな)
);

CREATE TABLE SUBJECTS (
    subject_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    course_category  TEXT NOT NULL
        CHECK (course_category IN ('個別指導', '戦略指導')),
    grade_band       TEXT NOT NULL
        CHECK (grade_band IN ('小学生低学年', '小学生高学年', '中学生', '高校生')),
    track            TEXT
        CHECK (track IS NULL OR track IN ('受験', '非受験')),
    subject_group    TEXT NOT NULL,   -- 上位グループ 例: 数学, 理科
    subject_name     TEXT NOT NULL    -- 具体科目名 例: 数1A, 化学基礎
);

-- ---------------------------------------------------------
-- 2. 時間軸系
-- ---------------------------------------------------------

CREATE TABLE PERIODS (
    period_number INTEGER PRIMARY KEY,   -- 1〜5限
    start_time    TEXT NOT NULL,          -- 'HH:MM'
    end_time      TEXT NOT NULL
);

CREATE TABLE TIME_SLOTS (
    slot_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date  TEXT NOT NULL,          -- 'YYYY-MM-DD'
    period_number INTEGER NOT NULL REFERENCES PERIODS(period_number),
    UNIQUE (session_date, period_number)
);

CREATE TABLE TERMS (
    term_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    term_name  TEXT NOT NULL,             -- 例: 2026年度前期
    start_date TEXT NOT NULL,
    end_date   TEXT NOT NULL
);

-- ---------------------------------------------------------
-- 3. 対応可能時間
-- ---------------------------------------------------------

CREATE TABLE STUDENT_WEEKLY_AVAILABILITY (
    availability_id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES STUDENTS(student_id),
    term_id         INTEGER NOT NULL REFERENCES TERMS(term_id),
    day_of_week     TEXT NOT NULL
        CHECK (day_of_week IN ('月', '火', '水', '木', '金', '土', '日')),
    period_number   INTEGER NOT NULL REFERENCES PERIODS(period_number),
    is_available    INTEGER NOT NULL CHECK (is_available IN (0, 1)),
    UNIQUE (student_id, term_id, day_of_week, period_number)
);

CREATE TABLE INSTRUCTOR_WEEKLY_AVAILABILITY (
    availability_id INTEGER PRIMARY KEY AUTOINCREMENT,
    instructor_id   INTEGER NOT NULL REFERENCES INSTRUCTORS(instructor_id),
    term_id         INTEGER NOT NULL REFERENCES TERMS(term_id),
    day_of_week     TEXT NOT NULL
        CHECK (day_of_week IN ('月', '火', '水', '木', '金', '土', '日')),
    period_number   INTEGER NOT NULL REFERENCES PERIODS(period_number),
    is_available    INTEGER NOT NULL CHECK (is_available IN (0, 1)),
    UNIQUE (instructor_id, term_id, day_of_week, period_number)
);

-- ---------------------------------------------------------
-- 講師の担当科目
-- ---------------------------------------------------------

CREATE TABLE INSTRUCTOR_SUBJECTS (
    instructor_subject_id INTEGER PRIMARY KEY AUTOINCREMENT,
    instructor_id          INTEGER NOT NULL REFERENCES INSTRUCTORS(instructor_id),
    subject_id             INTEGER NOT NULL REFERENCES SUBJECTS(subject_id),
    proficiency_level       INTEGER NOT NULL CHECK (proficiency_level BETWEEN 1 AND 5),
    UNIQUE (instructor_id, subject_id)
);

-- ---------------------------------------------------------
-- 4. スケジューリング本体（講習会）
-- ---------------------------------------------------------

CREATE TABLE SESSIONS (
    session_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id       INTEGER NOT NULL REFERENCES TIME_SLOTS(slot_id),
    instructor_id INTEGER NOT NULL REFERENCES INSTRUCTORS(instructor_id),
    UNIQUE (slot_id, instructor_id)   -- 同じ講師が同じ枠に複数セッションを持つことはない
);

CREATE TABLE ASSIGNMENTS (
    assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL REFERENCES SESSIONS(session_id),
    student_id    INTEGER NOT NULL REFERENCES STUDENTS(student_id),
    subject_id    INTEGER NOT NULL REFERENCES SUBJECTS(subject_id),
    UNIQUE (session_id, student_id)
    -- 「1セッション最大2人まで」はアプリ側(CP-SAT制約)で担保
);

-- ---------------------------------------------------------
-- 5. 通常授業契約・進級ルール
-- ---------------------------------------------------------

CREATE TABLE REGULAR_COURSE_ENROLLMENTS (
    enrollment_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id            INTEGER NOT NULL REFERENCES STUDENTS(student_id),
    subject_id            INTEGER NOT NULL REFERENCES SUBJECTS(subject_id),
    instructor_id          INTEGER NOT NULL REFERENCES INSTRUCTORS(instructor_id),
    day_of_week           TEXT NOT NULL
        CHECK (day_of_week IN ('月', '火', '水', '木', '金', '土', '日')),
    period_number          INTEGER NOT NULL REFERENCES PERIODS(period_number),
    effective_start_date    TEXT NOT NULL,   -- この契約が有効になった日
    effective_end_date      TEXT             -- 無効になった日。NULLなら現在も有効
);

CREATE TABLE STUDENT_HS_EXAM_INFO (
    student_id  INTEGER PRIMARY KEY REFERENCES STUDENTS(student_id),
    exam_method TEXT NOT NULL
        CHECK (exam_method IN ('私立専願', '持ち上がり', '公立専願', '併願'))
);

-- ---------------------------------------------------------
-- インデックス（スケジューリング時によく使う検索軸）
-- ---------------------------------------------------------

CREATE INDEX idx_time_slots_date ON TIME_SLOTS(session_date);
CREATE INDEX idx_sessions_slot ON SESSIONS(slot_id);
CREATE INDEX idx_assignments_student ON ASSIGNMENTS(student_id);
CREATE INDEX idx_reg_enroll_student_active ON REGULAR_COURSE_ENROLLMENTS(student_id, effective_end_date);
CREATE INDEX idx_reg_enroll_instructor_active ON REGULAR_COURSE_ENROLLMENTS(instructor_id, effective_end_date);
