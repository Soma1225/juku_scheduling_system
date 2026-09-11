CREATE TABLE IF NOT EXISTS MAKEUP_SESSIONS (
    makeup_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    attendance_id    INTEGER NOT NULL UNIQUE REFERENCES ATTENDANCE_RECORDS(attendance_id),
    makeup_date      TEXT NOT NULL,
    period_number    INTEGER NOT NULL,
    instructor_id    INTEGER NOT NULL REFERENCES INSTRUCTORS(instructor_id),
    reason_category  TEXT NOT NULL CHECK (reason_category IN ('講師都合','生徒都合','冠婚葬祭')),
    reason_detail    TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_makeup_sessions_date
    ON MAKEUP_SESSIONS(makeup_date, period_number, instructor_id);
