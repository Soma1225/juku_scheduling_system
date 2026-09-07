-- 講習会用紙の配布対象・配布済み・回収済みを生徒単位で追跡する。
-- IMAGE_IMPORT_* 8テーブルは候補保存専用のため変更しない。

CREATE TABLE CAMP_FORM_DISTRIBUTIONS (
    distribution_id INTEGER PRIMARY KEY AUTOINCREMENT,
    camp_id INTEGER NOT NULL REFERENCES CAMPS(camp_id),
    student_id INTEGER NOT NULL REFERENCES STUDENTS(student_id),
    source TEXT NOT NULL CHECK (source IN ('AUTO_REGULAR','MANUAL_EXCEPTION','SCAN_DISCOVERED')),
    status TEXT NOT NULL DEFAULT 'TARGET'
        CHECK (status IN ('TARGET','DISTRIBUTED','RETURNED','EXEMPT')),
    distributed_at TEXT,
    returned_at TEXT,
    returned_page_id INTEGER REFERENCES IMAGE_IMPORT_PAGES(page_id),
    created_by_instructor_id INTEGER REFERENCES INSTRUCTORS(instructor_id),
    updated_by_instructor_id INTEGER REFERENCES INSTRUCTORS(instructor_id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0,1)),
    UNIQUE (camp_id,student_id),
    CHECK (
        (status = 'TARGET' AND distributed_at IS NULL AND returned_at IS NULL) OR
        (status = 'DISTRIBUTED' AND distributed_at IS NOT NULL AND returned_at IS NULL) OR
        (status = 'RETURNED' AND returned_at IS NOT NULL) OR
        (status = 'EXEMPT' AND returned_at IS NULL)
    )
);
CREATE INDEX idx_camp_form_distributions_camp_status
    ON CAMP_FORM_DISTRIBUTIONS(camp_id,status,is_deleted);
CREATE INDEX idx_camp_form_distributions_student
    ON CAMP_FORM_DISTRIBUTIONS(student_id,is_deleted);
