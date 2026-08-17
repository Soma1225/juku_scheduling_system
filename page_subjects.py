# -*- coding: utf-8 -*-
"""page_subjects.py: 科目マスタ(SUBJECTS)の登録"""

from db import get_conn


def insert_subject(conn, course_category, grade_band, track, subject_group, subject_name) -> int:
    if course_category not in ("個別指導", "戦略指導"):
        raise ValueError(f"不正なcourse_categoryです: {course_category}")
    if grade_band not in ("小学生低学年", "小学生高学年", "中学生", "高校生"):
        raise ValueError(f"不正なgrade_bandです: {grade_band}")
    if track not in (None, "", "受験", "非受験"):
        raise ValueError(f"不正なtrackです: {track}")
    if not subject_group.strip() or not subject_name.strip():
        raise ValueError("subject_group と subject_name を入力してください")
    cur = conn.execute(
        "INSERT INTO SUBJECTS (course_category, grade_band, track, subject_group, subject_name) VALUES (?, ?, ?, ?, ?)",
        (course_category, grade_band, track or None, subject_group.strip(), subject_name.strip()),
    )
    conn.commit()
    return cur.lastrowid


def render(qs: dict, message_html: str = "") -> str:
    conn = get_conn()
    rows = conn.execute(
        "SELECT course_category, grade_band, track, subject_group, subject_name FROM SUBJECTS "
        "ORDER BY course_category, grade_band, subject_group, subject_name"
    ).fetchall()
    conn.close()
    rows_html = "".join(
        f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2] or '-'}</td><td>{r[3]}</td><td>{r[4]}</td></tr>" for r in rows
    )
    return f"""
    <h1>科目マスタ登録</h1>
    <div class="hint">既存の科目と重複していないか、下の一覧で確認してから登録してください</div>
    {message_html}
    <form method="POST" action="/subjects">
      <label>指導形態 <span class="req">*</span></label>
      <select name="course_category" required>
        <option value="個別指導">個別指導</option><option value="戦略指導">戦略指導</option>
      </select>
      <label>学年帯 <span class="req">*</span></label>
      <select name="grade_band" required>
        <option value="小学生低学年">小学生低学年</option><option value="小学生高学年">小学生高学年</option>
        <option value="中学生">中学生</option><option value="高校生">高校生</option>
      </select>
      <label>受験区分(小学生高学年のみ該当)</label>
      <select name="track">
        <option value="">該当なし</option><option value="受験">受験</option><option value="非受験">非受験</option>
      </select>
      <label>上位グループ <span class="req">*</span></label>
      <input type="text" name="subject_group" required placeholder="例: 数学">
      <label>具体科目名 <span class="req">*</span></label>
      <input type="text" name="subject_name" required placeholder="例: 数1A">
      <button type="submit">登録する</button>
    </form>
    <table>
      <tr><th>指導形態</th><th>学年帯</th><th>区分</th><th>グループ</th><th>科目名</th></tr>
      {rows_html}
    </table>
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    new_id = insert_subject(conn, get("course_category"), get("grade_band"), get("track") or None,
                             get("subject_group"), get("subject_name"))
    message_html = f'<div class="msg success">登録しました → subject_id={new_id}</div>'
    return message_html, {}
