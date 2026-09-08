# -*- coding: utf-8 -*-
"""
db.py

DB接続とスキーマ初期化だけを担当するモジュール。
他のどのファイルよりも「下」のレイヤーにあり、これ自身は他の自作モジュールに依存しない。
"""

import sqlite3
from pathlib import Path

from image_import_migrations import ensure_image_import_schema

DB_PATH = Path(__file__).parent / "juku_schedule.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

DEFAULT_PERIODS = [
    (1, "14:20", "15:40"), (2, "15:50", "17:10"), (3, "17:20", "18:40"),
    (4, "19:00", "20:20"), (5, "20:30", "21:50"),
]

# 講習会中、生徒1人が1日に受けられるコマ数の基本値(塾全体の方針)。
# 個別の生徒だけ上限を変えたい場合は CAMP_STUDENT_MAX_SESSIONS で上書きする。
DEFAULT_MAX_SESSIONS_PER_DAY = 3

# 既知の科目一覧(course_category, grade_band, track, subject_group, subject_name)。
# 以前の聞き取り内容に基づく、あらかじめ分かっている35パターン。
DEFAULT_SUBJECTS = [
    ("個別指導", "小学生低学年", None, "算国", "算国"),
    ("個別指導", "小学生低学年", None, "算数", "算"),
    ("個別指導", "小学生低学年", None, "国語", "国"),
    ("個別指導", "小学生高学年", "受験", "算数", "算数"),
    ("個別指導", "小学生高学年", "受験", "理科", "理科"),
    ("個別指導", "小学生高学年", "受験", "国語", "国語"),
    ("個別指導", "小学生高学年", "受験", "社会", "社会"),
    ("個別指導", "小学生高学年", "非受験", "算数", "算数"),
    ("個別指導", "小学生高学年", "非受験", "理科", "理科"),
    ("個別指導", "小学生高学年", "非受験", "国語", "国語"),
    ("個別指導", "小学生高学年", "非受験", "社会", "社会"),
    ("個別指導", "中学生", None, "数学", "数学"),
    ("個別指導", "中学生", None, "国語", "国語"),
    ("個別指導", "中学生", None, "英語", "英語"),
    ("個別指導", "中学生", None, "理科", "理科"),
    ("個別指導", "中学生", None, "社会", "社会"),
    ("個別指導", "高校生", None, "数学", "数1A"),
    ("個別指導", "高校生", None, "数学", "数2BC"),
    ("個別指導", "高校生", None, "数学", "数3"),
    ("個別指導", "高校生", None, "情報", "情報I"),
    ("個別指導", "高校生", None, "理科", "物理基礎"),
    ("個別指導", "高校生", None, "理科", "化学基礎"),
    ("個別指導", "高校生", None, "理科", "生物基礎"),
    ("個別指導", "高校生", None, "理科", "地学基礎"),
    ("個別指導", "高校生", None, "理科", "物理"),
    ("個別指導", "高校生", None, "理科", "化学"),
    ("個別指導", "高校生", None, "理科", "生物"),
    ("個別指導", "高校生", None, "理科", "地学"),
    ("個別指導", "高校生", None, "英語", "英語"),
    ("個別指導", "高校生", None, "国語", "現代文"),
    ("個別指導", "高校生", None, "国語", "古典"),
    ("個別指導", "高校生", None, "社会", "地理"),
    ("個別指導", "高校生", None, "社会", "日本史"),
    ("個別指導", "高校生", None, "社会", "世界史"),
    ("個別指導", "高校生", None, "社会", "政治経済"),
    ("個別指導", "高校生", None, "社会", "倫理"),
    ("戦略指導", "中学生", None, "戦略指導", "戦略指導(面談)"),
    ("戦略指導", "高校生", None, "戦略指導", "戦略指導(面談)"),
    ("戦略指導", "中学生", "文系", "教科フォロー", "教科フォロー(文系)"),
    ("戦略指導", "中学生", "理系", "教科フォロー", "教科フォロー(理系)"),
    ("戦略指導", "高校生", "文系", "教科フォロー", "教科フォロー(文系)"),
    ("戦略指導", "高校生", "理系", "教科フォロー", "教科フォロー(理系)"),
]


def ensure_db_exists() -> None:
    """DBファイルが無ければ schema.sql から作成し、PERIODS/SUBJECTS/TERMSが空なら初期値を投入する。"""
    if not DB_PATH.exists():
        if not SCHEMA_PATH.exists():
            raise FileNotFoundError(f"schema.sql が見つかりません: {SCHEMA_PATH}")
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        conn.close()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    if conn.execute("SELECT COUNT(*) FROM PERIODS").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO PERIODS (period_number, start_time, end_time) VALUES (?, ?, ?)",
            DEFAULT_PERIODS,
        )
        conn.commit()
    if conn.execute("SELECT COUNT(*) FROM SUBJECTS").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO SUBJECTS (course_category, grade_band, track, subject_group, subject_name) "
            "VALUES (?, ?, ?, ?, ?)",
            DEFAULT_SUBJECTS,
        )
        conn.commit()
    ensure_terms_exist(conn)
    ensure_image_import_schema(conn)
    conn.close()


def ensure_terms_exist(conn: sqlite3.Connection, years_ahead: int = 2, years_behind: int = 1) -> int:
    """
    「3月〜8月=前期、9月〜翌2月=後期」という固定ルールで、
    今年度を中心に前後複数年度分のTERMSを自動生成する(既存分はスキップ、重複しない)。
    毎回の起動時に呼ばれるため、年をまたいでも自動的に将来の学期が補充され続ける。
    戻り値: 新規作成した件数
    """
    import datetime

    today = datetime.date.today()
    # 3月始まりの年度なので、1〜2月は前の年度扱い
    current_fiscal_year = today.year if today.month >= 3 else today.year - 1

    rows = []
    for y in range(current_fiscal_year - years_behind, current_fiscal_year + years_ahead + 1):
        rows.append((f"{y}年度前期", f"{y}-03-01", f"{y}-08-31"))
        # 後期は年をまたぐため、翌年3月1日の前日(2月末)までとする
        next_march_first = datetime.date(y + 1, 3, 1)
        end_of_late_term = next_march_first - datetime.timedelta(days=1)
        rows.append((f"{y}年度後期", f"{y}-09-01", end_of_late_term.isoformat()))

    before = conn.execute("SELECT COUNT(*) FROM TERMS").fetchone()[0]
    conn.executemany(
        "INSERT OR IGNORE INTO TERMS (term_name, start_date, end_date) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM TERMS").fetchone()[0]
    return after - before


def get_conn() -> sqlite3.Connection:
    """外部キー制約を有効にした状態でDB接続を返す。呼び出し側でclose()すること。"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def get_current_academic_fiscal_year(today=None) -> int:
    """
    塾の年度(3月始まり、以前TERMS等で使っている前期/後期の切り替えに合わせる)を返す。
    1〜2月は前年度扱い。
    """
    import datetime
    today = today or datetime.date.today()
    return today.year if today.month >= 3 else today.year - 1


def get_grade_at_fiscal_year(
    enrollment_year: int,
    base_grade: int,
    academic_fiscal_year: int,
) -> int:
    """入塾年度・入塾時点の学年から、指定年度時点の学年を計算する。"""
    return base_grade + (academic_fiscal_year - enrollment_year)


def get_current_grade(enrollment_year: int, base_grade: int) -> int:
    """
    入塾年度・入塾時点の学年から、「今の学年」を自動計算する。
    (「毎年、全生徒の学年を手動で1つずつ進級させる」という作業を無くすため)
    留年などで実態とズレた場合は、base_gradeを手動修正することを想定している。
    """
    return get_grade_at_fiscal_year(
        enrollment_year,
        base_grade,
        get_current_academic_fiscal_year(),
    )


def format_grade_label(base_grade: int | None) -> str:
    """
    base_grade(1〜12の内部管理用の連番)を、日本の学校制度に沿った表示ラベルに変換する。
    例: 1→小1, 8→中2, 12→高3
    (「8年生」のような、日本の学校制度に存在しない表記を防ぐため)
    """
    if base_grade is None:
        return "-"
    if 1 <= base_grade <= 6:
        return f"小{base_grade}"
    if 7 <= base_grade <= 9:
        return f"中{base_grade - 6}"
    if 10 <= base_grade <= 12:
        return f"高{base_grade - 9}"
    return str(base_grade)  # 想定外の値が入っていた場合のフォールバック


def check_instructor_teaches_subject(conn, instructor_id: int, subject_id: int) -> bool:
    """
    その講師が、その科目をINSTRUCTOR_SUBJECTSに担当科目として登録しているかを確認する。
    (講師を手動で選ぶ画面で、選択ミスに気づけるようにするための安全チェック用)

    算国(小学生低学年)だけは特別扱い: 算国自体を個別に登録する運用ではないため、
    算数・国語のどちらか一方でも登録されていれば「担当できる」とみなす。
    """
    direct = conn.execute(
        "SELECT 1 FROM INSTRUCTOR_SUBJECTS WHERE instructor_id = ? AND subject_id = ?",
        (instructor_id, subject_id),
    ).fetchone()
    if direct:
        return True

    subject_row = conn.execute(
        "SELECT subject_group, grade_band FROM SUBJECTS WHERE subject_id = ?", (subject_id,)
    ).fetchone()
    if subject_row and subject_row[0] == "算国":
        grade_band = subject_row[1]
        found = conn.execute(
            """SELECT 1 FROM INSTRUCTOR_SUBJECTS isub
               JOIN SUBJECTS s ON s.subject_id = isub.subject_id
               WHERE isub.instructor_id = ? AND s.grade_band = ? AND s.subject_group IN ('算数', '国語')""",
            (instructor_id, grade_band),
        ).fetchone()
        return found is not None

    return False


def is_instructor_ng_for_student(conn, student_id: int, instructor_id: int) -> bool:
    """生徒に対して講師が「絶対NG」として登録されているかを返す。"""
    return conn.execute(
        """SELECT 1 FROM STUDENT_INSTRUCTOR_PREFERENCES
           WHERE student_id = ? AND instructor_id = ? AND preference_type = 'NG'""",
        (student_id, instructor_id),
    ).fetchone() is not None


def list_terms(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """学期一覧を取得する(対応可能時間ページなどから参照される共有関数)。"""
    return conn.execute("SELECT term_id, term_name FROM TERMS ORDER BY start_date").fetchall()
