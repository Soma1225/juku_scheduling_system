# -*- coding: utf-8 -*-
"""page_instructors.py: 講師(INSTRUCTORS)の登録・管理(担当科目の初期登録も同じ画面で行う)"""

import sqlite3
from db import get_conn

ACADEMIC_YEAR_GROUPS = [
    ("学部生", [(f"B{i}", f"学部{i}年（B{i}）") for i in range(1, 13)]),
    ("大学院博士前期課程（修士）", [(f"M{i}", f"修士{i}年（M{i}）") for i in range(1, 5)]),
    ("大学院博士後期課程（博士）", [(f"D{i}", f"博士{i}年（D{i}）") for i in range(1, 7)]),
]
ACADEMIC_YEAR_CODES = {code for _, items in ACADEMIC_YEAR_GROUPS for code, _ in items}
INSTRUCTOR_STATUSES = ["在籍", "休職", "辞職"]
DEFAULT_PROFICIENCY_AT_REGISTRATION = 1  # (現在は未使用。スライダーの初期値は0固定)

# 学年帯の表示順(DBの文字列アルファベット順だと中学生が小学生より先に来てしまうため、明示的に指定する)
GRADE_BAND_ORDER = ["小学生低学年", "小学生高学年", "中学生", "高校生"]

# subject_group単位ではなく、具体的な科目名(subject_name)単位でチェックさせたい組み合わせ。
# (「理科」「社会」を一括りにすると、実際に何を指導できるかが分からなくなるため)
DETAILED_GROUPS = {
    ("高校生", "理科"), ("高校生", "社会"), ("高校生", "教科フォロー"), ("高校生", "数学"),
    ("中学生", "教科フォロー"),
}

# 科目グループの一般的な表示順(50音順ではなく、国語→数学→英語→理科→社会という馴染みのある順にするため)
SUBJECT_GROUP_ORDER = ["国語", "算数", "数学", "英語", "理科", "社会"]
# 高校生は「情報」を最後にする、という個別ルール
SUBJECT_GROUP_ORDER_HIGH_SCHOOL_EXTRA = ["情報"]

# 小学生には「算国」の個別登録は不要(算数・国語それぞれ担当できる講師に、
# 算国コマも自動的に割り振る想定のため)。チェックリストからは除外する。
EXCLUDED_FROM_CHECKLIST = {("小学生低学年", "算国"), ("小学生高学年", "算国")}

# 理科・社会・教科フォロー(高校生)の、具体的な科目の表示順(50音順ではなく教科書的な順にするため)
DETAILED_SUBJECT_ORDER = {
    "理科": ["物理基礎", "物理", "化学基礎", "化学", "生物基礎", "生物", "地学基礎", "地学"],
    "社会": ["地理", "日本史", "世界史", "政治経済", "倫理"],
    "教科フォロー": ["教科フォロー(文系)", "教科フォロー(理系)"],
    "数学": ["数1A", "数2BC", "数3"],
}


# ---------------------------------------------------------
# DB操作
# ---------------------------------------------------------

def insert_instructor(conn, last_name, first_name, last_name_kana, first_name_kana,
                       academic_year, external_instructor_id=None, status="在籍") -> int:
    if not last_name.strip() or not first_name.strip():
        raise ValueError("姓・名を入力してください")
    if not last_name_kana.strip() or not first_name_kana.strip():
        raise ValueError("ふりがな(姓・名)を入力してください")
    if academic_year not in ACADEMIC_YEAR_CODES:
        raise ValueError("学年を選択してください")
    if status not in INSTRUCTOR_STATUSES:
        raise ValueError(f"不正なステータスです: {status}")

    external_instructor_id = external_instructor_id.strip() if external_instructor_id else None
    from db import get_current_academic_fiscal_year
    current_fy = get_current_academic_fiscal_year()
    try:
        cur = conn.execute(
            "INSERT INTO INSTRUCTORS (last_name, first_name, last_name_kana, first_name_kana, "
            "external_instructor_id, academic_year, status, academic_year_confirmed_fiscal_year) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (last_name.strip(), first_name.strip(), last_name_kana.strip(), first_name_kana.strip(),
             external_instructor_id, academic_year, status, current_fy),
        )
    except sqlite3.IntegrityError:
        raise ValueError(f"講師番号「{external_instructor_id}」は既に他の講師で使われています")
    conn.commit()
    return cur.lastrowid


def update_instructor_status(conn, instructor_id, status) -> None:
    if status not in INSTRUCTOR_STATUSES:
        raise ValueError(f"不正なステータスです: {status}")
    conn.execute("UPDATE INSTRUCTORS SET status = ? WHERE instructor_id = ?", (status, instructor_id))
    conn.commit()


def list_instructors(conn) -> list[tuple[int, str]]:
    """他のページ(担当科目・対応可能時間)からも参照される、共有の一覧取得関数。在籍中の講師のみ返す。"""
    return conn.execute(
        "SELECT instructor_id, last_name || ' ' || first_name FROM INSTRUCTORS "
        "WHERE status = '在籍' ORDER BY last_name_kana, first_name_kana"
    ).fetchall()


# ---------------------------------------------------------
# 画面(GET)
# ---------------------------------------------------------

def _academic_year_options() -> str:
    html = '<option value="">選択してください</option>'
    for group_label, items in ACADEMIC_YEAR_GROUPS:
        options = "".join(f'<option value="{code}">{label}</option>' for code, label in items)
        html += f'<optgroup label="{group_label}">{options}</optgroup>'
    return html


def _status_options(selected="在籍") -> str:
    return "".join(
        f'<option value="{s}"{" selected" if s == selected else ""}>{s}</option>' for s in INSTRUCTOR_STATUSES
    )


def _proficiency_number_line(index: int, key: str) -> str:
    """
    ネイティブのスライダーではなく、実際に「数直線」らしく見えるクリック式の部品にする。
    未選択の位置は目盛り線(縦棒)だけ、選ばれた位置にだけ丸が1つ現れる。
    """
    ticks = "".join(
        f'<div class="numline-tick" data-value="{v}" onclick="selectProficiency(this, {index})">'
        f'<span class="numline-label">{v}</span></div>'
        for v in range(3)
    )
    return f"""
    <input type="hidden" id="prof_input_{index}" name="proficiency_{index}" value="0">
    <input type="hidden" name="subject_key_{index}" value="{key}">
    <div class="numline" id="numline_{index}">
      <div class="numline-track"></div>
      {ticks}
      <div class="numline-dot" id="numline_dot_{index}" style="left:10px;"></div>
    </div>
    """


def _subject_group_sort_key(gb: str, sg: str) -> tuple:
    """
    科目グループの並び順キー。50音順ではなく「国語→数学→英語→理科→社会」という
    馴染みのある順にする。高校生だけ「情報」を最後にする個別ルールがある。
    """
    order_list = SUBJECT_GROUP_ORDER + (SUBJECT_GROUP_ORDER_HIGH_SCHOOL_EXTRA if gb == "高校生" else [])
    if sg in order_list:
        return (0, order_list.index(sg))
    return (1, sg)  # リストに無いものは、末尾に50音順で並べる


def _subject_checklist_html(conn) -> str:
    """
    科目のチェックボックス一覧を、指導形態・学年帯ごとに見出しを付けて組み立てる。
    学年帯は GRADE_BAND_ORDER の順(小さい学年から)、科目は「国語→数学→英語→理科→社会」の
    馴染みのある順に並べる。
    理科/社会/教科フォロー(高校生)は、大きな括りだと何を指導できるか分からなくなるため、
    具体的な科目名で個別にチェックさせる。
    小学生の「算国」は、算数・国語をそれぞれ担当できる講師に自動で割り振る想定のため、
    チェックリストからは除外する。
    """
    groups = conn.execute(
        "SELECT DISTINCT course_category, grade_band, subject_group FROM SUBJECTS"
    ).fetchall()
    groups = [g for g in groups if (g[1], g[2]) not in EXCLUDED_FROM_CHECKLIST]

    def sort_key(row):
        cc, gb, sg = row
        grade_index = GRADE_BAND_ORDER.index(gb) if gb in GRADE_BAND_ORDER else len(GRADE_BAND_ORDER)
        return (cc, grade_index) + _subject_group_sort_key(gb, sg)

    groups.sort(key=sort_key)

    sections: dict[tuple[str, str], list[str]] = {}
    for cc, gb, sg in groups:
        sections.setdefault((cc, gb), []).append(sg)

    index = 0
    html = ""
    for (cc, gb), subject_groups in sections.items():
        items_html = ""
        for sg in subject_groups:
            if (gb, sg) in DETAILED_GROUPS:
                # 具体的な科目名単位で分ける。並び順はDETAILED_SUBJECT_ORDERがあればそれに従う
                subject_rows = conn.execute(
                    "SELECT subject_id, subject_name FROM SUBJECTS "
                    "WHERE course_category = ? AND grade_band = ? AND subject_group = ?",
                    (cc, gb, sg),
                ).fetchall()
                preferred_order = DETAILED_SUBJECT_ORDER.get(sg, [])
                subject_rows.sort(
                    key=lambda r: preferred_order.index(r[1]) if r[1] in preferred_order else len(preferred_order)
                )
                for subject_id, subject_name in subject_rows:
                    # subject_nameが既にグループ名を含んでいる場合(例:「教科フォロー(文系)」)は、
                    # 「グループ名/具体名」にすると重複表示になるため、具体名だけを使う
                    label = subject_name if subject_name.startswith(sg) else f"{sg}/{subject_name}"
                    items_html += f"""
                    <div class="checklist-item">
                      <span class="subject-label">{label}</span>
                      {_proficiency_number_line(index, f"SUBJECT||{subject_id}")}
                    </div>
                    """
                    index += 1
            else:
                items_html += f"""
                <div class="checklist-item">
                  <span class="subject-label">{sg}</span>
                  {_proficiency_number_line(index, f"GROUP||{cc}||{gb}||{sg}")}
                </div>
                """
                index += 1
        html += f'<div class="checklist-section"><div class="checklist-heading">{cc} / {gb}</div>{items_html}</div>'

    return html, index


def _render_confirmation(qs: dict) -> str:
    """
    「登録する」を押した直後、実際にDBへ保存する前に見せる確認画面。
    このタイミングではまだ何もDBに書き込んでいない。
    """
    def get1(key, default=""):
        vals = qs.get(key, [default])
        return vals[0] if vals else default

    conn = get_conn()

    n_subjects = int(get1("n_confirm_items", "0"))
    subject_lines = []
    hidden_subject_inputs = ""
    for i in range(n_subjects):
        key = get1(f"c_subject_key_{i}")
        level = get1(f"c_proficiency_{i}")
        if not key:
            continue
        kind, rest = key.split("||", 1)
        if kind == "SUBJECT":
            subject_id = int(rest)
            row = conn.execute(
                "SELECT subject_group, subject_name FROM SUBJECTS WHERE subject_id = ?", (subject_id,)
            ).fetchone()
            if row:
                subject_group, subject_name = row
                label = subject_name if subject_name.startswith(subject_group) else f"{subject_group}/{subject_name}"
            else:
                label = "(不明な科目)"
        else:  # GROUP
            _cc, _gb, subject_group = rest.split("||")
            label = subject_group
        subject_lines.append(f"<li>{label}（習熟度 {level}）</li>")
        hidden_subject_inputs += (
            f'<input type="hidden" name="subject_key_{len(subject_lines)-1}" value="{key}">'
            f'<input type="hidden" name="proficiency_{len(subject_lines)-1}" value="{level}">'
        )
    conn.close()

    subjects_html = (
        f"<ul>{''.join(subject_lines)}</ul>" if subject_lines else '<div class="hint">担当科目は選択されていません</div>'
    )

    academic_year_label = get1("academic_year")

    return f"""
    <h1>講師 新規登録の確認</h1>
    <div class="hint">以下の内容で登録します。よろしければ「この内容で登録する」を押してください</div>
    <table style="margin-top:16px;">
      <tr><th>姓</th><td>{get1('last_name')}</td></tr>
      <tr><th>名</th><td>{get1('first_name')}</td></tr>
      <tr><th>姓(ふりがな)</th><td>{get1('last_name_kana')}</td></tr>
      <tr><th>名(ふりがな)</th><td>{get1('first_name_kana')}</td></tr>
      <tr><th>学年</th><td>{academic_year_label}</td></tr>
      <tr><th>講師番号</th><td>{get1('external_instructor_id') or '-'}</td></tr>
    </table>
    <h1 style="font-size:14px;color:#534AB7;margin-top:20px;">担当科目</h1>
    {subjects_html}
    <form method="POST" action="/instructors" style="margin-top:20px;">
      <input type="hidden" name="action" value="add">
      <input type="hidden" name="last_name" value="{get1('last_name')}">
      <input type="hidden" name="first_name" value="{get1('first_name')}">
      <input type="hidden" name="last_name_kana" value="{get1('last_name_kana')}">
      <input type="hidden" name="first_name_kana" value="{get1('first_name_kana')}">
      <input type="hidden" name="academic_year" value="{academic_year_label}">
      <input type="hidden" name="external_instructor_id" value="{get1('external_instructor_id')}">
      <input type="hidden" name="n_subject_items" value="{len(subject_lines)}">
      {hidden_subject_inputs}
      <button type="submit">この内容で登録する</button>
    </form>
    <a href="/instructors" style="font-size:12px;color:#888;display:inline-block;margin-top:10px;">やり直す</a>
    """


def render(qs: dict, message_html: str = "") -> str:
    if qs.get("confirm_mode", [""])[0] == "1":
        return _render_confirmation(qs)

    conn = get_conn()
    rows = conn.execute(
        "SELECT instructor_id, last_name, first_name, last_name_kana, first_name_kana, "
        "external_instructor_id, academic_year, status FROM INSTRUCTORS ORDER BY last_name_kana, first_name_kana"
    ).fetchall()
    subject_checklist, n_items = _subject_checklist_html(conn)
    conn.close()

    rows_html = ""
    for r in rows:
        iid, ln, fn, lk, fk, ext_id, ay, status = r
        status_color = {"在籍": "#0F6E56", "休職": "#B8860B", "辞職": "#888"}.get(status, "#333")
        rows_html += f"""
        <tr>
          <td>{ln} {fn}（{lk}{fk}）</td>
          <td>{ay}</td>
          <td><span style="color:{status_color};font-weight:bold;">{status}</span></td>
          <td>{ext_id or '-'}</td>
          <td>
            <form class="row-form" method="POST" action="/instructors">
              <input type="hidden" name="action" value="update_status">
              <input type="hidden" name="instructor_id" value="{iid}">
              <select name="status" style="width:100px;">{_status_options(status)}</select>
              <button type="submit" style="width:auto;padding:6px 12px;font-size:12px;">更新</button>
            </form>
          </td>
          <td><a href="/instructor-subjects?instructor_id={iid}" style="font-size:12px;">担当科目を編集</a></td>
        </tr>
        """

    return f"""
    <h1>講師 新規登録</h1>
    {message_html}
    <form method="POST" action="/instructors">
      <input type="hidden" name="action" value="review">
      <input type="hidden" name="n_subject_items" value="{n_items}">
      <div class="form-2col">
        <div>
          <label>姓 <span class="req">*</span></label>
          <input type="text" name="last_name" required placeholder="例: 田中">
        </div>
        <div>
          <label>名 <span class="req">*</span></label>
          <input type="text" name="first_name" required placeholder="例: 太郎">
        </div>
        <div>
          <label>姓(ふりがな) <span class="req">*</span></label>
          <input type="text" name="last_name_kana" required placeholder="例: たなか">
        </div>
        <div>
          <label>名(ふりがな) <span class="req">*</span></label>
          <input type="text" name="first_name_kana" required placeholder="例: たろう">
        </div>
        <div>
          <label>学年 <span class="req">*</span></label>
          <select name="academic_year" required>{_academic_year_options()}</select>
        </div>
        <div>
          <label>講師番号(任意。会社全体の番号)</label>
          <input type="text" name="external_instructor_id" placeholder="例: E-1234">
        </div>
      </div>

      <h1 style="font-size:14px;color:#534AB7;margin-top:24px;">担当科目(任意。ここで選んだ科目を一括登録します)</h1>
      <div class="hint">数直線の丸をクリックして選んでください(0=担当しない、1〜2=習熟度・自己申告)。後から「担当科目を編集」で個別に見直せます</div>
      <div class="checklist-scroll">{subject_checklist}</div>

      <button type="submit" style="margin-top:20px;">確認画面へ進む</button>
    </form>
    <style>
      .form-2col {{ display:grid; grid-template-columns:1fr 1fr; gap:4px 20px; }}
      .form-2col label {{ margin-top:14px; }}
      .form-2col input, .form-2col select {{ margin-top:4px; }}
      .checklist-scroll {{ max-height:320px; overflow-y:auto; border:1px solid #e5e3dd; border-radius:8px; padding:12px 16px; margin-top:8px; }}
      .checklist-section {{ margin-bottom:14px; }}
      .checklist-heading {{ font-size:12px; font-weight:bold; color:#444; margin-bottom:6px; }}
      .checklist-item {{ display:flex; align-items:center; gap:10px; font-size:13px; margin:14px 0; }}
      .subject-label {{ width:170px; flex-shrink:0; }}
      .numline {{ position:relative; width:170px; height:32px; }}
      .numline-track {{ position:absolute; top:10px; left:10px; right:10px; height:1px; background:#bbb; }}
      .numline-tick {{ position:absolute; top:4px; width:16px; height:13px; margin-left:-8px; cursor:pointer; }}
      .numline-tick::before {{ content:""; position:absolute; left:50%; top:0; width:1px; height:13px;
                                background:#bbb; transform:translateX(-50%); }}
      .numline-tick[data-value="0"] {{ left:10px; }}
      .numline-tick[data-value="1"] {{ left:85px; }}
      .numline-tick[data-value="2"] {{ left:160px; }}
      .numline-dot {{ position:absolute; top:5px; width:11px; height:11px; margin-left:-5.5px; border-radius:50%;
                       background:#1F6E8C; pointer-events:none; transition:left .12s ease; box-shadow:0 0 0 3px rgba(31,110,140,0.15); }}
      .numline-label {{ position:absolute; top:19px; left:50%; transform:translateX(-50%);
                         font-size:10.5px; color:#555; }}
    </style>
    <script>
      // 数直線の目盛りをクリックしたら、丸をその位置へ動かし、隠しフィールドに値を反映する
      function selectProficiency(tickEl, index) {{
        var dot = document.getElementById('numline_dot_' + index);
        dot.style.left = tickEl.style.left || getComputedStyle(tickEl).left;
        document.getElementById('prof_input_' + index).value = tickEl.getAttribute('data-value');
      }}
    </script>
    <h1 style="font-size:14px;margin-top:28px;">登録済みの講師 ({len(rows)}名)</h1>
    <table>
      <tr><th>氏名</th><th>学年</th><th>ステータス</th><th>講師番号</th><th></th><th></th></tr>
      {rows_html}
    </table>
    """


# ---------------------------------------------------------
# 送信処理(POST)
# ---------------------------------------------------------

def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    action = get("action", "add")

    if action == "update_status":
        instructor_id = int(get("instructor_id"))
        update_instructor_status(conn, instructor_id, get("status"))
        message_html = '<div class="msg success">ステータスを更新しました</div>'
        return message_html, {}

    if action == "review":
        # まだ何もDBに保存せず、確認画面を表示するためのqsを組み立てるだけ
        if get("academic_year") not in ACADEMIC_YEAR_CODES:
            raise ValueError("学年を選択してください")
        if not get("last_name").strip() or not get("first_name").strip():
            raise ValueError("姓・名を入力してください")
        if not get("last_name_kana").strip() or not get("first_name_kana").strip():
            raise ValueError("ふりがな(姓・名)を入力してください")

        n_items = int(get("n_subject_items") or 0)
        confirm_qs = {
            "confirm_mode": ["1"],
            "last_name": [get("last_name")], "first_name": [get("first_name")],
            "last_name_kana": [get("last_name_kana")], "first_name_kana": [get("first_name_kana")],
            "academic_year": [get("academic_year")],
            "external_instructor_id": [get("external_instructor_id")],
        }
        n_confirm = 0
        for i in range(n_items):
            proficiency = int(get(f"proficiency_{i}") or 0)
            if proficiency <= 0:
                continue
            subject_key = get(f"subject_key_{i}")
            if not subject_key:
                continue
            confirm_qs[f"c_subject_key_{n_confirm}"] = [subject_key]
            confirm_qs[f"c_proficiency_{n_confirm}"] = [str(proficiency)]
            n_confirm += 1
        confirm_qs["n_confirm_items"] = [str(n_confirm)]
        return "", confirm_qs

    new_id = insert_instructor(
        conn, get("last_name"), get("first_name"), get("last_name_kana"), get("first_name_kana"),
        get("academic_year"), get("external_instructor_id") or None,
    )

    # 担当科目(0=担当しない、1〜2=習熟度)を一括登録する
    from page_instructor_subjects import bulk_assign_subjects  # 循環import回避のため遅延import
    n_items = int(get("n_subject_items") or 0)
    n_subjects_assigned = 0
    for i in range(n_items):
        proficiency = int(get(f"proficiency_{i}") or 0)
        if proficiency <= 0:
            continue  # 0のままなら「担当しない」として何もしない

        subject_key = get(f"subject_key_{i}")
        if not subject_key:
            continue
        kind, rest = subject_key.split("||", 1)
        if kind == "GROUP":
            course_category, grade_band, subject_group = rest.split("||")
            n_subjects_assigned += bulk_assign_subjects(
                conn, new_id, subject_group, proficiency,
                grade_band=grade_band, course_category=course_category,
            )
        elif kind == "SUBJECT":
            subject_id = int(rest)
            conn.execute(
                "INSERT OR IGNORE INTO INSTRUCTOR_SUBJECTS (instructor_id, subject_id, proficiency_level) VALUES (?, ?, ?)",
                (new_id, subject_id, proficiency),
            )
            conn.commit()
            n_subjects_assigned += 1

    subject_note = f"（担当科目 {n_subjects_assigned}件も登録しました）" if n_subjects_assigned else ""
    message_html = (
        f'<div class="msg success">登録しました → instructor_id={new_id} / '
        f'{get("last_name")} {get("first_name")}{subject_note}</div>'
    )
    return message_html, {}
