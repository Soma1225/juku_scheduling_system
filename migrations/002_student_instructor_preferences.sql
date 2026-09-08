-- 生徒と講師の相性設定。講習会には紐づけず、すべての講習会で共有する。
CREATE TABLE IF NOT EXISTS STUDENT_INSTRUCTOR_PREFERENCES (
    preference_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES STUDENTS(student_id),
    instructor_id   INTEGER NOT NULL REFERENCES INSTRUCTORS(instructor_id),
    preference_type TEXT NOT NULL CHECK (preference_type IN ('PREFERRED', 'NG')),
    priority_rank   INTEGER,
    created_at      TEXT NOT NULL,
    CHECK (
        (preference_type = 'PREFERRED' AND priority_rank IS NOT NULL AND priority_rank >= 1)
        OR
        (preference_type = 'NG' AND priority_rank IS NULL)
    ),
    UNIQUE (student_id, instructor_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_student_instructor_pref_rank
    ON STUDENT_INSTRUCTOR_PREFERENCES(student_id, priority_rank)
    WHERE preference_type = 'PREFERRED';

CREATE INDEX IF NOT EXISTS idx_student_instructor_preferences_student
    ON STUDENT_INSTRUCTOR_PREFERENCES(student_id, preference_type);
