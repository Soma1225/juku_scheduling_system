# -*- coding: utf-8 -*-
"""page_students.py: 生徒(STUDENTS)の新規登録"""

# ---------------------------------------------------------
# DB操作(このページ専用のロジック)
# ---------------------------------------------------------

def insert_student(conn, last_name, first_name, last_name_kana, first_name_kana,
                    enrollment_year, base_grade, enrollment_status, external_student_id=None) -> int:
    if not last_name.strip() or not first_name.strip():
        raise ValueError("姓・名を入力してください")
    if not last_name_kana.strip() or not first_name_kana.strip():
        raise ValueError("ふりがな(姓・名)を入力してください")
    if enrollment_status not in ("在籍", "休会", "卒業", "退会"):
        raise ValueError(f"不正な在籍ステータスです: {enrollment_status}")
    cur = conn.execute(
        """INSERT INTO STUDENTS
           (external_student_id, last_name, first_name, last_name_kana, first_name_kana,
            enrollment_year, base_grade, enrollment_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (external_student_id or None, last_name.strip(), first_name.strip(),
         last_name_kana.strip(), first_name_kana.strip(), enrollment_year, base_grade, enrollment_status),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------
# 画面(GET)
# ---------------------------------------------------------

def render(qs: dict, message_html: str = "") -> str:
    return f"""
    <h1>生徒 新規登録</h1>
    {message_html}
    <form method="POST" action="/students">
      <label>姓 <span class="req">*</span></label>
      <input type="text" name="last_name" required placeholder="例: 山田">
      <label>名 <span class="req">*</span></label>
      <input type="text" name="first_name" required placeholder="例: 太郎">
      <label>姓(ふりがな) <span class="req">*</span></label>
      <input type="text" name="last_name_kana" required placeholder="例: やまだ">
      <label>名(ふりがな) <span class="req">*</span></label>
      <input type="text" name="first_name_kana" required placeholder="例: たろう">
      <label>入塾年度 <span class="req">*</span></label>
      <input type="number" name="enrollment_year" value="2026" required>
      <label>入塾時点の学年 <span class="req">*</span></label>
      <select name="base_grade" required>
        <option value="">選択してください</option>
        <option value="1">小1</option><option value="2">小2</option><option value="3">小3</option>
        <option value="4">小4</option><option value="5">小5</option><option value="6">小6</option>
        <option value="7">中1</option><option value="8">中2</option><option value="9">中3</option>
        <option value="10">高1</option><option value="11">高2</option><option value="12">高3</option>
      </select>
      <label>在籍ステータス <span class="req">*</span></label>
      <select name="enrollment_status" required>
        <option value="在籍" selected>在籍</option><option value="休会">休会</option>
        <option value="卒業">卒業</option><option value="退会">退会</option>
      </select>
      <label>外部生徒ID(任意)</label>
      <input type="text" name="external_student_id">
      <button type="submit">登録する</button>
    </form>
    """


# ---------------------------------------------------------
# 送信処理(POST)
# ---------------------------------------------------------

def handle_post(fields: dict, conn) -> tuple[str, dict]:
    """
    戻り値: (message_html, 再表示用のqs)
    このページは送信後に特定の状態を復元する必要がないので、qsは空でよい。
    """
    def get(key, default=""):
        return fields.get(key, [default])[0]

    new_id = insert_student(
        conn, get("last_name"), get("first_name"), get("last_name_kana"), get("first_name_kana"),
        int(get("enrollment_year") or 0), int(get("base_grade") or 0), get("enrollment_status"),
        get("external_student_id") or None,
    )
    message_html = (
        f'<div class="msg success">登録しました → student_id={new_id} / '
        f'{get("last_name")} {get("first_name")}</div>'
    )
    return message_html, {}
