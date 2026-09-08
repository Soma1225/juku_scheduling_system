# -*- coding: utf-8 -*-
"""生徒ごとの推奨講師・絶対NG講師を登録するページ。"""

import html
import sqlite3

from db import get_conn


def get_student_instructor_preferences(conn, student_id: int) -> tuple[list[int], list[int]]:
    """推奨講師（順位順）とNG講師のID一覧を返す。"""
    preferred = [
        row[0]
        for row in conn.execute(
            """SELECT instructor_id FROM STUDENT_INSTRUCTOR_PREFERENCES
               WHERE student_id = ? AND preference_type = 'PREFERRED'
               ORDER BY priority_rank""",
            (student_id,),
        ).fetchall()
    ]
    ng = [
        row[0]
        for row in conn.execute(
            """SELECT instructor_id FROM STUDENT_INSTRUCTOR_PREFERENCES
               WHERE student_id = ? AND preference_type = 'NG'
               ORDER BY preference_id""",
            (student_id,),
        ).fetchall()
    ]
    return preferred, ng


def save_student_instructor_preferences(
    conn, student_id: int, preferred_instructor_ids: list[int], ng_instructor_ids: list[int]
) -> None:
    """生徒1人分の設定を検証後、1トランザクションで置き換える。"""
    preferred = [int(instructor_id) for instructor_id in preferred_instructor_ids]
    ng = [int(instructor_id) for instructor_id in ng_instructor_ids]

    if len(preferred) != len(set(preferred)):
        raise ValueError("同じ講師を推奨講師に複数回登録することはできません")
    if len(ng) != len(set(ng)):
        raise ValueError("同じ講師をNG講師に複数回登録することはできません")
    overlap = set(preferred) & set(ng)
    if overlap:
        names = [
            row[0]
            for row in conn.execute(
                f"SELECT last_name || first_name FROM INSTRUCTORS WHERE instructor_id IN ({','.join('?' * len(overlap))})",
                sorted(overlap),
            ).fetchall()
        ]
        label = "、".join(names) if names else "同じ講師"
        raise ValueError(f"{label}を推奨講師とNG講師の両方に登録することはできません")

    selected_ids = set(preferred) | set(ng)
    if selected_ids:
        found_ids = {
            row[0]
            for row in conn.execute(
                f"SELECT instructor_id FROM INSTRUCTORS WHERE instructor_id IN ({','.join('?' * len(selected_ids))})",
                sorted(selected_ids),
            ).fetchall()
        }
        missing_ids = selected_ids - found_ids
        if missing_ids:
            raise ValueError("存在しない講師が選択されています")

    if conn.execute("SELECT 1 FROM STUDENTS WHERE student_id = ?", (student_id,)).fetchone() is None:
        raise ValueError("選択された生徒が見つかりません")

    with conn:
        conn.execute("DELETE FROM STUDENT_INSTRUCTOR_PREFERENCES WHERE student_id = ?", (student_id,))
        conn.executemany(
            """INSERT INTO STUDENT_INSTRUCTOR_PREFERENCES
                   (student_id, instructor_id, preference_type, priority_rank, created_at)
               VALUES (?, ?, 'PREFERRED', ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))""",
            [(student_id, instructor_id, rank) for rank, instructor_id in enumerate(preferred, start=1)],
        )
        conn.executemany(
            """INSERT INTO STUDENT_INSTRUCTOR_PREFERENCES
                   (student_id, instructor_id, preference_type, priority_rank, created_at)
               VALUES (?, ?, 'NG', NULL, strftime('%Y-%m-%dT%H:%M:%fZ','now'))""",
            [(student_id, instructor_id) for instructor_id in ng],
        )


def _options(instructors: list[tuple[int, str]], selected_id: int | None = None) -> str:
    return '<option value="">未設定</option>' + "".join(
        f'<option value="{instructor_id}"{" selected" if instructor_id == selected_id else ""}>'
        f'{html.escape(name)}</option>'
        for instructor_id, name in instructors
    )


def _preference_rows(
    kind: str, values: list[int], instructors: list[tuple[int, str]], minimum_rows: int = 3
) -> str:
    row_count = max(minimum_rows, len(values))
    rows = []
    for index in range(row_count):
        selected_id = values[index] if index < len(values) else None
        rank = f'<span class="preference-rank">{index + 1}位</span>' if kind == "preferred" else ""
        rows.append(
            f'<div class="preference-row" style="display:flex;gap:8px;align-items:center;margin:7px 0;">'
            f'{rank}<select name="{kind}_instructor_ids" style="flex:1;">'
            f'{_options(instructors, selected_id)}</select>'
            f'<button type="button" onclick="removePreferenceRow(this, \'{kind}\')">削除</button></div>'
        )
    return "".join(rows)


def render(qs: dict, message_html: str = "") -> str:
    student_id = qs.get("student_id", [""])[0]
    conn = get_conn()
    students = conn.execute(
        "SELECT student_id, last_name || ' ' || first_name FROM STUDENTS ORDER BY last_name_kana, first_name_kana"
    ).fetchall()
    instructors = conn.execute(
        """SELECT instructor_id,
                  last_name || ' ' || first_name ||
                  CASE WHEN status = '在籍' THEN '' ELSE '（' || status || '）' END
           FROM INSTRUCTORS ORDER BY status != '在籍', last_name_kana, first_name_kana"""
    ).fetchall()
    preferred: list[int] = []
    ng: list[int] = []
    if student_id:
        preferred, ng = get_student_instructor_preferences(conn, int(student_id))
    conn.close()

    student_options = "".join(
        f'<option value="{sid}"{" selected" if str(sid) == student_id else ""}>{html.escape(name)}</option>'
        for sid, name in students
    )
    instructor_template_options = _options(instructors)
    form_html = ""
    if student_id:
        form_html = f"""
        <form method="POST" action="/student-instructor-preferences">
          <input type="hidden" name="student_id" value="{student_id}">
          <h2 style="font-size:16px;margin-top:24px;">推奨講師</h2>
          <div class="hint">上から順に優先されます。通常は3人まで、必要な場合は「＋」で追加できます。</div>
          <div id="preferred-rows">{_preference_rows('preferred', preferred, instructors)}</div>
          <button type="button" onclick="addPreferenceRow('preferred')">＋ 推奨講師を追加</button>

          <h2 style="font-size:16px;margin-top:28px;">絶対NG講師</h2>
          <div class="hint">ここに登録した講師は、すべての講習会で候補から除外されます。</div>
          <div id="ng-rows">{_preference_rows('ng', ng, instructors)}</div>
          <button type="button" onclick="addPreferenceRow('ng')">＋ NG講師を追加</button>

          <div style="margin-top:26px;"><button type="submit">設定を保存する</button></div>
        </form>
        <template id="preferred-row-template">
          <div class="preference-row" style="display:flex;gap:8px;align-items:center;margin:7px 0;">
            <span class="preference-rank"></span>
            <select name="preferred_instructor_ids" style="flex:1;">{instructor_template_options}</select>
            <button type="button" onclick="removePreferenceRow(this, 'preferred')">削除</button>
          </div>
        </template>
        <template id="ng-row-template">
          <div class="preference-row" style="display:flex;gap:8px;align-items:center;margin:7px 0;">
            <select name="ng_instructor_ids" style="flex:1;">{instructor_template_options}</select>
            <button type="button" onclick="removePreferenceRow(this, 'ng')">削除</button>
          </div>
        </template>
        <script>
          function updatePreferredRanks() {{
            document.querySelectorAll('#preferred-rows .preference-rank').forEach((element, index) => {{
              element.textContent = (index + 1) + '位';
            }});
          }}
          function addPreferenceRow(kind) {{
            const template = document.getElementById(kind + '-row-template');
            document.getElementById(kind + '-rows').appendChild(template.content.cloneNode(true));
            updatePreferredRanks();
          }}
          function removePreferenceRow(button, kind) {{
            button.closest('.preference-row').remove();
            if (kind === 'preferred') updatePreferredRanks();
          }}
        </script>
        """

    return f"""
    <h1>生徒ごとの推奨・NG講師</h1>
    <div class="hint">この設定は特定の講習会に限定されず、今後の講習会でも引き続き使用されます。</div>
    {message_html}
    <label>生徒</label>
    <select onchange="location.href='/student-instructor-preferences?student_id='+this.value">
      <option value="">選択してください</option>{student_options}
    </select>
    {form_html if student_id else '<div class="hint">生徒を選択してください</div>'}
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    student_id_value = fields.get("student_id", [""])[0]
    if not student_id_value:
        raise ValueError("生徒を選択してください")

    try:
        preferred_ids = [int(value) for value in fields.get("preferred_instructor_ids", []) if value]
        ng_ids = [int(value) for value in fields.get("ng_instructor_ids", []) if value]
    except ValueError as exc:
        raise ValueError("講師の指定が不正です") from exc

    student_id = int(student_id_value)
    try:
        save_student_instructor_preferences(conn, student_id, preferred_ids, ng_ids)
    except sqlite3.IntegrityError as exc:
        raise ValueError("同じ講師または同じ推奨順位が重複しています。選択内容を確認してください") from exc

    return '<div class="msg success">推奨講師・NG講師の設定を保存しました</div>', {
        "student_id": [student_id_value]
    }
