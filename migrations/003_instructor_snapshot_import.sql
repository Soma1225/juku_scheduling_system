CREATE TABLE INSTRUCTORS_NEW (
    instructor_id INTEGER PRIMARY KEY AUTOINCREMENT,
    last_name TEXT NOT NULL,
    first_name TEXT NOT NULL,
    last_name_kana TEXT NOT NULL,
    first_name_kana TEXT NOT NULL,
    external_instructor_id TEXT UNIQUE,
    short_name TEXT UNIQUE,
    academic_year TEXT
        CHECK (academic_year IS NULL OR academic_year IN (
            'B1','B2','B3','B4','B5','B6','B7','B8','B9','B10','B11','B12',
            'M1','M2','M3','M4','D1','D2','D3','D4','D5','D6'
        )),
    status TEXT NOT NULL DEFAULT '在籍'
        CHECK (status IN ('在籍', '休職', '辞職')),
    academic_year_confirmed_fiscal_year INTEGER
);

INSERT INTO INSTRUCTORS_NEW (
    instructor_id, last_name, first_name, last_name_kana, first_name_kana,
    external_instructor_id, short_name, academic_year, status,
    academic_year_confirmed_fiscal_year
)
SELECT instructor_id, last_name, first_name, last_name_kana, first_name_kana,
       external_instructor_id, NULL, academic_year, status,
       academic_year_confirmed_fiscal_year
FROM INSTRUCTORS;

DROP TABLE INSTRUCTORS;
ALTER TABLE INSTRUCTORS_NEW RENAME TO INSTRUCTORS;
