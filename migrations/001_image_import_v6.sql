-- IMAGE_IMPORT_* candidate storage schema, approved revision 6.
-- This migration intentionally does not add a UNIQUE constraint to
-- CAMP_COURSE_ENROLLMENTS; production duplicate investigation is still pending.

CREATE TABLE IMAGE_IMPORT_BATCHES (
    batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    camp_id INTEGER NOT NULL REFERENCES CAMPS(camp_id),
    paper_fiscal_year INTEGER NOT NULL,
    paper_type TEXT NOT NULL,
    layout_key TEXT NOT NULL,
    layout_version INTEGER NOT NULL,
    source_pdf_path TEXT NOT NULL,
    source_pdf_hash TEXT NOT NULL CHECK (LENGTH(source_pdf_hash) = 64),
    page_count INTEGER NOT NULL CHECK (page_count > 0),
    status TEXT NOT NULL DEFAULT 'PROCESSING'
        CHECK (status IN ('PROCESSING','REVIEW_PENDING','REVIEWED','IMPORTED','CANCELLED')),
    uploaded_by_instructor_id INTEGER REFERENCES INSTRUCTORS(instructor_id),
    school_id TEXT,
    school_name TEXT,
    created_at TEXT NOT NULL,
    imported_at TEXT,
    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0,1))
);
CREATE INDEX idx_image_import_batches_camp ON IMAGE_IMPORT_BATCHES(camp_id);
CREATE INDEX idx_image_import_batches_status ON IMAGE_IMPORT_BATCHES(status);
CREATE INDEX idx_image_import_batches_pdf_hash ON IMAGE_IMPORT_BATCHES(source_pdf_hash);

CREATE TABLE IMAGE_IMPORT_PAGES (
    page_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES IMAGE_IMPORT_BATCHES(batch_id),
    page_number INTEGER NOT NULL CHECK (page_number > 0),
    page_image_path TEXT NOT NULL,
    page_image_hash TEXT NOT NULL CHECK (LENGTH(page_image_hash) = 64),
    recognized_name_text TEXT,
    recognized_grade_text TEXT,
    layout_quality TEXT NOT NULL DEFAULT 'OK'
        CHECK (layout_quality IN ('OK','PARTIAL_UNREADABLE','STRUCTURE_FAILED','ILLEGIBLE')),
    processing_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (processing_status IN ('PENDING','RECOGNIZED','REVIEWED','IMPORTED','SKIPPED')),
    created_at TEXT NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0,1)),
    UNIQUE (batch_id, page_number)
);
CREATE INDEX idx_image_import_pages_batch ON IMAGE_IMPORT_PAGES(batch_id);
CREATE INDEX idx_image_import_pages_hash ON IMAGE_IMPORT_PAGES(page_image_hash);

CREATE TABLE IMAGE_IMPORT_PAGE_STUDENTS (
    page_student_id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL REFERENCES IMAGE_IMPORT_PAGES(page_id),
    candidate_student_id INTEGER REFERENCES STUDENTS(student_id),
    candidate_rank INTEGER,
    match_confidence REAL CHECK (match_confidence IS NULL OR match_confidence BETWEEN 0.0 AND 1.0),
    match_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (match_status IN ('AUTO_MATCHED','AMBIGUOUS','NOT_FOUND','MANUALLY_CONFIRMED','PENDING')),
    is_selected INTEGER NOT NULL DEFAULT 0 CHECK (is_selected IN (0,1)),
    reviewed_by_instructor_id INTEGER REFERENCES INSTRUCTORS(instructor_id),
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0,1)),
    CHECK (
        (match_status = 'NOT_FOUND' AND candidate_student_id IS NULL AND is_selected = 0) OR
        (match_status IN ('PENDING','AMBIGUOUS') AND candidate_student_id IS NOT NULL AND is_selected = 0) OR
        (match_status = 'AUTO_MATCHED' AND candidate_student_id IS NOT NULL AND is_selected = 1) OR
        (match_status = 'MANUALLY_CONFIRMED' AND candidate_student_id IS NOT NULL AND is_selected = 1
            AND reviewed_by_instructor_id IS NOT NULL AND reviewed_at IS NOT NULL)
    ),
    CHECK (candidate_rank IS NULL OR candidate_rank >= 1)
);
CREATE INDEX idx_image_import_page_students_page ON IMAGE_IMPORT_PAGE_STUDENTS(page_id);
CREATE INDEX idx_image_import_page_students_student ON IMAGE_IMPORT_PAGE_STUDENTS(candidate_student_id);
CREATE UNIQUE INDEX uq_image_import_page_students_selected
    ON IMAGE_IMPORT_PAGE_STUDENTS(page_id) WHERE is_selected = 1 AND is_deleted = 0;
CREATE UNIQUE INDEX uq_image_import_page_student_candidate
    ON IMAGE_IMPORT_PAGE_STUDENTS(page_id,candidate_student_id)
    WHERE candidate_student_id IS NOT NULL AND is_deleted = 0;

CREATE TABLE IMAGE_IMPORT_SUBJECT_ENROLLMENTS (
    import_enrollment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL REFERENCES IMAGE_IMPORT_PAGES(page_id),
    subject_row_label TEXT NOT NULL,
    recognized_detail_text TEXT,
    detail_crop_image_path TEXT,
    recognized_count_text TEXT,
    recognized_count INTEGER CHECK (recognized_count IS NULL OR recognized_count >= 0),
    recognized_count_confidence REAL
        CHECK (recognized_count_confidence IS NULL OR recognized_count_confidence BETWEEN 0.0 AND 1.0),
    resolved_count INTEGER CHECK (resolved_count IS NULL OR resolved_count >= 0),
    count_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (count_status IN ('PENDING','AUTO_RECOGNIZED','AUTO_BLANK','AMBIGUOUS','MANUALLY_CONFIRMED')),
    recognized_subject_id INTEGER REFERENCES SUBJECTS(subject_id),
    resolved_subject_id INTEGER REFERENCES SUBJECTS(subject_id),
    subject_match_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (subject_match_status IN ('AUTO_MATCHED','AMBIGUOUS','NOT_FOUND','MANUALLY_CONFIRMED','PENDING')),
    imported_enrollment_id INTEGER REFERENCES CAMP_COURSE_ENROLLMENTS(enrollment_id),
    reviewed_by_instructor_id INTEGER REFERENCES INSTRUCTORS(instructor_id),
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0,1)),
    CHECK (
        (count_status IN ('PENDING','AMBIGUOUS') AND resolved_count IS NULL) OR
        (count_status = 'AUTO_BLANK' AND recognized_count IS 0 AND resolved_count IS 0) OR
        (count_status = 'AUTO_RECOGNIZED' AND resolved_count IS NOT NULL AND resolved_count = recognized_count) OR
        (count_status = 'MANUALLY_CONFIRMED' AND resolved_count IS NOT NULL
            AND reviewed_by_instructor_id IS NOT NULL AND reviewed_at IS NOT NULL)
    ),
    CHECK (
        (subject_match_status IN ('PENDING','AMBIGUOUS','NOT_FOUND') AND resolved_subject_id IS NULL) OR
        (subject_match_status = 'AUTO_MATCHED' AND recognized_subject_id IS NOT NULL
            AND resolved_subject_id IS NOT NULL
            AND resolved_subject_id = recognized_subject_id) OR
        (subject_match_status = 'MANUALLY_CONFIRMED' AND resolved_subject_id IS NOT NULL
            AND reviewed_by_instructor_id IS NOT NULL AND reviewed_at IS NOT NULL)
    )
);
CREATE INDEX idx_image_import_subject_enroll_page ON IMAGE_IMPORT_SUBJECT_ENROLLMENTS(page_id);
CREATE INDEX idx_image_import_subject_enroll_subject ON IMAGE_IMPORT_SUBJECT_ENROLLMENTS(resolved_subject_id);

CREATE TABLE IMAGE_IMPORT_AVAILABILITY (
    import_availability_id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL REFERENCES IMAGE_IMPORT_PAGES(page_id),
    session_date TEXT NOT NULL,
    period_number INTEGER NOT NULL REFERENCES PERIODS(period_number),
    matched_slot_id INTEGER REFERENCES TIME_SLOTS(slot_id),
    ink_ratio REAL CHECK (ink_ratio IS NULL OR ink_ratio BETWEEN 0.0 AND 1.0),
    line_crossing_score REAL CHECK (line_crossing_score IS NULL OR line_crossing_score BETWEEN 0.0 AND 1.0),
    recognition_confidence REAL
        CHECK (recognition_confidence IS NULL OR recognition_confidence BETWEEN 0.0 AND 1.0),
    recognized_is_available INTEGER CHECK (recognized_is_available IS NULL OR recognized_is_available IN (0,1)),
    resolved_is_available INTEGER CHECK (resolved_is_available IS NULL OR resolved_is_available IN (0,1)),
    availability_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (availability_status IN ('AUTO_AVAILABLE','AUTO_UNAVAILABLE','AMBIGUOUS',
            'SLOT_NOT_FOUND','OUT_OF_CAMP_RANGE','MANUALLY_CONFIRMED','PENDING')),
    imported_availability_id INTEGER REFERENCES CAMP_STUDENT_AVAILABILITY(availability_id),
    reviewed_by_instructor_id INTEGER REFERENCES INSTRUCTORS(instructor_id),
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0,1)),
    UNIQUE (page_id,session_date,period_number),
    CHECK (
        (availability_status = 'PENDING' AND recognized_is_available IS NULL AND resolved_is_available IS NULL) OR
        (availability_status = 'AMBIGUOUS' AND resolved_is_available IS NULL) OR
        (availability_status = 'AUTO_AVAILABLE'
            AND recognized_is_available IS 1 AND resolved_is_available IS 1) OR
        (availability_status = 'AUTO_UNAVAILABLE'
            AND recognized_is_available IS 0 AND resolved_is_available IS 0) OR
        (availability_status = 'MANUALLY_CONFIRMED' AND resolved_is_available IS NOT NULL
            AND reviewed_by_instructor_id IS NOT NULL AND reviewed_at IS NOT NULL) OR
        (availability_status IN ('SLOT_NOT_FOUND','OUT_OF_CAMP_RANGE') AND resolved_is_available IS NULL)
    )
);
CREATE INDEX idx_image_import_availability_page ON IMAGE_IMPORT_AVAILABILITY(page_id);
CREATE INDEX idx_image_import_availability_slot ON IMAGE_IMPORT_AVAILABILITY(matched_slot_id);

CREATE TABLE IMAGE_IMPORT_REVIEW_ITEMS (
    review_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL REFERENCES IMAGE_IMPORT_PAGES(page_id),
    item_type TEXT NOT NULL CHECK (item_type IN ('STUDENT_MATCH','SUBJECT_MATCH','COUNT_AMBIGUOUS',
        'AVAILABILITY_AMBIGUOUS','SLOT_NOT_FOUND','OUT_OF_CAMP_RANGE')),
    related_page_student_id INTEGER REFERENCES IMAGE_IMPORT_PAGE_STUDENTS(page_student_id),
    related_subject_enrollment_id INTEGER REFERENCES IMAGE_IMPORT_SUBJECT_ENROLLMENTS(import_enrollment_id),
    related_availability_id INTEGER REFERENCES IMAGE_IMPORT_AVAILABILITY(import_availability_id),
    crop_image_path TEXT,
    candidate_value_text TEXT,
    resolution TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (resolution IN ('PENDING','APPROVED','CORRECTED','REJECTED')),
    corrected_value_text TEXT,
    resolved_by_instructor_id INTEGER REFERENCES INSTRUCTORS(instructor_id),
    resolved_at TEXT,
    created_at TEXT NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0,1)),
    CHECK (
        (item_type = 'STUDENT_MATCH' AND related_page_student_id IS NOT NULL
            AND related_subject_enrollment_id IS NULL AND related_availability_id IS NULL) OR
        (item_type IN ('SUBJECT_MATCH','COUNT_AMBIGUOUS') AND related_subject_enrollment_id IS NOT NULL
            AND related_page_student_id IS NULL AND related_availability_id IS NULL) OR
        (item_type IN ('AVAILABILITY_AMBIGUOUS','SLOT_NOT_FOUND','OUT_OF_CAMP_RANGE')
            AND related_availability_id IS NOT NULL AND related_page_student_id IS NULL
            AND related_subject_enrollment_id IS NULL)
    ),
    CHECK (
        (resolution = 'PENDING' AND resolved_by_instructor_id IS NULL
            AND resolved_at IS NULL AND corrected_value_text IS NULL) OR
        (resolution IN ('APPROVED','REJECTED') AND resolved_by_instructor_id IS NOT NULL
            AND resolved_at IS NOT NULL AND corrected_value_text IS NULL) OR
        (resolution = 'CORRECTED' AND corrected_value_text IS NOT NULL
            AND resolved_by_instructor_id IS NOT NULL AND resolved_at IS NOT NULL)
    )
);
CREATE INDEX idx_image_import_review_items_page ON IMAGE_IMPORT_REVIEW_ITEMS(page_id);
CREATE INDEX idx_image_import_review_items_resolution ON IMAGE_IMPORT_REVIEW_ITEMS(resolution);

CREATE TABLE IMAGE_IMPORT_ATTACHMENTS (
    attachment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL REFERENCES IMAGE_IMPORT_PAGES(page_id),
    attachment_type TEXT NOT NULL
        CHECK (attachment_type IN ('CURRICULUM_REQUEST','REMARKS','OTHER_FREE_TEXT')),
    crop_image_path TEXT NOT NULL,
    crop_image_hash TEXT NOT NULL CHECK (LENGTH(crop_image_hash) = 64),
    created_at TEXT NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0,1))
);
CREATE INDEX idx_image_import_attachments_page ON IMAGE_IMPORT_ATTACHMENTS(page_id);

CREATE TABLE IMAGE_IMPORT_AUDIT_LOG (
    audit_log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES IMAGE_IMPORT_BATCHES(batch_id),
    page_id INTEGER REFERENCES IMAGE_IMPORT_PAGES(page_id),
    action_type TEXT NOT NULL CHECK (action_type IN ('UPLOAD','RECOGNIZE','APPROVE','CORRECT',
        'REJECT','IMPORT','DELETE','RESTORE','DUPLICATE_SELECTED')),
    target_table TEXT NOT NULL,
    target_id INTEGER,
    before_value_json TEXT,
    after_value_json TEXT,
    source_pdf_path TEXT,
    source_page_number INTEGER,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('SYSTEM','INSTRUCTOR')),
    operator_instructor_id INTEGER REFERENCES INSTRUCTORS(instructor_id),
    operator_name TEXT,
    school_id TEXT,
    school_name TEXT,
    created_at TEXT NOT NULL,
    CHECK (
        (actor_type = 'SYSTEM' AND operator_instructor_id IS NULL AND operator_name IS NULL) OR
        (actor_type = 'INSTRUCTOR' AND operator_instructor_id IS NOT NULL AND operator_name IS NOT NULL)
    )
);
CREATE INDEX idx_image_import_audit_log_batch ON IMAGE_IMPORT_AUDIT_LOG(batch_id);
CREATE INDEX idx_image_import_audit_log_created ON IMAGE_IMPORT_AUDIT_LOG(created_at);

CREATE TRIGGER trg_image_import_audit_log_no_update
BEFORE UPDATE ON IMAGE_IMPORT_AUDIT_LOG BEGIN
    SELECT RAISE(ABORT,'IMAGE_IMPORT_AUDIT_LOG is append-only: UPDATE is not allowed');
END;
CREATE TRIGGER trg_image_import_audit_log_no_delete
BEFORE DELETE ON IMAGE_IMPORT_AUDIT_LOG BEGIN
    SELECT RAISE(ABORT,'IMAGE_IMPORT_AUDIT_LOG is append-only: DELETE is not allowed');
END;

CREATE TRIGGER trg_image_import_review_items_page_consistency
BEFORE INSERT ON IMAGE_IMPORT_REVIEW_ITEMS BEGIN
    SELECT RAISE(ABORT,'related_page_student_id must belong to the same page_id as the review item')
    WHERE NEW.related_page_student_id IS NOT NULL AND NEW.page_id <> (
        SELECT page_id FROM IMAGE_IMPORT_PAGE_STUDENTS WHERE page_student_id = NEW.related_page_student_id);
    SELECT RAISE(ABORT,'related_subject_enrollment_id must belong to the same page_id as the review item')
    WHERE NEW.related_subject_enrollment_id IS NOT NULL AND NEW.page_id <> (
        SELECT page_id FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS
        WHERE import_enrollment_id = NEW.related_subject_enrollment_id);
    SELECT RAISE(ABORT,'related_availability_id must belong to the same page_id as the review item')
    WHERE NEW.related_availability_id IS NOT NULL AND NEW.page_id <> (
        SELECT page_id FROM IMAGE_IMPORT_AVAILABILITY
        WHERE import_availability_id = NEW.related_availability_id);
END;

CREATE TRIGGER trg_image_import_review_items_target_immutable
BEFORE UPDATE OF page_id,item_type,related_page_student_id,
    related_subject_enrollment_id,related_availability_id,created_at
ON IMAGE_IMPORT_REVIEW_ITEMS
WHEN NEW.page_id IS NOT OLD.page_id
 OR NEW.item_type IS NOT OLD.item_type
 OR NEW.related_page_student_id IS NOT OLD.related_page_student_id
 OR NEW.related_subject_enrollment_id IS NOT OLD.related_subject_enrollment_id
 OR NEW.related_availability_id IS NOT OLD.related_availability_id
 OR NEW.created_at IS NOT OLD.created_at
BEGIN
    SELECT RAISE(ABORT,'review item target and created_at columns are immutable after creation');
END;

CREATE TRIGGER trg_image_import_review_items_pending_content_only
BEFORE UPDATE OF crop_image_path,candidate_value_text
ON IMAGE_IMPORT_REVIEW_ITEMS
WHEN (NEW.crop_image_path IS NOT OLD.crop_image_path
      OR NEW.candidate_value_text IS NOT OLD.candidate_value_text)
 AND (OLD.resolution <> 'PENDING' OR NEW.resolution <> 'PENDING')
BEGIN
    SELECT RAISE(ABORT,'crop_image_path and candidate_value_text can only be changed while resolution is PENDING');
END;

CREATE TRIGGER trg_image_import_review_items_resolution_final
BEFORE UPDATE OF resolution ON IMAGE_IMPORT_REVIEW_ITEMS
WHEN OLD.resolution <> 'PENDING' AND NEW.resolution IS NOT OLD.resolution
BEGIN
    SELECT RAISE(ABORT,'a resolved review item cannot be reopened or changed');
END;

-- Mutable columns: resolution, corrected_value_text,
-- resolved_by_instructor_id, resolved_at, is_deleted, crop_image_path and
-- candidate_value_text. The latter two are mutable only while resolution is PENDING.
