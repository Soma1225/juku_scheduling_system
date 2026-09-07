# -*- coding: utf-8 -*-
"""
page_camp_sync_groups.py

講習会ごとに、「同じ日付・同じ限に揃えたい生徒グループ」(兄弟同時受講など)を管理するページ。
スケジューラー側は、同じグループに属する生徒を可能な限り同じ枠に配置しようとする
(ほぼ絶対条件として扱う)。
"""

from db import get_conn
from page_camps import list_camps


def create_sync_group(conn, camp_id, group_name, student_ids: list[int]) -> int:
    if not group_name.strip():
        raise ValueError("グループ名を入力してください")
    if len(student_ids) < 2:
        raise ValueError("グループには2人以上の生徒を選んでください")

    cur = conn.execute(
        "INSERT INTO CAMP_STUDENT_SYNC_GROUPS (camp_id, group_name) VALUES (?, ?)",
        (camp_id, group_name.strip()),
    )
    sync_group_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO CAMP_STUDENT_SYNC_GROUP_MEMBERS (sync_group_id, student_id) VALUES (?, ?)",
        [(sync_group_id, sid) for sid in student_ids],
    )
    conn.commit()
    return sync_group_id


def delete_sync_group(conn, sync_group_id) -> None:
    conn.execute("DELETE FROM CAMP_STUDENT_SYNC_GROUP_MEMBERS WHERE sync_group_id = ?", (sync_group_id,))
    conn.execute("DELETE FROM CAMP_STUDENT_SYNC_GROUPS WHERE sync_group_id = ?", (sync_group_id,))
    conn.commit()


def render(qs: dict, message_html: str = "") -> str:
    camp_id = qs.get("camp_id", [""])[0]
    conn = get_conn()
    camps = list_camps(conn)

    body_html = ""
    if camp_id:
        students = conn.execute(
            "SELECT student_id, last_name || ' ' || first_name FROM STUDENTS ORDER BY last_name_kana, first_name_kana"
        ).fetchall()
        checkboxes = "".join(
            f'<label style="display:block;font-weight:normal;margin-top:4px;">'
            f'<input type="checkbox" name="student_ids" value="{sid}" style="width:auto;display:inline;margin-right:6px;">{name}</label>'
            for sid, name in students
        )

        groups = conn.execute(
            "SELECT sync_group_id, group_name FROM CAMP_STUDENT_SYNC_GROUPS WHERE camp_id = ? ORDER BY sync_group_id",
            (camp_id,),
        ).fetchall()
        groups_html = ""
        for gid, gname in groups:
            members = conn.execute(
                """SELECT s.last_name || s.first_name FROM CAMP_STUDENT_SYNC_GROUP_MEMBERS m
                   JOIN STUDENTS s ON s.student_id = m.student_id
                   WHERE m.sync_group_id = ?""",
                (gid,),
            ).fetchall()
            member_names = "、".join(m[0] for m in members)
            groups_html += f"""
            <tr>
              <td>{gname}</td>
              <td>{member_names}</td>
              <td>
                <form method="POST" action="/camp-sync-groups" onsubmit="return confirm('このグループを削除しますか？');">
                  <input type="hidden" name="action" value="delete">
                  <input type="hidden" name="camp_id" value="{camp_id}">
                  <input type="hidden" name="sync_group_id" value="{gid}">
                  <button class="btn-remove" type="submit" style="margin:0;padding:4px 10px;font-size:12px;width:auto;">削除</button>
                </form>
              </td>
            </tr>
            """
        groups_table = (
            f"<table><tr><th>グループ名</th><th>構成員</th><th></th></tr>{groups_html}</table>"
            if groups else '<div class="hint">まだグループがありません</div>'
        )

        body_html = f"""
        <h1 style="font-size:14px;color:#534AB7;margin-top:26px;">新しいグループを作る</h1>
        <form method="POST" action="/camp-sync-groups">
          <input type="hidden" name="action" value="create">
          <input type="hidden" name="camp_id" value="{camp_id}">
          <label>グループ名 <span class="req">*</span></label>
          <input type="text" name="group_name" required placeholder="例: 田中家兄弟">
          <label>同時受講させたい生徒(2人以上)</label>
          <div style="max-height:200px;overflow-y:auto;border:1px solid #eee;border-radius:6px;padding:10px;margin-top:4px;">
            {checkboxes}
          </div>
          <button type="submit">グループを作成する</button>
        </form>
        <h1 style="font-size:14px;color:#534AB7;margin-top:26px;">この講習会のグループ一覧</h1>
        {groups_table}
        """

    conn.close()

    def options(rows, selected=""):
        return "".join(
            f'<option value="{i}"{" selected" if str(i) == selected else ""}>{name}</option>' for i, name in rows
        )

    return f"""
    <h1>兄弟等 同時受講グループ</h1>
    <div class="hint">同じグループの生徒は、できるだけ同じ日付・同じ限になるようスケジューリングされます(ほぼ絶対条件として扱われます)</div>
    {message_html}
    <label>講習会</label>
    <select onchange="location.href='/camp-sync-groups?camp_id='+this.value">
      <option value="">選択してください</option>{options(camps, camp_id)}
    </select>
    {body_html if camp_id else '<div class="hint">先に講習会を選択してください</div>'}
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    action = get("action")
    camp_id = get("camp_id")

    if action == "create":
        student_ids = [int(v) for v in fields.get("student_ids", [])]
        new_id = create_sync_group(conn, int(camp_id), get("group_name"), student_ids)
        message_html = f'<div class="msg success">グループを作成しました → sync_group_id={new_id}</div>'
    elif action == "delete":
        delete_sync_group(conn, int(get("sync_group_id")))
        message_html = '<div class="msg success">グループを削除しました</div>'
    else:
        raise ValueError(f"不明な action です: {action}")

    return message_html, {"camp_id": [camp_id]}
