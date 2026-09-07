# -*- coding: utf-8 -*-
"""
page_excel_import.py

通常授業Excelの取り込み画面(本実装)。3段階のウィザード形式:
  1. アップロード
  2. 講師・生徒の確認(曖昧な人だけ選択、未登録は新規登録)
  3. 科目の確認(生徒の学年・文理から自動判定できない組み合わせだけ確認)
  4. 確定してREGULAR_COURSE_ENROLLMENTSへ一括登録

このツールは職員だけが使う共有PC上のものなので、同時に複数人が別々の
取り込み作業をすることは想定していない。そのため、進行状況はプロセス内の
メモリ(_STATE)に保持するだけで、DBやセッション管理は行わない。
"""

import datetime
import tempfile
import os
import re
from db import get_conn, format_grade_label
from excel_import import (
    parse_regular_timetable, build_match_results, resolve_subject, save_student_track,
    split_grade_and_name, grade_text_to_base_grade,
)
from page_instructors import insert_instructor
from page_students import insert_student
from page_regular_enrollments import insert_regular_enrollment

_STATE = {
    "phase": "upload",       # upload -> confirm_people -> confirm_subjects -> done
    "records": [],            # parse_regular_timetable()の生データ
    "match": None,             # build_match_results()の結果
    "instructor_map": {},      # excel_text -> instructor_id
    "student_map": {},         # excel_text -> student_id
    "subject_pending": [],     # 確認が必要な(subject_text, student_id)のリスト
    "subject_map": {},         # (subject_text, student_id) -> subject_id
}


def _reset_state():
    _STATE["phase"] = "upload"
    _STATE["records"] = []
    _STATE["match"] = None
    _STATE["instructor_map"] = {}
    _STATE["student_map"] = {}
    _STATE["subject_pending"] = []
    _STATE["subject_map"] = {}


def _default_enrollment_year() -> int:
    today = datetime.date.today()
    return today.year if today.month >= 3 else today.year - 1


def _split_name(full_name: str) -> tuple[str, str]:
    """「山田 太郎」のような表記を(姓, 名)に分割する。空白が無ければ全体を姓として扱う。"""
    parts = re.split(r"[\s\u3000]+", full_name.strip(), maxsplit=1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return full_name.strip(), "（名不明）"


# ---------------------------------------------------------
# render: 現在のphaseに応じて画面を出し分ける
# ---------------------------------------------------------

def render(qs: dict, message_html: str = "") -> str:
    if qs.get("action", [""])[0] == "reset":
        _reset_state()

    phase = _STATE["phase"]
    if phase == "upload":
        return _render_upload(message_html)
    elif phase == "confirm_people":
        return _render_confirm_people(message_html)
    elif phase == "confirm_subjects":
        return _render_confirm_subjects(message_html)
    else:
        return _render_done(message_html)


def _render_upload(message_html: str) -> str:
    return f"""
    <h1>通常授業 Excel取り込み</h1>
    <div class="hint">「時間割一覧」シートを含むExcelファイルをアップロードしてください</div>
    {message_html}
    <form method="POST" action="/excel-import" enctype="multipart/form-data">
      <input type="hidden" name="action" value="upload">
      <label>Excelファイル <span class="req">*</span></label>
      <input type="file" name="excel_file" accept=".xlsx" required>
      <button type="submit">読み込む</button>
    </form>
    """


def _render_confirm_people(message_html: str) -> str:
    instructors = _STATE["match"]["instructors"]
    students = _STATE["match"]["students"]

    def status_meta(status):
        return {
            "matched": ("✅", "自動一致", "#0F6E56"),
            "ambiguous": ("⚠️", "要選択（複数候補）", "#B8860B"),
            "not_found": ("❌", "未登録（新規登録）", "#993C1D"),
        }[status]

    order = {"ambiguous": 0, "not_found": 1, "matched": 2}

    instructor_rows = ""
    for i, item in sorted(enumerate(instructors), key=lambda p: order[p[1]["status"]]):
        icon, label, color = status_meta(item["status"])
        if item["status"] == "matched":
            action = f'<span>{item["candidates"][0]["label"]}</span>'
        elif item["status"] == "ambiguous":
            opts = "".join(f'<option value="{c["id"]}">{c["label"]}</option>' for c in item["candidates"])
            action = f'<select name="i_choice_{i}" required><option value="">選択してください</option>{opts}</select>'
        else:
            action = f"""
            <div style="display:flex;gap:6px;">
              <input type="text" name="i_new_last_kana_{i}" placeholder="姓(ふりがな)" style="width:100px;" required>
              <input type="text" name="i_new_first_kana_{i}" placeholder="名(ふりがな)" style="width:100px;" required>
            </div>
            """
        instructor_rows += (
            f'<tr><td>{item["excel_text"]}</td>'
            f'<td><span style="color:{color};font-weight:bold;">{icon} {label}</span></td>'
            f'<td>{action}</td></tr>'
        )

    student_rows = ""
    for i, item in sorted(enumerate(students), key=lambda p: order[p[1]["status"]]):
        icon, label, color = status_meta(item["status"])
        if item["status"] == "matched":
            action = f'<span>{item["candidates"][0]["label"]}</span>'
        elif item["status"] == "ambiguous":
            opts = "".join(f'<option value="{c["id"]}">{c["label"]}</option>' for c in item["candidates"])
            action = f'<select name="s_choice_{i}" required><option value="">選択してください</option>{opts}</select>'
        else:
            action = f"""
            <div style="display:flex;gap:6px;">
              <input type="text" name="s_new_last_kana_{i}" placeholder="姓(ふりがな)" style="width:100px;" required>
              <input type="text" name="s_new_first_kana_{i}" placeholder="名(ふりがな)" style="width:100px;" required>
            </div>
            """
        student_rows += (
            f'<tr><td>{item["excel_text"]}</td>'
            f'<td><span style="color:{color};font-weight:bold;">{icon} {label}</span></td>'
            f'<td>{action}</td></tr>'
        )

    return f"""
    <h1>Excel取り込み: 講師・生徒の確認</h1>
    <div class="hint">確認が必要な行を上に表示しています</div>
    {message_html}
    <form method="POST" action="/excel-import">
      <input type="hidden" name="action" value="confirm_people">
      <h1 style="font-size:15px;color:#534AB7;margin-top:20px;">講師</h1>
      <table><tr><th>Excel上の表記</th><th>状態</th><th>対応</th></tr>{instructor_rows}</table>
      <h1 style="font-size:15px;color:#534AB7;margin-top:28px;">生徒</h1>
      <table><tr><th>Excel上の表記</th><th>状態</th><th>対応</th></tr>{student_rows}</table>
      <button type="submit" style="margin-top:20px;">確定して科目の確認へ進む</button>
    </form>
    <a href="/excel-import?action=reset" style="font-size:12px;color:#888;">最初からやり直す</a>
    """


def _render_confirm_subjects(message_html: str) -> str:
    pending = _STATE["subject_pending"]
    rows = ""
    for i, item in enumerate(pending):
        opts = "".join(f'<option value="{c["id"]}">{c["label"]}</option>' for c in item["candidates"])
        rows += f"""
        <tr>
          <td>{item['subject_text']}</td>
          <td>{item['student_label']}</td>
          <td><select name="subj_choice_{i}" required><option value="">選択してください</option>{opts}</select></td>
        </tr>
        """
    table_html = (
        f"<table><tr><th>Excel上の科目</th><th>対象生徒</th><th>選択</th></tr>{rows}</table>"
        if pending else '<div class="hint">確認が必要な科目はありません</div>'
    )

    return f"""
    <h1>Excel取り込み: 科目の確認</h1>
    <div class="hint">学年・文理だけでは自動判定できなかった組み合わせです。選んだ内容は、対象生徒の文理として記憶されます</div>
    {message_html}
    <form method="POST" action="/excel-import">
      <input type="hidden" name="action" value="confirm_subjects">
      {table_html}
      <label>この時間割の適用開始日 <span class="req">*</span></label>
      <input type="date" name="effective_start_date" required>
      <button type="submit" style="margin-top:20px;">この内容で一括登録する</button>
    </form>
    <a href="/excel-import?action=reset" style="font-size:12px;color:#888;">最初からやり直す</a>
    """


def _render_done(message_html: str) -> str:
    return f"""
    <h1>Excel取り込み: 完了</h1>
    {message_html}
    <a href="/excel-import?action=reset">別のファイルを取り込む</a>
    """


# ---------------------------------------------------------
# handle_post: phaseごとの処理
# ---------------------------------------------------------

def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    action = get("action")

    if action == "upload":
        return _handle_upload(fields, conn)
    elif action == "confirm_people":
        return _handle_confirm_people(fields, conn)
    elif action == "confirm_subjects":
        return _handle_confirm_subjects(fields, conn)
    else:
        raise ValueError(f"不明な action です: {action}")


def _handle_upload(fields: dict, conn) -> tuple[str, dict]:
    files = fields.get("_files", {})
    if "excel_file" not in files:
        raise ValueError("Excelファイルを選択してください")

    file_info = files["excel_file"]
    if not file_info["filename"].lower().endswith(".xlsx"):
        raise ValueError("拡張子が.xlsxのファイルを選択してください")

    # 一時ファイルとして保存してから読み込む(openpyxlはファイルパスまたはファイルオブジェクトを要求するため)
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(file_info["content"])
        tmp_path = tmp.name

    try:
        records = parse_regular_timetable(tmp_path)
    except KeyError:
        raise ValueError("「時間割一覧」というシートが見つかりません。想定した形式のファイルか確認してください")
    finally:
        os.unlink(tmp_path)

    if not records:
        raise ValueError("時間割データが1件も読み取れませんでした")

    match = build_match_results(conn, records)

    _STATE["records"] = records
    _STATE["match"] = match
    _STATE["phase"] = "confirm_people"

    message_html = (
        f'<div class="msg success">読み込みました({len(records)}件のレコード、'
        f'講師{len(match["instructors"])}名、生徒{len(match["students"])}名)</div>'
    )
    return message_html, {}


def _handle_confirm_people(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    instructors = _STATE["match"]["instructors"]
    students = _STATE["match"]["students"]

    instructor_map: dict[str, int] = {}
    for i, item in enumerate(instructors):
        if item["status"] == "matched":
            instructor_map[item["excel_text"]] = item["candidates"][0]["id"]
        elif item["status"] == "ambiguous":
            choice = get(f"i_choice_{i}")
            if not choice:
                raise ValueError(f"講師「{item['excel_text']}」を選択してください")
            instructor_map[item["excel_text"]] = int(choice)
        else:  # not_found
            lk = get(f"i_new_last_kana_{i}")
            fk = get(f"i_new_first_kana_{i}")
            if not lk or not fk:
                raise ValueError(f"講師「{item['excel_text']}」のふりがなを入力してください")
            last_name, first_name = _split_name(item["excel_text"])
            new_id = insert_instructor(conn, last_name, first_name, lk, fk)
            instructor_map[item["excel_text"]] = new_id

    student_map: dict[str, int] = {}
    for i, item in enumerate(students):
        if item["status"] == "matched":
            student_map[item["excel_text"]] = item["candidates"][0]["id"]
        elif item["status"] == "ambiguous":
            choice = get(f"s_choice_{i}")
            if not choice:
                raise ValueError(f"生徒「{item['excel_text']}」を選択してください")
            student_map[item["excel_text"]] = int(choice)
        else:  # not_found
            lk = get(f"s_new_last_kana_{i}")
            fk = get(f"s_new_first_kana_{i}")
            if not lk or not fk:
                raise ValueError(f"生徒「{item['excel_text']}」のふりがなを入力してください")
            grade_text, name = split_grade_and_name(item["excel_text"])
            base_grade = grade_text_to_base_grade(grade_text)
            if base_grade is None:
                raise ValueError(f"生徒「{item['excel_text']}」の学年を読み取れませんでした")
            last_name, first_name = _split_name(name)
            new_id = insert_student(
                conn, last_name, first_name, lk, fk,
                _default_enrollment_year(), base_grade, "在籍",
            )
            student_map[item["excel_text"]] = new_id

    _STATE["instructor_map"] = instructor_map
    _STATE["student_map"] = student_map

    # --- 科目の解決: 生徒が確定したので、ここで初めて学年・文理から科目を判定できる ---
    pending = []
    subject_map: dict[tuple[str, int], int] = {}
    seen_pairs: set[tuple[str, int]] = set()

    for record in _STATE["records"]:
        if not record["student_text"]:
            continue  # 生徒名が無い行(教科フォローの未確定枠など)は今回はスキップ
        student_id = student_map.get(record["student_text"])
        if student_id is None:
            continue
        key = (record["subject_text"], student_id)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)

        row = conn.execute("SELECT base_grade, track FROM STUDENTS WHERE student_id = ?", (student_id,)).fetchone()
        base_grade, track = row
        result = resolve_subject(conn, record["subject_text"], base_grade, track)

        if result["status"] == "matched":
            subject_map[key] = result["candidates"][0]["id"]
        elif result["status"] == "ambiguous":
            name_row = conn.execute(
                "SELECT last_name, first_name FROM STUDENTS WHERE student_id = ?", (student_id,)
            ).fetchone()
            pending.append({
                "subject_text": record["subject_text"], "student_id": student_id,
                "student_label": f"{name_row[0]}{name_row[1]}（{format_grade_label(base_grade)}）",
                "candidates": result["candidates"],
            })
        # not_found の場合は登録をスキップする(このレコードは取り込まれない)

    _STATE["subject_pending"] = pending
    _STATE["subject_map"] = subject_map
    _STATE["phase"] = "confirm_subjects"

    message_html = '<div class="msg success">講師・生徒を確定しました</div>'
    return message_html, {}


def _handle_confirm_subjects(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    pending = _STATE["subject_pending"]
    subject_map = dict(_STATE["subject_map"])

    for i, item in enumerate(pending):
        choice = get(f"subj_choice_{i}")
        if not choice:
            raise ValueError(f"「{item['subject_text']}」（{item['student_label']}）の科目を選択してください")
        subject_id = int(choice)
        subject_map[(item["subject_text"], item["student_id"])] = subject_id

        # 選んだ内容から文理が分かる場合は、生徒の属性として記憶しておく
        chosen_candidate = next((c for c in item["candidates"] if c["id"] == subject_id), None)
        if chosen_candidate and "track" in chosen_candidate:
            save_student_track(conn, item["student_id"], chosen_candidate["track"])

    effective_start_date = get("effective_start_date")
    if not effective_start_date:
        raise ValueError("適用開始日を入力してください")

    instructor_map = _STATE["instructor_map"]
    student_map = _STATE["student_map"]

    n_created = 0
    n_skipped = 0
    for record in _STATE["records"]:
        if not record["student_text"] or not record["instructor_text"]:
            n_skipped += 1
            continue
        student_id = student_map.get(record["student_text"])
        instructor_id = instructor_map.get(record["instructor_text"])
        subject_id = subject_map.get((record["subject_text"], student_id))
        if student_id is None or instructor_id is None or subject_id is None:
            n_skipped += 1
            continue

        insert_regular_enrollment(
            conn, student_id, subject_id, instructor_id,
            record["day_of_week"], record["period_number"], effective_start_date,
        )
        n_created += 1

    _STATE["phase"] = "done"
    message_html = (
        f'<div class="msg success">取り込みが完了しました(登録: {n_created}件、'
        f'スキップ: {n_skipped}件（生徒名や科目が未確定の枠など）)</div>'
    )
    return message_html, {}
