import random
from db import ensure_db_exists, get_conn
from page_camps import insert_camp

random.seed(42)
ensure_db_exists()
conn = get_conn()

N_STUDENTS = 100
N_INSTRUCTORS = 18

# --- 生徒・講師を大量投入 ---
last_names = ['山田','鈴木','田中','佐藤','高橋','伊藤','渡辺','中村','小林','加藤']
first_names = ['太郎','花子','次郎','三郎','四郎','美咲','健太','optional']
for i in range(N_STUDENTS):
    ln = random.choice(last_names)
    fn = f"生徒{i}"
    grade = random.choice([7,8,9,10,11,12])  # 中1〜高3
    conn.execute(
        "INSERT INTO STUDENTS (last_name,first_name,last_name_kana,first_name_kana,enrollment_year,base_grade,enrollment_status) VALUES (?,?,?,?,?,?,?)",
        (ln, fn, 'かな', 'かな', 2026, grade, '在籍')
    )

for i in range(N_INSTRUCTORS):
    conn.execute(
        "INSERT INTO INSTRUCTORS (last_name,first_name,last_name_kana,first_name_kana,academic_year) VALUES (?,?,?,?,?)",
        (f"講師{i}", "先生", "かな", "かな", random.choice(['B2','B3','B4','M1','M2']))
    )
conn.commit()

student_ids = [r[0] for r in conn.execute("SELECT student_id FROM STUDENTS").fetchall()]
instructor_ids = [r[0] for r in conn.execute("SELECT instructor_id FROM INSTRUCTORS").fetchall()]

# --- 講習会登録(5週間) ---
camp_id, n_slots = insert_camp(conn, 2026, '夏期講習会', '2026-07-20', '2026-08-23')
print(f"camp_id={camp_id}, TIME_SLOTS={n_slots}件")

# --- 中学生・高校生向けの主要科目だけ使う ---
subject_rows = conn.execute(
    "SELECT subject_id, grade_band FROM SUBJECTS WHERE grade_band IN ('中学生','高校生') AND course_category='個別指導'"
).fetchall()
subjects_by_grade = {}
for sid, gb in subject_rows:
    subjects_by_grade.setdefault(gb, []).append(sid)

def grade_band_of(grade):
    return '中学生' if grade <= 9 else '高校生'

# --- 講師の担当科目登録(科目ごとに2〜3人、フォールバックで拾われるようにする) ---
for gb, sids in subjects_by_grade.items():
    for sid in sids:
        chosen = random.sample(instructor_ids, k=min(3, len(instructor_ids)))
        for iid in chosen:
            conn.execute(
                "INSERT OR IGNORE INTO INSTRUCTOR_SUBJECTS (instructor_id, subject_id, proficiency_level) VALUES (?,?,?)",
                (iid, sid, random.choice([1, 2]))
            )
conn.commit()

# --- 受講契約(生徒ごとに1〜2科目、4〜8コマ) ---
enrollment_count = 0
for student_id in student_ids:
    grade = conn.execute("SELECT base_grade FROM STUDENTS WHERE student_id=?", (student_id,)).fetchone()[0]
    gb = grade_band_of(grade)
    candidates = subjects_by_grade.get(gb, [])
    if not candidates:
        continue
    n_subjects = random.choice([1, 1, 2])
    chosen_subjects = random.sample(candidates, k=min(n_subjects, len(candidates)))
    for sid in chosen_subjects:
        count = random.choice([4, 5, 6, 8])
        fmt = random.choice(['1:2', '1:2', '1:2', '1:1'])
        conn.execute(
            "INSERT INTO CAMP_COURSE_ENROLLMENTS (camp_id, student_id, subject_id, contracted_count, format) VALUES (?,?,?,?,?)",
            (camp_id, student_id, sid, count, fmt)
        )
        enrollment_count += 1
conn.commit()
print(f"受講契約数: {enrollment_count}")

# --- 対応可能時間: 生徒・講師とも、平日はランダムに70%程度の確率で対応可能 ---
all_slots = conn.execute(
    "SELECT slot_id, session_date, period_number FROM TIME_SLOTS WHERE session_date BETWEEN '2026-07-20' AND '2026-08-23'"
).fetchall()

import datetime
def is_sunday(date_str):
    y,m,d = map(int, date_str.split('-'))
    return datetime.date(y,m,d).weekday() == 6

student_avail_rows = []
instructor_avail_rows = []
for sid, date_str, period in all_slots:
    if is_sunday(date_str):
        continue
    for student_id in student_ids:
        if random.random() < 0.6:
            student_avail_rows.append((student_id, sid))
    for instructor_id in instructor_ids:
        if random.random() < 0.7:
            instructor_avail_rows.append((instructor_id, sid))

conn.executemany("INSERT INTO CAMP_STUDENT_AVAILABILITY (student_id, slot_id, is_available) VALUES (?,?,1)", student_avail_rows)
conn.executemany("INSERT INTO CAMP_INSTRUCTOR_AVAILABILITY (instructor_id, slot_id, is_available) VALUES (?,?,1)", instructor_avail_rows)
conn.commit()
print(f"生徒対応可能枠: {len(student_avail_rows)}件, 講師対応可能枠: {len(instructor_avail_rows)}件")

conn.close()
print("データ生成完了")
