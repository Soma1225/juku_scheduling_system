# -*- coding: utf-8 -*-
"""
excel_import.py

通常授業の時間割Excel(「時間割一覧」シート形式)を読み込み、
生の(曜日, 限, 講師名, 生徒表記, 科目名)のレコード一覧を組み立てる。

このシートの構造:
- B列に曜日(月・火・水・木・金・土)が現れる行が、その曜日ブロックの開始行
- 各曜日ブロックの中で、5限分の「教員・生徒名・科目・出欠」の4列セットが
  横に並んでいる(1限=D〜G, 2限=H〜K, 3限=L〜O, 4限=P〜S, 5限=T〜W)
- 同じ講師が複数の生徒を続けて担当する場合、2行目以降は講師名セルが
  空欄になっている(1行目の講師名を引き継ぐ)
"""

import openpyxl
from db import format_grade_label

WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]

# 各限の(教員列, 生徒名列, 科目列, 出欠列)。列番号(1始まり: A=1, B=2, ...)
PERIOD_COLUMNS = {
    1: (4, 5, 6, 7),      # D, E, F, G
    2: (8, 9, 10, 11),    # H, I, J, K
    3: (12, 13, 14, 15),  # L, M, N, O
    4: (16, 17, 18, 19),  # P, Q, R, S
    5: (20, 21, 22, 23),  # T, U, V, W
}


def find_day_block_rows(ws) -> list[tuple[str, int, int]]:
    """
    B列を走査し、(曜日, 開始行, 終了行)のリストを返す。
    終了行は「次の曜日ブロックの開始行の1つ手前」(最後のブロックはシート末尾まで)。
    """
    markers = []
    for r in range(1, ws.max_row + 1):
        v = ws.cell(row=r, column=2).value
        if v in WEEKDAYS:
            markers.append((v, r))

    blocks = []
    for i, (day, start_row) in enumerate(markers):
        end_row = markers[i + 1][1] - 1 if i + 1 < len(markers) else ws.max_row
        blocks.append((day, start_row, end_row))
    return blocks


def parse_regular_timetable(file_path: str) -> list[dict]:
    """
    通常授業Excelを読み込み、生のレコード一覧を返す。
    各レコード: {"day_of_week": "月", "period_number": 3, "instructor_text": "河原 優真",
                 "student_text": "小6　アルタンベコフ 一路", "subject_text": "理科"}
    """
    wb = openpyxl.load_workbook(file_path, data_only=True)
    ws = wb["時間割一覧"]

    records = []
    for day, start_row, end_row in find_day_block_rows(ws):
        for period, (col_instructor, col_student, col_subject, _col_attendance) in PERIOD_COLUMNS.items():
            current_instructor = None
            for r in range(start_row, end_row + 1):
                instructor_cell = ws.cell(row=r, column=col_instructor).value
                if instructor_cell:
                    current_instructor = str(instructor_cell).strip()

                subject_cell = ws.cell(row=r, column=col_subject).value
                if subject_cell:
                    student_cell = ws.cell(row=r, column=col_student).value
                    records.append({
                        "day_of_week": day,
                        "period_number": period,
                        "instructor_text": current_instructor,
                        "student_text": str(student_cell).strip() if student_cell else None,
                        "subject_text": str(subject_cell).strip(),
                    })
    return records


def split_grade_and_name(student_text: str) -> tuple[str | None, str]:
    """
    「中2　川口 真慶」のような表記を、(学年表記, 氏名)に分割する。
    学年表記が見つからない場合は (None, 元の文字列) を返す。
    """
    if not student_text:
        return None, ""
    # 全角/半角スペース、タブなど、区切りとして使われる空白文字で分割
    import re
    parts = re.split(r"[\s\u3000]+", student_text.strip(), maxsplit=1)
    if len(parts) == 2 and _looks_like_grade(parts[0]):
        return parts[0], parts[1]
    return None, student_text.strip()


_GRADE_PREFIXES = ("小", "中", "高")


def _looks_like_grade(text: str) -> bool:
    return len(text) >= 2 and text[0] in _GRADE_PREFIXES


def grade_text_to_base_grade(grade_text: str | None) -> int | None:
    """
    「小６」「中3」「高１」のような表記(全角/半角混在)を、base_grade(1〜12)に変換する。
    変換できなければNoneを返す。
    """
    if not grade_text:
        return None
    import unicodedata
    normalized = unicodedata.normalize("NFKC", grade_text)  # 全角数字を半角に統一
    prefix = normalized[0]
    if prefix not in _GRADE_PREFIXES:
        return None
    try:
        n = int(normalized[1:])
    except ValueError:
        return None
    offset = {"小": 0, "中": 6, "高": 9}[prefix]
    base_grade = offset + n
    return base_grade if 1 <= base_grade <= 12 else None


# ---------------------------------------------------------
# システム側データ(STUDENTS/INSTRUCTORS)との照合
# ---------------------------------------------------------

def _normalize_name(name: str) -> str:
    """氏名の照合用に、空白の差異(全角/半角/連続)を吸収する。"""
    import re
    return re.sub(r"[\s\u3000]+", "", name)


def match_student(conn, student_text: str) -> dict:
    """
    Excel上の生徒表記(例: 「中2　川口 真慶」)を、STUDENTSと照合する。
    学年+氏名の完全一致で検索する。
    戻り値: {"excel_text":..., "status": "matched"|"ambiguous"|"not_found",
             "grade_label": ..., "candidates": [{"id":..., "label":...}, ...]}
    """
    grade_text, name = split_grade_and_name(student_text)
    base_grade = grade_text_to_base_grade(grade_text)
    target_name = _normalize_name(name)

    rows = conn.execute(
        "SELECT student_id, last_name, first_name, base_grade, enrollment_status FROM STUDENTS"
    ).fetchall()

    candidates = []
    for student_id, last_name, first_name, sg, status in rows:
        if _normalize_name(last_name + first_name) != target_name:
            continue
        if base_grade is not None and sg != base_grade:
            continue  # 学年が分かっている場合は、学年も一致するものだけ候補にする
        candidates.append({
            "id": student_id,
            "label": f"{last_name} {first_name}（{format_grade_label(sg)}・{status}）",
        })

    if len(candidates) == 1:
        status_result = "matched"
    elif len(candidates) > 1:
        status_result = "ambiguous"
    else:
        status_result = "not_found"

    return {
        "excel_text": student_text, "status": status_result,
        "grade_label": grade_text or "-", "candidates": candidates,
    }


def match_instructor(conn, instructor_text: str) -> dict:
    """
    Excel上の講師名を、INSTRUCTORSと照合する(氏名のみでの完全一致、学年の概念が無いため)。
    """
    target_name = _normalize_name(instructor_text)
    rows = conn.execute(
        "SELECT instructor_id, last_name, first_name, external_instructor_id, status FROM INSTRUCTORS"
    ).fetchall()

    candidates = []
    for instructor_id, last_name, first_name, ext_id, status in rows:
        if _normalize_name(last_name + first_name) != target_name:
            continue
        ext_part = f"　講師番号: {ext_id}" if ext_id else ""
        candidates.append({
            "id": instructor_id, "label": f"{last_name} {first_name}（{status}）{ext_part}",
        })

    if len(candidates) == 1:
        status_result = "matched"
    elif len(candidates) > 1:
        status_result = "ambiguous"
    else:
        status_result = "not_found"

    return {"excel_text": instructor_text, "status": status_result, "candidates": candidates}


def build_match_results(conn, records: list[dict]) -> dict:
    """
    parse_regular_timetable()の結果全体から、生徒・講師それぞれの
    「Excel上でユニークな表記」ごとに1回だけ照合し、結果をまとめて返す。
    (同じ生徒が何度も出てくるExcelなので、表記の重複を除いてから照合する)
    """
    unique_students = sorted({r["student_text"] for r in records if r["student_text"]})
    unique_instructors = sorted({r["instructor_text"] for r in records if r["instructor_text"]})

    student_results = [match_student(conn, text) for text in unique_students]
    instructor_results = [match_instructor(conn, text) for text in unique_instructors]

    return {"students": student_results, "instructors": instructor_results}


# ---------------------------------------------------------
# 科目の解決(数学・教科フォローは学年/文理によって具体的な科目名が変わるため、
# Excel上の大まかな表記から、SUBJECTSの具体的な行を絞り込む)
# ---------------------------------------------------------

def _grade_band_of(base_grade: int) -> str | None:
    if base_grade is None:
        return None
    if 1 <= base_grade <= 3:
        return "小学生低学年"
    if 4 <= base_grade <= 6:
        return "小学生高学年"
    if 7 <= base_grade <= 9:
        return "中学生"
    if 10 <= base_grade <= 12:
        return "高校生"
    return None


def resolve_subject(conn, subject_text: str, base_grade: int, track: str | None) -> dict:
    """
    Excel上の科目表記(例: 「数学」「教科フォロー」「理科」「戦略面談」)を、
    生徒の学年・文理から、SUBJECTSの具体的な1行に絞り込む。

    戻り値: {"excel_text":..., "status": "matched"|"ambiguous"|"not_found",
             "resolved_track": ...(教科フォロー解決時にSTUDENTS.trackへ書き戻す値。無ければNone),
             "candidates": [{"id":..., "label":...}, ...]}
    """
    grade_band = _grade_band_of(base_grade)
    resolved_track = None

    if grade_band == "高校生" and subject_text == "数学":
        # 学年+文理から自動判定する。高3は理系/文系で数3か数2BCかが変わる
        if base_grade == 10:      # 高1
            target_name = "数1A"
        elif base_grade == 11:    # 高2
            target_name = "数2BC"
        elif base_grade == 12:    # 高3
            if track == "理系":
                target_name = "数3"
            elif track == "文系":
                target_name = "数2BC"
            else:
                target_name = None  # 文理不明 → 確定できない
        else:
            target_name = None

        if target_name:
            row = conn.execute(
                "SELECT subject_id FROM SUBJECTS WHERE grade_band = '高校生' AND subject_name = ?",
                (target_name,),
            ).fetchone()
            candidates = [{"id": row[0], "label": target_name}] if row else []
        else:
            # 文理不明で確定できない場合は、数2BC/数3を選択肢として提示する
            rows = conn.execute(
                "SELECT subject_id, subject_name FROM SUBJECTS WHERE grade_band = '高校生' "
                "AND subject_name IN ('数2BC', '数3')"
            ).fetchall()
            candidates = [{"id": sid, "label": name} for sid, name in rows]

    elif grade_band == "高校生" and subject_text == "教科フォロー":
        if track in ("文系", "理系"):
            row = conn.execute(
                "SELECT subject_id FROM SUBJECTS WHERE grade_band = '高校生' AND subject_group = '教科フォロー' AND track = ?",
                (track,),
            ).fetchone()
            candidates = [{"id": row[0], "label": f"教科フォロー({track})"}] if row else []
        else:
            rows = conn.execute(
                "SELECT subject_id, track FROM SUBJECTS WHERE grade_band = '高校生' AND subject_group = '教科フォロー'"
            ).fetchall()
            candidates = [{"id": sid, "label": f"教科フォロー({t})", "track": t} for sid, t in rows]

    else:
        # それ以外は、学年帯+科目名の直接一致を試みる(中学生の数学/理科など、Excel表記=SUBJECTS表記が一致するケース)
        rows = conn.execute(
            "SELECT subject_id, subject_name, track FROM SUBJECTS WHERE grade_band = ? AND subject_name = ?",
            (grade_band, subject_text),
        ).fetchall()
        candidates = [
            {"id": sid, "label": f"{name}（{t}）" if t else name}
            for sid, name, t in rows
        ]

    if len(candidates) == 1:
        status_result = "matched"
    elif len(candidates) > 1:
        status_result = "ambiguous"
    else:
        status_result = "not_found"

    return {
        "excel_text": subject_text, "status": status_result,
        "resolved_track": resolved_track, "candidates": candidates,
    }


def save_student_track(conn, student_id: int, track: str) -> None:
    """教科フォローの文理確認結果を、生徒の属性として書き戻す(次回以降は自動判定できるようにするため)。"""
    if track not in ("文系", "理系"):
        raise ValueError(f"不正なtrackです: {track}")
    conn.execute("UPDATE STUDENTS SET track = ? WHERE student_id = ?", (track, student_id))
    conn.commit()
