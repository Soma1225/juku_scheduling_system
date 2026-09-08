# -*- coding: utf-8 -*-
"""page_students.py: 生徒(STUDENTS)の新規登録"""

import datetime
import html

from db import format_grade_label, get_conn, get_current_academic_fiscal_year, get_current_grade

GENDERS = ["", "男", "女", "その他"]


# ---------------------------------------------------------
# DB操作(このページ専用のロジック)
# ---------------------------------------------------------

def insert_student(conn, last_name, first_name, last_name_kana, first_name_kana,
                    enrollment_year, base_grade, enrollment_status, external_student_id=None,
                    gender=None, junior_high_school=None, high_school=None) -> int:
    if not last_name.strip() or not first_name.strip():
        raise ValueError("姓・名を入力してください")
    if not last_name_kana.strip() or not first_name_kana.strip():
        raise ValueError("ふりがな(姓・名)を入力してください")
    if enrollment_status not in ("在籍", "休会", "卒業", "退会"):
        raise ValueError(f"不正な在籍ステータスです: {enrollment_status}")
    if gender and gender not in ("男", "女", "その他"):
        raise ValueError(f"不正な性別です: {gender}")

    cur = conn.execute(
        """INSERT INTO STUDENTS
           (external_student_id, last_name, first_name, last_name_kana, first_name_kana,
            enrollment_year, base_grade, enrollment_status, gender, junior_high_school, high_school)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (external_student_id or None, last_name.strip(), first_name.strip(),
         last_name_kana.strip(), first_name_kana.strip(), enrollment_year, base_grade, enrollment_status,
         gender or None, (junior_high_school or "").strip() or None, (high_school or "").strip() or None),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------
# 画面(GET)
# ---------------------------------------------------------

def _grade_options() -> str:
    labels = [
        (1, "小1"), (2, "小2"), (3, "小3"), (4, "小4"), (5, "小5"), (6, "小6"),
        (7, "中1"), (8, "中2"), (9, "中3"), (10, "高1"), (11, "高2"), (12, "高3"),
    ]
    return "".join(f'<option value="{v}">{label}</option>' for v, label in labels)


def _gender_options() -> str:
    labels = {"": "未回答", "男": "男", "女": "女", "その他": "その他"}
    return "".join(f'<option value="{g}">{labels[g]}</option>' for g in GENDERS)


def render(qs: dict, message_html: str = "") -> str:
    current_fy = get_current_academic_fiscal_year()
    today = datetime.date.today().isoformat()
    conn = get_conn()
    students = conn.execute(
        """SELECT s.student_id, s.last_name, s.first_name,
                  s.enrollment_year, s.base_grade, s.enrollment_status,
                  s.external_student_id,
                  (SELECT COUNT(DISTINCT regular.subject_id)
                   FROM REGULAR_COURSE_ENROLLMENTS regular
                   WHERE regular.student_id = s.student_id
                     AND regular.effective_start_date <= ?
                     AND (regular.effective_end_date IS NULL OR regular.effective_end_date > ?))
                    AS regular_subject_count
           FROM STUDENTS s
           ORDER BY s.last_name_kana, s.first_name_kana""",
        (today, today),
    ).fetchall()
    conn.close()

    student_rows = ""
    for student_id, last_name, first_name, enrollment_year, base_grade, status, external_id, subject_count in students:
        grade = format_grade_label(get_current_grade(enrollment_year, base_grade))
        summary_html = (
            f"<strong>{html.escape(last_name)} {html.escape(first_name)}</strong><br>"
            f"学年: {html.escape(grade)}<br>ステータス: {html.escape(status)}<br>"
            f"現在の通常授業: {subject_count}科目"
        )
        student_rows += f"""
        <tr>
          <td><span class="person-quick-view" tabindex="0" role="button"
                    data-detail-url="/student-detail?student_id={student_id}"
                    data-summary-html="{html.escape(summary_html, quote=True)}"
                    title="シングルクリックで概要、ダブルクリックで詳細">{html.escape(last_name)} {html.escape(first_name)}</span></td>
          <td>{html.escape(grade)}</td><td>{html.escape(status)}</td><td>{html.escape(external_id or '-')}</td>
        </tr>
        """

    return f"""
    <h1>生徒 新規登録</h1>
    {message_html}
    <form method="POST" action="/students">
      <div class="form-2col">
        <div>
          <label>姓 <span class="req">*</span></label>
          <input type="text" id="last_name" name="last_name" required placeholder="例: 山田">
        </div>
        <div>
          <label>名 <span class="req">*</span></label>
          <input type="text" id="first_name" name="first_name" required placeholder="例: 太郎">
        </div>
        <div>
          <label>姓(ふりがな) <span class="req">*</span></label>
          <input type="text" id="last_name_kana" name="last_name_kana" required placeholder="例: やまだ">
        </div>
        <div>
          <label>名(ふりがな) <span class="req">*</span></label>
          <input type="text" id="first_name_kana" name="first_name_kana" required placeholder="例: たろう">
        </div>
        <div>
          <label>性別(任意)</label>
          <select name="gender">{_gender_options()}</select>
        </div>
        <div>
          <label>入塾時点の学年 <span class="req">*</span></label>
          <select name="base_grade" required>
            <option value="">選択してください</option>
            {_grade_options()}
          </select>
        </div>
        <div>
          <label>入塾年度 <span class="req">*</span></label>
          <input type="number" name="enrollment_year" value="{current_fy}" required>
        </div>
        <div>
          <label>在籍ステータス <span class="req">*</span></label>
          <select name="enrollment_status" required>
            <option value="在籍" selected>在籍</option><option value="休会">休会</option>
            <option value="卒業">卒業</option><option value="退会">退会</option>
          </select>
        </div>
        <div>
          <label>所属中学(任意)</label>
          <input type="text" name="junior_high_school" placeholder="例: 〇〇中学校">
        </div>
        <div>
          <label>所属高校(任意)</label>
          <input type="text" name="high_school" placeholder="例: 〇〇高校">
        </div>
        <div style="grid-column:1 / -1;">
          <label>外部生徒ID(任意)</label>
          <input type="text" name="external_student_id">
        </div>
      </div>
      <button type="submit">登録する</button>
    </form>
    <style>
      .form-2col {{ display:grid; grid-template-columns:1fr 1fr; gap:4px 20px; }}
      .form-2col label {{ margin-top:14px; }}
      .form-2col input, .form-2col select {{ margin-top:4px; }}
    </style>
    <script>
      // 漢字入力欄で変換前のひらがな読みを検知し、ふりがな欄が空なら自動入力する
      function setupFuriganaAutofill(kanjiId, kanaId) {{
        var kanjiInput = document.getElementById(kanjiId);
        var kanaInput = document.getElementById(kanaId);
        var lastReading = '';
        kanjiInput.addEventListener('compositionupdate', function() {{
          var val = kanjiInput.value;
          if (/^[\\u3040-\\u309F\\u30FC]*$/.test(val)) {{
            lastReading = val;  // 変換確定前の、ひらがなだけの状態を覚えておく
          }}
        }});
        kanjiInput.addEventListener('compositionend', function() {{
          if (kanaInput.value.trim() === '' && lastReading) {{
            kanaInput.value = lastReading;
          }}
        }});
      }}
      setupFuriganaAutofill('last_name', 'last_name_kana');
      setupFuriganaAutofill('first_name', 'first_name_kana');
    </script>
    <h1 style="font-size:14px;margin-top:28px;">登録済みの生徒 ({len(students)}名)</h1>
    <div class="hint">名前をシングルクリックすると概要、ダブルクリックすると生徒詳細を表示します</div>
    <table>
      <tr><th>氏名</th><th>学年</th><th>ステータス</th><th>外部生徒ID</th></tr>
      {student_rows}
    </table>
    """


# ---------------------------------------------------------
# 送信処理(POST)
# ---------------------------------------------------------

def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    new_id = insert_student(
        conn, get("last_name"), get("first_name"), get("last_name_kana"), get("first_name_kana"),
        int(get("enrollment_year") or 0), int(get("base_grade") or 0), get("enrollment_status"),
        get("external_student_id") or None,
        get("gender") or None, get("junior_high_school") or None, get("high_school") or None,
    )
    message_html = (
        f'<div class="msg success">登録しました → student_id={new_id} / '
        f'{get("last_name")} {get("first_name")}</div>'
    )
    return message_html, {}
