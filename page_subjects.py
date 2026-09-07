# -*- coding: utf-8 -*-
"""
page_subjects.py

科目マスタ(SUBJECTS)の登録・編集・削除ページ。
既知の科目は db.py 側で起動時に自動投入されるため、
このページは普段は「一覧を見る」「必要なら編集/削除する」だけで、
新しい科目が本当に必要なときだけ追加登録する形になっている。

削除は、既に他のテーブル(INSTRUCTOR_SUBJECTS, 受講科目回数登録など)から
参照されている科目には実行できない(外部キー制約により自動的に守られる)。
"""

import sqlite3
from db import get_conn

COURSE_CATEGORIES = ["個別指導", "戦略指導"]
GRADE_BANDS = ["小学生低学年", "小学生高学年", "中学生", "高校生"]
TRACKS = ["", "受験", "非受験", "文系", "理系"]


def _check_duplicate(conn, course_category, grade_band, track, subject_group, subject_name, exclude_id=None):
    query = """SELECT subject_id FROM SUBJECTS
               WHERE course_category = ? AND grade_band = ? AND IFNULL(track, '') = IFNULL(?, '')
               AND subject_group = ? AND subject_name = ?"""
    params = [course_category, grade_band, track, subject_group, subject_name]
    if exclude_id is not None:
        query += " AND subject_id != ?"
        params.append(exclude_id)
    return conn.execute(query, params).fetchone()


def insert_subject(conn, course_category, grade_band, track, subject_group, subject_name) -> int:
    if course_category not in COURSE_CATEGORIES:
        raise ValueError(f"不正なcourse_categoryです: {course_category}")
    if grade_band not in GRADE_BANDS:
        raise ValueError(f"不正なgrade_bandです: {grade_band}")
    if track not in (None, "", "受験", "非受験", "文系", "理系"):
        raise ValueError(f"不正なtrackです: {track}")
    if not subject_group.strip() or not subject_name.strip():
        raise ValueError("subject_group と subject_name を入力してください")

    subject_group = subject_group.strip()
    subject_name = subject_name.strip()

    if _check_duplicate(conn, course_category, grade_band, track, subject_group, subject_name):
        raise ValueError("同じ組み合わせが既に登録されています")

    cur = conn.execute(
        "INSERT INTO SUBJECTS (course_category, grade_band, track, subject_group, subject_name) VALUES (?, ?, ?, ?, ?)",
        (course_category, grade_band, track or None, subject_group, subject_name),
    )
    conn.commit()
    return cur.lastrowid


def update_subject(conn, subject_id, course_category, grade_band, track, subject_group, subject_name) -> None:
    if course_category not in COURSE_CATEGORIES:
        raise ValueError(f"不正なcourse_categoryです: {course_category}")
    if grade_band not in GRADE_BANDS:
        raise ValueError(f"不正なgrade_bandです: {grade_band}")
    if track not in (None, "", "受験", "非受験", "文系", "理系"):
        raise ValueError(f"不正なtrackです: {track}")
    if not subject_group.strip() or not subject_name.strip():
        raise ValueError("subject_group と subject_name を入力してください")

    subject_group = subject_group.strip()
    subject_name = subject_name.strip()

    if _check_duplicate(conn, course_category, grade_band, track, subject_group, subject_name, exclude_id=subject_id):
        raise ValueError("同じ組み合わせが既に他の科目として登録されています")

    conn.execute(
        """UPDATE SUBJECTS SET course_category=?, grade_band=?, track=?, subject_group=?, subject_name=?
           WHERE subject_id=?""",
        (course_category, grade_band, track or None, subject_group, subject_name, subject_id),
    )
    conn.commit()


def delete_subject(conn, subject_id) -> None:
    try:
        conn.execute("DELETE FROM SUBJECTS WHERE subject_id = ?", (subject_id,))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise ValueError(
            "この科目は既に担当科目・受講科目回数登録などで使われているため削除できません。"
            "先にそれらの登録を削除するか、削除ではなく編集で対応してください"
        )


def render(qs: dict, message_html: str = "") -> str:
    edit_id = qs.get("edit_id", [""])[0]
    conn = get_conn()
    rows = conn.execute(
        "SELECT subject_id, course_category, grade_band, track, subject_group, subject_name FROM SUBJECTS "
        "ORDER BY course_category, grade_band, subject_group, subject_name"
    ).fetchall()
    existing_groups = conn.execute(
        "SELECT DISTINCT subject_group FROM SUBJECTS ORDER BY subject_group"
    ).fetchall()

    edit_row = None
    if edit_id:
        edit_row = conn.execute(
            "SELECT subject_id, course_category, grade_band, track, subject_group, subject_name "
            "FROM SUBJECTS WHERE subject_id = ?", (edit_id,)
        ).fetchone()
    conn.close()

    group_options_html = "".join(f'<option value="{g[0]}">{g[0]}</option>' for g in existing_groups)

    def cat_options(selected):
        return "".join(f'<option value="{c}"{" selected" if c == selected else ""}>{c}</option>' for c in COURSE_CATEGORIES)

    def grade_options(selected):
        return "".join(f'<option value="{g}"{" selected" if g == selected else ""}>{g}</option>' for g in GRADE_BANDS)

    def track_options(selected):
        labels = {"": "該当なし", "受験": "受験", "非受験": "非受験", "文系": "文系", "理系": "理系"}
        return "".join(
            f'<option value="{t}"{" selected" if (t or "") == (selected or "") else ""}>{labels[t]}</option>'
            for t in TRACKS
        )

    rows_html = ""
    for r in rows:
        sid, cc, gb, tr, sg, sn = r
        rows_html += f"""
        <tr>
          <td>{cc}</td><td>{gb}</td><td>{tr or '-'}</td><td>{sg}</td><td>{sn}</td>
          <td>
            <a href="/subjects?edit_id={sid}" style="font-size:12px;color:#1F4E5F;">編集</a>
            &nbsp;
            <form class="row-form" method="POST" action="/subjects" style="display:inline;"
                  onsubmit="return confirm('本当に削除しますか？');">
              <input type="hidden" name="action" value="delete">
              <input type="hidden" name="subject_id" value="{sid}">
              <button class="btn-remove" type="submit" style="font-size:12px;">削除</button>
            </form>
          </td>
        </tr>
        """

    if edit_row:
        sid, cc, gb, tr, sg, sn = edit_row
        add_or_edit_form = f"""
        <div class="hint" style="color:#534AB7;">「{sg}/{sn}」を編集しています</div>
        <form method="POST" action="/subjects">
          <input type="hidden" name="action" value="update">
          <input type="hidden" name="subject_id" value="{sid}">
          <label>指導形態 <span class="req">*</span></label>
          <select name="course_category" required>{cat_options(cc)}</select>
          <label>学年帯 <span class="req">*</span></label>
          <select name="grade_band" id="edit_grade_band_select" required onchange="updateEditTrackOptions(this.value)">{grade_options(gb)}</select>
          <label>区分(学年帯に応じて選択肢が変わります)</label>
          <select name="track" id="edit_track_select">{track_options(tr)}</select>
          <script>
            function updateEditTrackOptions(gradeBand) {{
              var sel = document.getElementById('edit_track_select');
              var allowed = {{
                '小学生高学年': ['', '受験', '非受験'],
                '高校生': ['', '文系', '理系']
              }};
              var visible = allowed[gradeBand] || [''];
              for (var i = 0; i < sel.options.length; i++) {{
                var v = sel.options[i].value;
                sel.options[i].hidden = (visible.indexOf(v) === -1);
              }}
            }}
            updateEditTrackOptions(document.getElementById('edit_grade_band_select').value);
          </script>
          <label>上位グループ <span class="req">*</span></label>
          <input type="text" name="subject_group" required value="{sg}">
          <label>具体科目名 <span class="req">*</span></label>
          <input type="text" name="subject_name" required value="{sn}">
          <button type="submit">この内容で更新する</button>
        </form>
        <a href="/subjects" style="font-size:12px;color:#888;">編集をやめて新規登録に戻る</a>
        """
    else:
        add_or_edit_form = f"""
        <form method="POST" action="/subjects" onsubmit="
          var sel = document.getElementById('group_select');
          var newInput = document.getElementById('new_group_input');
          var mirror = document.getElementsByName('subject_group')[0];
          var value = (sel.value === '__new__') ? newInput.value.trim() : sel.value;
          if (!value) {{ alert('上位グループを選択(または新しいグループ名を入力)してください'); return false; }}
          mirror.innerHTML = '';
          var opt = document.createElement('option');
          opt.value = value;
          opt.selected = true;
          mirror.appendChild(opt);
          return true;
        ">
          <input type="hidden" name="action" value="add">
          <label>指導形態 <span class="req">*</span></label>
          <select name="course_category" required>{cat_options("個別指導")}</select>
          <label>学年帯 <span class="req">*</span></label>
          <select name="grade_band" id="grade_band_select" required onchange="updateTrackOptions(this.value)">{grade_options("小学生低学年")}</select>
          <label>区分(学年帯に応じて選択肢が変わります)</label>
          <select name="track" id="track_select">{track_options("")}</select>
          <script>
            function updateTrackOptions(gradeBand) {{
              var sel = document.getElementById('track_select');
              var allowed = {{
                '小学生高学年': ['', '受験', '非受験'],
                '高校生': ['', '文系', '理系']
              }};
              var visible = allowed[gradeBand] || [''];
              for (var i = 0; i < sel.options.length; i++) {{
                var v = sel.options[i].value;
                sel.options[i].hidden = (visible.indexOf(v) === -1);
              }}
              if (visible.indexOf(sel.value) === -1) {{ sel.value = ''; }}
            }}
            updateTrackOptions(document.getElementById('grade_band_select').value);
          </script>

          <label>上位グループ <span class="req">*</span></label>
          <select id="group_select" onchange="
            var box = document.getElementById('new_group_box');
            box.style.display = (this.value === '__new__') ? 'block' : 'none';
          ">
            <option value="">選択してください</option>
            {group_options_html}
            <option value="__new__">＋新しいグループを追加</option>
          </select>
          <select name="subject_group" style="display:none;" id="hidden_group_mirror"></select>
          <div id="new_group_box" style="display:none; margin-top:8px;">
            <input type="text" id="new_group_input" placeholder="新しいグループ名(例: プログラミング)">
          </div>

          <label>具体科目名 <span class="req">*</span></label>
          <input type="text" name="subject_name" required placeholder="例: 数1A">
          <button type="submit">登録する</button>
        </form>
        """

    return f"""
    <h1>科目マスタ登録</h1>
    <div class="hint">既知の科目はあらかじめ登録済みです。編集・削除は一覧のボタンから行えます</div>
    {message_html}
    {add_or_edit_form}
    <table>
      <tr><th>指導形態</th><th>学年帯</th><th>区分</th><th>グループ</th><th>科目名</th><th></th></tr>
      {rows_html}
    </table>
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    action = get("action", "add")

    if action == "add":
        new_id = insert_subject(conn, get("course_category"), get("grade_band"), get("track") or None,
                                 get("subject_group"), get("subject_name"))
        message_html = f'<div class="msg success">登録しました → subject_id={new_id}</div>'
        return message_html, {}

    elif action == "update":
        subject_id = int(get("subject_id"))
        update_subject(conn, subject_id, get("course_category"), get("grade_band"), get("track") or None,
                        get("subject_group"), get("subject_name"))
        message_html = '<div class="msg success">更新しました</div>'
        return message_html, {}

    elif action == "delete":
        delete_subject(conn, int(get("subject_id")))
        message_html = '<div class="msg success">削除しました</div>'
        return message_html, {}

    else:
        raise ValueError(f"不明な action です: {action}")
