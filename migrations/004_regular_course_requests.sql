-- 通常授業の自動組み前の希望（科目・週あたり回数）を保持する。
CREATE TABLE IF NOT EXISTS REGULAR_COURSE_REQUESTS (
    request_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id              INTEGER NOT NULL REFERENCES STUDENTS(student_id),
    term_id                 INTEGER NOT NULL REFERENCES TERMS(term_id),
    subject_id              INTEGER NOT NULL REFERENCES SUBJECTS(subject_id),
    desired_count_per_week  INTEGER NOT NULL CHECK (desired_count_per_week BETWEEN 1 AND 5),
    format                  TEXT NOT NULL DEFAULT '1:2'
        CHECK (format IN ('1:1', '1:2')),
    assigned_instructor_id  INTEGER REFERENCES INSTRUCTORS(instructor_id),
    status                  TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'SCHEDULED', 'CANCELLED')),
    UNIQUE (student_id, term_id, subject_id)
);

CREATE INDEX IF NOT EXISTS idx_regular_course_requests_term_status
    ON REGULAR_COURSE_REQUESTS(term_id, status);
CREATE INDEX IF NOT EXISTS idx_regular_course_requests_student
    ON REGULAR_COURSE_REQUESTS(student_id, term_id);
