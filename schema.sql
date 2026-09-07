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
        CHECK (enrollment_status IN ('在籍', '休会', '卒業', '退会')),
    track                 TEXT  -- 高校生の文系/理系。任意(高1は文理選択前のためNULLのことが多い)
        CHECK (track IS NULL OR track IN ('文系', '理系')),
    gender                TEXT  -- 性別。任意、今は記録のみ(スケジューリングには未使用。将来「同性の講師と組む」等の制約に使う可能性あり)
        CHECK (gender IS NULL OR gender IN ('男', '女', 'その他')),
    junior_high_school     TEXT,  -- 所属中学(任意、参考情報)
    high_school            TEXT   -- 所属高校(任意、参考情報)
);

CREATE TABLE INSTRUCTORS (
    instructor_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    last_name       TEXT NOT NULL,   -- 姓
    first_name      TEXT NOT NULL,   -- 名
    last_name_kana   TEXT NOT NULL,   -- 姓(ふりがな)
    first_name_kana  TEXT NOT NULL,   -- 名(ふりがな)
    external_instructor_id TEXT UNIQUE,  -- 会社全体の講師番号(教室単位では飛び飛び)。将来の外部ID連携用。今はNULL可
    academic_year TEXT NOT NULL  -- 学年(講師は大学生が基本のため必須)。学部生B1〜B12、修士M1〜M4、博士D1〜D6
        CHECK (academic_year IS NULL OR academic_year IN (
            'B1','B2','B3','B4','B5','B6','B7','B8','B9','B10','B11','B12',
            'M1','M2','M3','M4',
            'D1','D2','D3','D4','D5','D6'
        )),
    status TEXT NOT NULL DEFAULT '在籍'
        CHECK (status IN ('在籍', '休職', '辞職')),
    academic_year_confirmed_fiscal_year INTEGER  -- 学年を最後に本人が確認(承認)した年度(4月始まり)。NULLなら未確認
);

CREATE TABLE SUBJECTS (
    subject_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    course_category  TEXT NOT NULL
        CHECK (course_category IN ('個別指導', '戦略指導')),
    grade_band       TEXT NOT NULL
        CHECK (grade_band IN ('小学生低学年', '小学生高学年', '中学生', '高校生')),
    track            TEXT
        CHECK (track IS NULL OR track IN ('受験', '非受験', '文系', '理系')),
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
    term_name  TEXT NOT NULL UNIQUE,      -- 例: 2026年度前期
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
    proficiency_level       INTEGER NOT NULL CHECK (proficiency_level BETWEEN 1 AND 2),
    UNIQUE (instructor_id, subject_id)
);

-- ---------------------------------------------------------
-- 4. スケジューリング本体（講習会）
-- ---------------------------------------------------------

CREATE TABLE SESSIONS (
    session_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    camp_id       INTEGER REFERENCES CAMPS(camp_id),  -- どの講習会で生成されたか。NULL可(将来通常期にも使う可能性を残す)
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

-- 教科フォロー専用の登録(通常授業とは別テーブルで管理する)。
-- 構造はREGULAR_COURSE_ENROLLMENTSと同じ(曜日・限固定、手動登録)だが、
-- 教科フォローという性質上、通常授業とは別で管理したいという要望のため分離した。
-- 登録時、通常授業・他の教科フォローと、生徒/講師どちらの面でも時間が被らないことを
-- アプリ側(page_follow_enrollments.py)でチェックする。
CREATE TABLE FOLLOW_COURSE_ENROLLMENTS (
    follow_enrollment_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id            INTEGER NOT NULL REFERENCES STUDENTS(student_id),
    subject_id            INTEGER NOT NULL REFERENCES SUBJECTS(subject_id),
    instructor_id          INTEGER NOT NULL REFERENCES INSTRUCTORS(instructor_id),
    day_of_week           TEXT NOT NULL
        CHECK (day_of_week IN ('月', '火', '水', '木', '金', '土', '日')),
    period_number          INTEGER NOT NULL REFERENCES PERIODS(period_number),
    effective_start_date    TEXT NOT NULL,
    effective_end_date      TEXT
);

CREATE TABLE STUDENT_HS_EXAM_INFO (
    student_id  INTEGER PRIMARY KEY REFERENCES STUDENTS(student_id),
    exam_method TEXT NOT NULL
        CHECK (exam_method IN ('私立専願', '持ち上がり', '公立専願', '併願'))
);

-- ---------------------------------------------------------
-- 6. 講習会
-- ---------------------------------------------------------

CREATE TABLE CAMPS (
    camp_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    camp_name           TEXT NOT NULL,          -- 例: 2026年 春期講習
    planned_start_date   TEXT NOT NULL,          -- 組んでほしい開始日(目標。はみ出す場合あり)
    planned_end_date     TEXT NOT NULL
);

CREATE TABLE CAMP_COURSE_ENROLLMENTS (
    enrollment_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    camp_id                INTEGER NOT NULL REFERENCES CAMPS(camp_id),
    student_id             INTEGER NOT NULL REFERENCES STUDENTS(student_id),
    subject_id             INTEGER NOT NULL REFERENCES SUBJECTS(subject_id),
    contracted_count        INTEGER NOT NULL,     -- 紙に書かれた契約コマ数
    format                 TEXT NOT NULL DEFAULT '1:2'
        CHECK (format IN ('1:1', '1:2')),
    assigned_instructor_id  INTEGER REFERENCES INSTRUCTORS(instructor_id),  -- 例外対応時の指定講師。NULL可
    enrollment_end_date      TEXT  -- この契約の授業を入れてよい最終日。NULLなら講習会全体の終了日をそのまま使う
                                    -- (例: 共通テストのみで使う科目、私立入試前に退塾する生徒など)
);

CREATE TABLE CAMP_STUDENT_AVAILABILITY (
    availability_id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES STUDENTS(student_id),
    slot_id         INTEGER NOT NULL REFERENCES TIME_SLOTS(slot_id),   -- 日付×限が既に紐づく
    is_available    INTEGER NOT NULL CHECK (is_available IN (0, 1)),
    UNIQUE (student_id, slot_id)
);

CREATE TABLE CAMP_INSTRUCTOR_AVAILABILITY (
    availability_id INTEGER PRIMARY KEY AUTOINCREMENT,
    instructor_id   INTEGER NOT NULL REFERENCES INSTRUCTORS(instructor_id),
    slot_id         INTEGER NOT NULL REFERENCES TIME_SLOTS(slot_id),
    is_available    INTEGER NOT NULL CHECK (is_available IN (0, 1)),
    UNIQUE (instructor_id, slot_id)
);

-- 生徒×講習会ごとの「1日の上限コマ数」の個別上書き。
-- 行が無ければ、塾全体の基本値(Python側の定数)を使う。
CREATE TABLE CAMP_STUDENT_MAX_SESSIONS (
    camp_id             INTEGER NOT NULL REFERENCES CAMPS(camp_id),
    student_id           INTEGER NOT NULL REFERENCES STUDENTS(student_id),
    max_sessions_per_day  INTEGER NOT NULL CHECK (max_sessions_per_day BETWEEN 1 AND 5),
    PRIMARY KEY (camp_id, student_id)
);

-- 兄弟などを「同じ日付・同じ限に揃えたい」グループ(講習会ごと)。
CREATE TABLE CAMP_STUDENT_SYNC_GROUPS (
    sync_group_id INTEGER PRIMARY KEY AUTOINCREMENT,
    camp_id        INTEGER NOT NULL REFERENCES CAMPS(camp_id),
    group_name     TEXT NOT NULL
);

CREATE TABLE CAMP_STUDENT_SYNC_GROUP_MEMBERS (
    sync_group_id INTEGER NOT NULL REFERENCES CAMP_STUDENT_SYNC_GROUPS(sync_group_id),
    student_id     INTEGER NOT NULL REFERENCES STUDENTS(student_id),
    PRIMARY KEY (sync_group_id, student_id)
);

-- 出欠記録。通常授業・講習会のどちらの授業も、
-- (日付, 生徒, 科目, 講師, 限)の組み合わせで一意に1件のレコードを持つ。
-- 行が無い状態が「未入力」を表す(未入力とステータスを明示的に区別するため、
-- 「未入力」というステータス値は持たず、行の有無だけで判定する)。
CREATE TABLE ATTENDANCE_RECORDS (
    attendance_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date    TEXT NOT NULL,
    student_id      INTEGER NOT NULL REFERENCES STUDENTS(student_id),
    subject_id      INTEGER NOT NULL REFERENCES SUBJECTS(subject_id),
    instructor_id   INTEGER NOT NULL REFERENCES INSTRUCTORS(instructor_id),
    period_number   INTEGER NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('出席', '欠席')),
    UNIQUE (session_date, student_id, subject_id, instructor_id, period_number)
);

-- ---------------------------------------------------------
-- インデックス（スケジューリング時によく使う検索軸）
-- ---------------------------------------------------------

CREATE INDEX idx_time_slots_date ON TIME_SLOTS(session_date);
CREATE INDEX idx_sessions_slot ON SESSIONS(slot_id);
CREATE INDEX idx_assignments_student ON ASSIGNMENTS(student_id);
CREATE INDEX idx_reg_enroll_student_active ON REGULAR_COURSE_ENROLLMENTS(student_id, effective_end_date);
CREATE INDEX idx_reg_enroll_instructor_active ON REGULAR_COURSE_ENROLLMENTS(instructor_id, effective_end_date);
CREATE INDEX idx_attendance_date ON ATTENDANCE_RECORDS(session_date);
